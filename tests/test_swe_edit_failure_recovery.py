"""SWE Edit-Failure Recovery — edit/write_file 执行失败后的有界定向恢复。

已确认 P1（pylint-6506 / 5859 / 7228 三个实例稳定复现）：

    diagnosis → repair plan → force_edit → edit failed → 无恢复消费者
    → agent 漂移（跑 pytest / 写草稿文件 / 探索无关路径）→ 0-byte diff → failed

force_edit 只是工具白名单状态，不是 edit-failure 的恢复状态。本文件覆盖新增的
`[EDIT_FAILURE_RECOVERY]` 恢复路径的 7 类行为：触发、正则失败、定向 read、
TestGuard 不被绕过、有界、成功清零、与既有 EDIT_VALIDATION_FAILED 路径共存。

测试用真实文件系统 + 真实工具执行（mock backend 只决定"下一步做什么"，
edit/write_file 真实执行并真实失败）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from zmai.agent import AgentContext
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolCall, ToolRegistry

try:
    from zmai.swe.agent import MAX_EDIT_FAILURE_RECOVERIES
except ImportError:                     # 反向验证时源码改动被 stash，仍要能收集用例
    MAX_EDIT_FAILURE_RECOVERIES = 3

# ═══════════════════════════════════════════════════════════════════
# 辅助: 缺失 @app.route 的 Flask 项目（测试先失败）
# ═══════════════════════════════════════════════════════════════════


APP_BUGGY = '''\
from flask import Flask

app = Flask(__name__)


def index():
    return "Hello"


if __name__ == "__main__":
    app.run(debug=True)
'''

TEST_APP = '''\
from app import app


def test_home_returns_200():
    app.config["TESTING"] = True
    with app.test_client() as c:
        assert c.get("/").status_code == 200
'''

HELPERS = '''\
"""与 bug 无关的旁支模块：让"读取 N 个不同文件"能真实发生。"""

DEFAULT_TIMEOUT = 30
'''


def _write_flask_project(project_dir: Path, *, with_tests_dir: bool = False) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "app.py").write_text(APP_BUGGY, encoding="utf-8")
    (project_dir / "test_app.py").write_text(TEST_APP, encoding="utf-8")
    (project_dir / "helpers.py").write_text(HELPERS, encoding="utf-8")
    if with_tests_dir:
        (project_dir / "tests").mkdir(exist_ok=True)


def _read_file(path: str, tid: str = "r") -> ToolCall:
    return ToolCall(id=tid, name="read_file", params={"path": path})


def _diagnostic_reads() -> list[ToolCall]:
    """3 次读取不同文件 → 攒满 fix.read_limit → 下一步进入 force_edit。"""
    return [_read_file("app.py", "r1"), _read_file("test_app.py", "r2"),
            _read_file("helpers.py", "r3")]


_PYTEST = ToolCall(id="p", name="shell_exec",
                   params={"command": "python -m pytest -q"})

# 空 diff：old_text 与 new_text 相同 → 工具拒绝（EDIT_NO_CHANGE）
_NO_CHANGE_EDIT = ToolCall(
    id="ef", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "def index", "new_text": "def index"})

# 非法正则 → 工具拒绝（regex error: ...）
_BAD_REGEX_EDIT = ToolCall(
    id="er", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "[", "new_text": "@app.route('/')"})

# 真实修复：加上路由（成功修改 → 应清零恢复计数）
_FIX_EDIT = ToolCall(
    id="ok", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "def index", "new_text": """@app.route('/')
def index"""})

# 写出语法错误的 .py → 工具拒绝（EDIT_VALIDATION_FAILED，走既有 [EDIT_REPAIR] 路径）
_BAD_SYNTAX_EDIT = ToolCall(
    id="sv", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "def index", "new_text": "def index(:"})


def _edit_no_change(tid: str) -> ToolCall:
    return ToolCall(id=tid, name="edit", params=dict(_NO_CHANGE_EDIT.params))


class _ScriptedBackend(Backend):
    """按预写脚本依次返回工具调用；无脚本时返回 end_turn。"""

    name = "scripted_edit_recovery"

    def __init__(self, script: list[list[ToolCall] | None]):
        self._script = script
        self._idx = 0
        self.calls_seen: list[str] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None
        if self._idx < len(self._script):
            calls = self._script[self._idx]
        self._idx += 1
        if calls:
            for c in calls:
                self.calls_seen.append(c.name)
        return BackendResponse(
            content="",
            tool_calls=calls,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use" if calls else "end_turn",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


async def _run_agent(project_dir: Path, backend: Backend,
                     max_steps: int = 12) -> tuple[AgentContext, Any]:
    agent = SWEAgent("edit_recovery_test")
    registry = ToolRegistry()
    ctx = AgentContext(
        agent_id="edit_recovery_test",
        task=f"项目在 {project_dir} 目录下。修复 app.py，使测试通过。",
        backend=backend,
        tools=registry,
        config={"project_path": str(project_dir), "timeout": 30},
        metadata={},
    )
    await agent.initialize(ctx)

    action = None
    for _ in range(max_steps):
        action = await agent.step(ctx)
        if action.type in ("complete", "fail"):
            break
    return ctx, action


# ── 观测辅助（在注入点截获，避免被上下文预算压缩后看不到）──


def _capture_messages(monkeypatch, marker: str) -> list[str]:
    """在注入点截获含 marker 的 user 消息原文。"""
    from zmai.context.manager import ContextManager

    captured: list[str] = []
    original = ContextManager.add_message

    def _spy(self, role, content, metadata=None):
        if role == "user" and content and marker in content:
            captured.append(content)
        return original(self, role, content, metadata)

    monkeypatch.setattr(ContextManager, "add_message", _spy)
    return captured


def _capture_tool_results(monkeypatch) -> list[dict[str, Any]]:
    """截获每次工具结果（用于验证"哪次 read 真的执行了 / 哪次被拒绝"）。"""
    from zmai.context.manager import ContextManager

    seen: list[dict[str, Any]] = []
    original = ContextManager.add_tool_result

    def _spy(self, name, success, output, error=None, duration_ms=0, truncate=None):
        seen.append({"name": name, "success": success,
                     "text": (output or "") + (error or "")})
        return original(self, name, success, output, error, duration_ms, truncate)

    monkeypatch.setattr(ContextManager, "add_tool_result", _spy)
    return seen


def _messages_text(ctx: AgentContext) -> str:
    parts = []
    for m in ctx.metadata.get("messages", []) or []:
        parts.append(m.get("content", "") if isinstance(m, dict)
                     else getattr(m, "content", ""))
    return " ".join(p or "" for p in parts)


def _recoveries(ctx: AgentContext) -> int:
    return int(ctx.metadata.get("swe_stats", {}).get("edit_failure_recoveries", 0))


# ═══════════════════════════════════════════════════════════════════
# Test 1 — EDIT_NO_CHANGE 触发定向恢复
# ═══════════════════════════════════════════════════════════════════


class TestEditNoChangeRecovery:
    def test_no_change_edit_failure_triggers_recovery(self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_no_change"
        _write_flask_project(project)
        injected = _capture_messages(monkeypatch, "[EDIT_FAILURE_RECOVERY]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],                    # 失败 → 进入修复态
            _diagnostic_reads(),          # 攒满阈值 → force_edit
            [_NO_CHANGE_EDIT],            # 空 diff → 工具拒绝
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert injected, "edit 执行失败后必须注入 [EDIT_FAILURE_RECOVERY]"
        msg = injected[0]
        assert "target: app.py" in msg, f"恢复提示必须点名目标文件: {msg}"
        assert "[EDIT_NO_CHANGE]" in msg, f"恢复提示必须含失败原因首行: {msg}"
        assert "read target file first" in msg and "retry modification" in msg, \
            f"恢复提示必须规定可执行的下一步: {msg}"
        assert ctx.metadata["edit_failure_recovery_attempts"] == 1
        assert _recoveries(ctx) == 1

    def test_recovery_does_not_clear_force_edit(self, tmp_path: Path, monkeypatch):
        """恢复注入不得解除整段 force_edit（未修改前读取仍被结构性拒绝）。"""
        project = tmp_path / "rec_keep_force"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),
            [_NO_CHANGE_EDIT],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert ctx.metadata["force_edit"] is True, \
            "edit 失败后仍处于强制修改期，不得被恢复注入解除"


# ═══════════════════════════════════════════════════════════════════
# Test 2 — regex error 同样触发（非测试类工具失败也要有恢复靶子）
# ═══════════════════════════════════════════════════════════════════


class TestRegexErrorRecovery:
    def test_bad_regex_edit_failure_triggers_recovery(self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_bad_regex"
        _write_flask_project(project)
        injected = _capture_messages(monkeypatch, "[EDIT_FAILURE_RECOVERY]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),
            [_BAD_REGEX_EDIT],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert injected, "正则错误必须触发恢复"
        msg = injected[0]
        assert "target: app.py" in msg, f"恢复提示必须点名目标文件: {msg}"
        assert "regex error" in msg, f"恢复提示必须含失败原因首行: {msg}"
        assert ctx.metadata["edit_failure_recovery_attempts"] == 1


# ═══════════════════════════════════════════════════════════════════
# Test 3 — 定向 read：只放行目标文件一次，不解除 force_edit
# ═══════════════════════════════════════════════════════════════════


class TestTargetedRead:
    def test_target_read_allowed_once_others_still_blocked(self, tmp_path: Path,
                                                           monkeypatch):
        project = tmp_path / "rec_target_read"
        _write_flask_project(project)
        results = _capture_tool_results(monkeypatch)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),                       # → force_edit
            [_NO_CHANGE_EDIT],                         # 失败 → 放行一次定向 read
            [_read_file("app.py", "t1")],              # 目标文件：放行
            [_read_file("helpers.py", "t2")],          # 非目标：仍被拦截
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        reads = [r for r in results if r["name"] == "read_file"]
        # 脚本里最后两次 read（诊断 3 次之后）依次是：目标文件 app.py、非目标 helpers.py。
        # 判据是"执行 vs 拦截"：目标文件必须**真的执行**（若文件自上次读取未变，
        # ReadFileTool 会回 [ReadCache] 提示——那也是一次成功的执行，不是拦截）。
        target_read, other_read = reads[-2], reads[-1]
        assert target_read["success"] is True, \
            f"恢复期内对目标文件的一次 read 必须被放行: {target_read['text'][:200]}"
        assert other_read["success"] is False and "[FixDriving]" in other_read["text"], \
            "非目标文件的 read 必须仍被 force_edit 拦截"
        assert len([r for r in reads if not r["success"]]) == 1, \
            "定向 read 只放行目标文件 —— 其余读取一律照旧被拒"
        assert "[EDIT_FAILURE_RECOVERY]" in _messages_text(ctx)

        # 额度消耗即失效，且 force_edit 未被解除
        assert ctx.metadata["edit_recovery_read_allowance"] == 0
        assert ctx.metadata["force_edit"] is True

    def test_only_one_read_is_allowed(self, tmp_path: Path, monkeypatch):
        """同一个目标文件读第二次必须被拒（额度只有一次）。"""
        project = tmp_path / "rec_target_read_once"
        _write_flask_project(project)
        results = _capture_tool_results(monkeypatch)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),
            [_NO_CHANGE_EDIT],
            [_read_file("app.py", "t1")],       # 放行
            [_read_file("app.py", "t2")],       # 第二次 → 拒绝
            None,
        ]
        backend = _ScriptedBackend(script)
        asyncio.run(_run_agent(project, backend))

        reads = [r for r in results if r["name"] == "read_file"]
        first, second = reads[-2], reads[-1]
        assert first["success"] is True, "第一次定向 read 必须放行"
        assert second["success"] is False and "[FixDriving]" in second["text"], \
            "额度用尽后再次读取目标文件必须被强制修改期拦截"


# ═══════════════════════════════════════════════════════════════════
# Test 4 — TestGuard 不被绕过；测试文件也不会被当成恢复靶子
# ═══════════════════════════════════════════════════════════════════


class TestTestGuardNotBypassed:
    def test_write_test_file_still_rejected_under_recovery(self, tmp_path: Path,
                                                           monkeypatch):
        project = tmp_path / "rec_testguard"
        _write_flask_project(project, with_tests_dir=True)
        results = _capture_tool_results(monkeypatch)
        injected = _capture_messages(monkeypatch, "[EDIT_FAILURE_RECOVERY]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),
            [_NO_CHANGE_EDIT],                         # 源码目标失败 → target=app.py
            [ToolCall(id="tw", name="write_file",      # 尝试改测试文件
                      params={"path": "tests/test_x.py",
                              "content": "def test_x():\n    assert True\n"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        # 1) TestGuard 仍然拒绝（未修改 TestGuard）
        rejected = [r for r in results
                    if r["name"] == "write_file" and "[TestGuard]" in r["text"]]
        assert rejected and not rejected[0]["success"], \
            f"恢复期内写测试文件必须仍被 TestGuard 拒绝: {rejected}"
        assert not (project / "tests" / "test_x.py").exists(), "测试文件不得被创建"

        # 2) 被拒的测试文件不得成为恢复靶子（否则等于持续把模型推向测试文件）
        assert ctx.metadata.get("edit_failure_target") == "app.py", \
            "TestGuard 拒绝不得把测试文件写成恢复目标"
        # 3) 恢复提示仍然指向源码文件
        assert len(injected) >= 2, "测试文件被拒同样是一次失败的 edit，应有恢复提示"
        assert "target: app.py" in injected[1], \
            f"TestGuard 拒绝后的恢复提示必须仍指向源码文件: {injected[1]}"
        assert "测试文件永远不得修改" in injected[1] or "TestGuard" in injected[1]

    def test_source_target_read_allowed_after_testguard_rejection(
            self, tmp_path: Path, monkeypatch):
        """TestGuard 拒绝后，定向 read 额度必须发给**源码 target**。

        额度判据若落在"这次被拒的文件"（测试文件）上，源码 target 的读取会被一并
        关掉 —— 而恢复提示里仍写着 "read target file first"，成了空头支票。
        """
        project = tmp_path / "rec_testguard_read"
        _write_flask_project(project, with_tests_dir=True)
        results = _capture_tool_results(monkeypatch)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),
            [_NO_CHANGE_EDIT],                         # 源码目标失败 → target=app.py
            [ToolCall(id="tw", name="write_file",      # TestGuard 拒绝
                      params={"path": "tests/test_x.py",
                              "content": "def test_x():\n    assert True\n"})],
            [_read_file("app.py", "t1")],              # 源码 target：必须放行
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        reads = [r for r in results if r["name"] == "read_file"]
        assert reads[-1]["success"] is True, \
            f"TestGuard 拒绝后对源码 target 的定向 read 必须放行: " \
            f"{reads[-1]['text'][:200]}"
        assert ctx.metadata["edit_recovery_read_allowance"] == 0, \
            "额度消耗即失效"
        assert not (project / "tests" / "test_x.py").exists(), "测试文件仍不得被创建"


# ═══════════════════════════════════════════════════════════════════
# Test 5 — 有界：恢复注入次数不超过上限，且最终仍走 fail-closed
# ═══════════════════════════════════════════════════════════════════


class TestBoundedRecovery:
    def test_recovery_injections_are_bounded(self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_bounded"
        _write_flask_project(project)
        injected = _capture_messages(monkeypatch, "[EDIT_FAILURE_RECOVERY]")

        script: list[list[ToolCall] | None] = [[_PYTEST], _diagnostic_reads()]
        for i in range(12):                       # 连续制造 edit 失败
            script.append([_edit_no_change(f"ef{i}")])
        script.append(None)

        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=30))

        # 1) 注入次数有上界，且注入上限就是常量本身
        assert len(injected) == MAX_EDIT_FAILURE_RECOVERIES, \
            f"恢复注入应恰好 {MAX_EDIT_FAILURE_RECOVERIES} 次（有界）: {len(injected)}"
        assert _recoveries(ctx) == MAX_EDIT_FAILURE_RECOVERIES
        # 2) 失败次数照常累计（超过上限后只是不再注入，不是不再计数）
        assert ctx.metadata["edit_failure_recovery_attempts"] > MAX_EDIT_FAILURE_RECOVERIES
        # 3) 不产生无限恢复：最终由既有强制期预算明确失败（fail-closed 不变）
        assert action.type == "fail", f"应收敛为明确失败, 实际 {action.type}: {action.output}"
        assert "force_edit armed" in (action.error or ""), \
            f"应由既有 force_edit 预算终止: {action.error}"


# ═══════════════════════════════════════════════════════════════════
# Test 6 — 成功修改清零：历史失败不污染后续 repair cycle
# ═══════════════════════════════════════════════════════════════════


class TestCounterResetOnSuccess:
    def test_successful_edit_resets_counter(self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_reset"
        _write_flask_project(project)
        injected = _capture_messages(monkeypatch, "[EDIT_FAILURE_RECOVERY]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),
            [_NO_CHANGE_EDIT],        # 失败 #1 → 计数 1
            [_FIX_EDIT],              # 真实修改落地 → 计数清零
            [_NO_CHANGE_EDIT],        # 失败 #2 → 从 0 重新开始 → 1
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert (project / "app.py").read_text(encoding="utf-8").count("@app.route('/')") == 1, \
            "中间那次 edit 必须真实修改了工作区"
        # 若未清零，这里会是 2；从 0 重新开始才是 1
        assert ctx.metadata["edit_failure_recovery_attempts"] == 1, \
            "成功修改后计数必须清零，后续失败从 0 重新计数"
        # 两次失败各自注入一次（第二次是全新 cycle，不因历史失败被压制）
        assert ctx.metadata["swe_stats"]["edit_failures"] == 2
        assert len(injected) == 2, f"清零后新 cycle 必须重新获得恢复机会: {len(injected)}"


# ═══════════════════════════════════════════════════════════════════
# Test 7 — 兼容：EDIT_VALIDATION_FAILED 的 [EDIT_REPAIR] 路径不被抢走
# ═══════════════════════════════════════════════════════════════════


class TestValidationFailedPathPreserved:
    def test_edit_repair_path_still_works_and_is_not_double_consumed(
            self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_validation"
        _write_flask_project(project)
        repair = _capture_messages(monkeypatch, "[EDIT_REPAIR]")
        recovery = _capture_messages(monkeypatch, "[EDIT_FAILURE_RECOVERY]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [_BAD_SYNTAX_EDIT],      # 语法错误 #1 → edit_repair_attempts=1
            [_BAD_SYNTAX_EDIT],      # 语法错误 #2 → 达上限 → [EDIT_REPAIR]
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        # 既有路径照常工作
        assert repair, "EDIT_VALIDATION_FAILED 的 [EDIT_REPAIR] 路径必须保持可用"
        assert "write_file" in repair[0] and "read_file" in repair[0]
        assert ctx.metadata["edit_repair_attempts"] == 2
        # 新 recovery 不得重复消费同一条失败
        assert not recovery, f"语法错误已有专用路径，不得被新 recovery 重复消费: {recovery}"
        assert ctx.metadata.get("edit_failure_recovery_attempts", 0) == 0
