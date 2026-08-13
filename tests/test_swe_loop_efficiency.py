"""修复闭环效率回归测试 — 短路径、ReadCache、LoopRecovery。

不使用 mock 伪造行为：failure parser 真实读测试文件、read_file 真实读盘、
agent 用真实工具与脚本化 backend 驱动。

覆盖：
  Case 1: failure parser 从"测试读取 static/js/main.js"推断候选业务文件（短路径根因）。
  Case 2: expected/actual/line 精确提取。
  Case 3: 重复 read 同一未变化文件命中 ReadCache，返回复用提示。
  Case 4: LoopGuard 阻断后注入结构化 [LoopRecovery] 且累计 loop_recovery_count。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterator

import pytest

from zmai.swe.failure import parse_test_failure
from zmai.swe.tools import ReadFileTool, WriteFileTool


# ═══════════════════════════════════════════════════════════════════
# Case 1 + 2 — failure parser 推断候选业务文件与 expected/actual/line
# ═══════════════════════════════════════════════════════════════════

BRACES_TRACEBACK = """\
tests/test_app.py:40: in test_button_works
    assert src.count("{") == src.count("}"), "unbalanced braces in main.js"
E   AssertionError: unbalanced braces in main.js
E   assert 6 == 5
"""

TEST_APP_BRACES = '''\
def test_button_works():
    src = open("static/js/main.js", encoding="utf-8").read()
    assert src.count("{") == src.count("}"), "unbalanced braces in main.js"
'''


class TestFailureParserShortPath:
    def test_infers_candidate_business_file_from_test_source(self, tmp_path: Path):
        """测试读取 static/js/main.js → candidate_files 应含它（而非 test 文件）。"""
        (tmp_path / "test_app.py").write_text(TEST_APP_BRACES, encoding="utf-8")
        issue = parse_test_failure(BRACES_TRACEBACK, project_root=tmp_path)
        assert issue is not None
        assert "static/js/main.js" in issue.candidate_files, \
            f"应推断出业务文件 main.js: {issue.candidate_files}"
        # 业务文件应优先于测试文件本身
        assert not issue.candidate_files or issue.candidate_files[0].endswith("main.js")

    def test_extracts_line_expected_actual(self, tmp_path: Path):
        issue = parse_test_failure(BRACES_TRACEBACK, project_root=tmp_path)
        assert issue is not None
        assert issue.line == 40
        assert issue.expected == "5", issue.expected
        assert issue.actual == "6", issue.actual
        assert issue.test_name == "test_button_works"

    def test_fix_plan_prefers_candidate_file(self, tmp_path: Path):
        from zmai.swe.fix_planner import generate_fix_plan
        (tmp_path / "test_app.py").write_text(TEST_APP_BRACES, encoding="utf-8")
        issue = parse_test_failure(BRACES_TRACEBACK, project_root=tmp_path)
        plan = generate_fix_plan(issue)
        steps = "\n".join(plan.steps)
        assert "main.js" in steps, f"修复计划应指向 main.js: {steps}"


# ═══════════════════════════════════════════════════════════════════
# Case 3 — ReadCache：重复读同一未变化文件 → 复用提示
# ═══════════════════════════════════════════════════════════════════


class TestReadCache:
    def _ctx(self, root: Path):
        from zmai.tool import ToolContext
        return ToolContext(agent_id="a", workspace_path=root,
                           project_path=root, timeout=10)

    def test_repeated_read_hits_cache(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        tool = ReadFileTool()  # 同一实例 = 同一修复上下文
        ctx = self._ctx(tmp_path)
        r1 = tool.execute(ctx, {"path": "app.py"})
        assert r1.success
        r2 = tool.execute(ctx, {"path": "app.py"})
        assert r2.success
        assert "[ReadCache]" in r2.output, f"第二次读取应命中缓存: {r2.output}"
        assert r2.metadata.get("cached") is True

    def test_modified_file_re_reads(self, tmp_path: Path):
        p = tmp_path / "app.py"
        p.write_text("def f():\n    return 1\n", encoding="utf-8")
        tool = ReadFileTool()
        ctx = self._ctx(tmp_path)
        r1 = tool.execute(ctx, {"path": "app.py"})
        assert r1.success
        p.write_text("def f():\n    return 2\n", encoding="utf-8")
        r2 = tool.execute(ctx, {"path": "app.py"})
        # 内容变化 → 重新读盘（不再是缓存命中）
        assert r2.success
        assert "[ReadCache]" not in r2.output
        assert "return 2" in r2.output


# ═══════════════════════════════════════════════════════════════════
# Case 4 — LoopGuard 阻断 → 结构化 [LoopRecovery] + 计数
# ═══════════════════════════════════════════════════════════════════


def _messages_text(ctx) -> str:
    parts = []
    for m in ctx.metadata.get("messages", []):
        if isinstance(m, dict):
            parts.append(m.get("content", "") or "")
        else:
            parts.append(getattr(m, "content", "") or "")
    return " ".join(parts)


class _ReadLoopBackend:
    name = "read_loop"
    calls_seen: list[str] = []

    def invoke(self, request):
        from zmai.gateway.base import BackendResponse, TokenUsage
        from zmai.tool import ToolCall
        self.calls_seen.append("read")
        return BackendResponse(
            content="", tool_calls=[ToolCall(id="r", name="read_file",
                                             params={"path": "app.py"})],
            usage=TokenUsage(1, 1), stop_reason="tool_use")

    def stream(self, request):
        from zmai.gateway.base import BackendEvent
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self):
        from zmai.gateway.base import BackendCapability
        return {BackendCapability.TOOL_USE}


class TestLoopRecovery:
    def test_blocked_injects_loop_recovery_and_counts(self, tmp_path: Path):
        from zmai.agent import AgentContext
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolRegistry

        (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        agent = SWEAgent("lr")
        ctx = AgentContext(
            agent_id="lr",
            task=f"修复 {tmp_path} 的 bug 使测试通过",
            backend=_ReadLoopBackend(),
            tools=ToolRegistry(),
            config={"project_path": str(tmp_path), "timeout": 10,
                    "loop_guard.threshold": 3},
            metadata={},
            max_steps=10,
        )
        asyncio.run(agent.initialize(ctx))
        recovery_seen = False
        for _ in range(8):
            action = asyncio.run(agent.step(ctx))
            if "[LoopRecovery]" in _messages_text(ctx):
                recovery_seen = True
            if action.type in ("complete", "fail"):
                break
        assert recovery_seen, "LoopGuard 阻断后应注入 [LoopRecovery] 结构化消息"
        assert ctx.metadata.get("loop_recovery_count", 0) >= 1, \
            "loop_recovery_count 应累计"
        # 至少应包含"之前无效动作"与"下一步"策略
        joined = _messages_text(ctx)
        assert "不要重复上述任何" in joined or "不要重复" in joined
