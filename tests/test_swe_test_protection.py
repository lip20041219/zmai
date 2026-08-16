"""SWE 测试文件只读保护回归测试 — 杜绝"伪造成功"。

目标：Agent 在真实修复任务中**不得**通过修改测试文件、删除测试、放宽断言、
跳过测试等方式伪造成功。测试文件（tests/、test_*.py、*_test.py、conftest.py）
一律视为只读验收标准。

覆盖：
  1. 工具层：EditTool / WriteFileTool 直接拦截对测试文件的写操作（真实文件）。
  2. 端到端：故意制造业务 Bug → Agent 试图修改测试被拦截 → Agent 修改业务
     代码 → 真实 pytest 全部通过 → 自动完成。测试文件内容保持不变。

不使用 mock 伪造行为：工具真实读写文件，backend 仅决定"下一步做什么"。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

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
from zmai.swe.tools import EditTool, WriteFileTool
from zmai.tool import ToolCall, ToolContext, ToolRegistry

# ═══════════════════════════════════════════════════════════════════
# 夹具：业务 Bug 项目（test_app.py 为严格验收标准）
# ═══════════════════════════════════════════════════════════════════

APP_BUGGY = '''\
from flask import Flask, jsonify

app = Flask(__name__)

users = [{"id": 1, "name": "Alice"}]


@app.route("/api/users")
def get_users():
    return jsonify({"user": users})  # BUG: 字段名应为 "users"


if __name__ == "__main__":
    app.run(debug=True)
'''

# 严格断言，Agent 若想"伪造成功"会试图改动这一行
STRICT_ASSERT = 'assert data["users"][0]["name"] == "Alice"'

TEST_APP = '''\
from app import app


def test_api_returns_correct_json():
    client = app.test_client()
    data = client.get("/api/users").get_json()
    assert "users" in data
    assert data["users"][0]["name"] == "Alice"
'''


def _write_flask_project(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "app.py").write_text(APP_BUGGY, encoding="utf-8")
    (project_dir / "test_app.py").write_text(TEST_APP, encoding="utf-8")


def _pytest() -> ToolCall:
    return ToolCall(id="pytest", name="shell_exec",
                    params={"command": "python -m pytest -q"})


class _ScriptedBackend(Backend):
    name = "scripted_test_protect"

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
            self.calls_seen.extend(c.name for c in calls)
        return BackendResponse(content="", tool_calls=calls,
                               usage=TokenUsage(1, 1),
                               stop_reason="tool_use" if calls else "end_turn")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


async def _run_agent(project_dir: Path, backend: Backend,
                     max_steps: int = 12) -> tuple[AgentContext, object]:
    agent = SWEAgent("test_protect")
    ctx = AgentContext(
        agent_id="test_protect",
        task=(f"项目在 {project_dir} 目录下。修复业务 Bug，使 test_app.py 全部通过。"
              f"不要修改测试文件。"),
        backend=backend,
        tools=ToolRegistry(),
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


def _messages_text(ctx: AgentContext) -> str:
    messages = ctx.metadata.get("messages", [])
    parts = []
    for m in messages:
        if isinstance(m, dict):
            parts.append(m.get("content", "") or "")
        else:
            parts.append(getattr(m, "content", "") or "")
    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════
# 1. 工具层：测试文件写操作被拦截（真实文件）
# ═══════════════════════════════════════════════════════════════════


class TestToolLevelProtection:
    def _ctx(self, root: Path) -> ToolContext:
        return ToolContext(agent_id="t", workspace_path=root,
                           project_path=root, timeout=10)

    def test_edit_test_file_blocked(self, tmp_path: Path):
        (tmp_path / "test_app.py").write_text(TEST_APP, encoding="utf-8")
        r = EditTool().execute(self._ctx(tmp_path), {
            "path": "test_app.py", "mode": "regex_replace",
            "old_text": STRICT_ASSERT, "new_text": "assert True",
        })
        assert not r.success, "编辑测试文件必须被拦截"
        assert "[TestGuard]" in r.error, f"应返回明确原因: {r.error}"
        # 文件内容未被改动（严格断言仍在）
        content = (tmp_path / "test_app.py").read_text(encoding="utf-8")
        assert STRICT_ASSERT in content, "测试断言不能被削弱"

    def test_write_new_file_under_tests_blocked(self, tmp_path: Path):
        (tmp_path / "tests").mkdir(exist_ok=True)
        r = WriteFileTool().execute(self._ctx(tmp_path), {
            "path": "tests/extra_test.py", "content": "def test_fake():\n    pass\n",
        })
        assert not r.success, "新建 tests/ 下的测试文件必须被拦截"
        assert "[TestGuard]" in r.error
        assert not (tmp_path / "tests" / "extra_test.py").exists()

    def test_business_file_still_editable(self, tmp_path: Path):
        (tmp_path / "app.py").write_text(APP_BUGGY, encoding="utf-8")
        r = EditTool().execute(self._ctx(tmp_path), {
            "path": "app.py", "mode": "regex_replace",
            "old_text": r'return jsonify\(\{"user": users\}\)',
            "new_text": r'return jsonify({"users": users})',
        })
        assert r.success, f"业务代码应可正常编辑: {r.error}"
        assert 'return jsonify({"users": users})' in \
            (tmp_path / "app.py").read_text(encoding="utf-8")

    def test_pytest_config_blocked(self, tmp_path: Path):
        (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n",
                                             encoding="utf-8")
        r = EditTool().execute(self._ctx(tmp_path), {
            "path": "pytest.ini", "mode": "regex_replace",
            "old_text": "tests", "new_text": "nope",
        })
        assert not r.success, "修改 pytest.ini 跳过测试收集必须被拦截"

    def test_shell_delete_test_blocked(self, tmp_path: Path):
        """用 shell 删除/移动测试文件必须被拦截（绕过写工具路径）。"""
        from zmai.swe.tools import ShellTool
        for cmd in ("del tests\\test_app.py", "rm -rf tests",
                    "move test_app.py x.py", "rmdir /s /q tests"):
            r = ShellTool().execute(self._ctx(tmp_path), {"command": cmd})
            assert not r.success, f"{cmd} 应被拦截"
            assert "[TestGuard]" in (r.error or ""), f"{cmd}: {r.error}"

    def test_shell_normal_commands_not_blocked(self, tmp_path: Path):
        """正常 shell 命令（dir / pytest / 移动非测试文件）不应触发保护。"""
        from zmai.swe.tools import ShellTool
        for cmd in ("dir", "python -m pytest -q", "move report.txt backup.txt"):
            r = ShellTool().execute(self._ctx(tmp_path),
                                    {"command": cmd, "timeout": 10})
            assert "[TestGuard]" not in (r.error or ""), \
                f"{cmd} 不应触发测试保护: {r.error}"


# ═══════════════════════════════════════════════════════════════════
# 2. 端到端：业务 Bug → 不能改测试 → 改业务代码 → 测试通过
# ═══════════════════════════════════════════════════════════════════


class TestAgentCannotFakeSuccess:
    def test_cannot_modify_test_must_fix_business_code(self, tmp_path: Path):
        """Agent 试图放宽测试断言被拦截 → 只能修业务代码 → 全绿完成。"""
        project = tmp_path / "flask_protect"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [_pytest()],                                        # 1 失败
            [ToolCall(id="w", name="edit",                      # 2 试图放宽断言 → 拦截
                      params={"path": "test_app.py", "mode": "regex_replace",
                              "old_text": STRICT_ASSERT,
                              "new_text": "assert True"})],
            [ToolCall(id="b", name="edit",                      # 3 只能修业务代码
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r'return jsonify\(\{"user": users\}\)',
                              "new_text": r'return jsonify({"users": users})'})],
            [_pytest()],                                        # 4 全绿
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 1) 试图改测试被拦截：上下文里出现 TestGuard 提示
        joined = _messages_text(ctx)
        assert "[TestGuard]" in joined, "试图修改测试文件应被拦截并明确说明原因"

        # 2) 原始测试仍然存在、断言未被削弱
        test_text = (project / "test_app.py").read_text(encoding="utf-8")
        assert "test_api_returns_correct_json" in test_text
        assert STRICT_ASSERT in test_text, "测试断言不能被削弱"

        # 3) 业务代码被正确修改
        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert 'return jsonify({"users": users})' in app_text, "业务代码应被修复"
        assert '{"user": users}' not in app_text

        # 4) 真实 pytest 全部通过
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=str(project), capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"修复后 pytest 应通过: {r.stdout}{r.stderr}"

        # 5) Agent 自动完成
        assert action.type == "complete", f"应 complete, 实际 {action.type}: {action.output}"

        # 6) Agent 确实调用过业务代码 edit
        assert backend.calls_seen.count("edit") == 2, backend.calls_seen


# ═══════════════════════════════════════════════════════════════════
# 3. P0：测试套件收缩不得伪造成功（pyproject 反选 / 通配符删除测试）
# ═══════════════════════════════════════════════════════════════════
#
# 若 Agent 通过修改 pyproject.toml 反选失败测试，或用 shell 通配符删除失败
# 测试文件（保留一个通过测试），pytest 会以 exit 0 + "N passed" 结束，但业务
# 代码并未被真实验证。基线测试数回退防护应拒绝这类"绿色"，不得 complete。


class TestNoFakeCompleteViaSuiteShrinkage:
    def _project(self, root: Path) -> Path:
        proj = root / "flask_shrink"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "app.py").write_text(
            "from flask import Flask, jsonify\n"
            "app = Flask(__name__)\n"
            'users = [{"id": 1, "name": "Alice"}]\n'
            '@app.route("/api/health")\n'
            "def health():\n"
            '    return jsonify({"status": "ok"})\n'
            '@app.route("/api/users")\n'
            "def get_users():\n"
            '    return jsonify({"user": users})  # BUG\n'
            "if __name__ == '__main__':\n"
            "    app.run(debug=True)\n",
            encoding="utf-8")
        (proj / "test_ok.py").write_text(
            "from app import app\n"
            "def test_health_ok():\n"
            "    c = app.test_client()\n"
            "    assert c.get('/api/health').status_code == 200\n",
            encoding="utf-8")
        (proj / "test_broken.py").write_text(
            "from app import app\n"
            "def test_api_returns_correct_json():\n"
            "    c = app.test_client()\n"
            "    data = c.get('/api/users').get_json()\n"
            '    assert "users" in data\n'
            '    assert data["users"][0]["name"] == "Alice"\n',
            encoding="utf-8")
        return proj

    async def _drive(self, proj: Path, script: list[list[ToolCall] | None]):
        backend = _ScriptedBackend(script)
        agent = SWEAgent("shrink")
        ctx = AgentContext(
            agent_id="shrink",
            task=f"项目在 {proj}。修复业务 Bug 使测试全通过。",
            backend=backend,
            tools=ToolRegistry(),
            config={"project_path": str(proj), "timeout": 30},
            metadata={},
        )
        await agent.initialize(ctx)
        action = None
        for _ in range(10):
            action = await agent.step(ctx)
            if action.type in ("complete", "fail"):
                break
        return action.type if action else "none", \
            ctx.metadata.get("test_success_count", 0)

    def test_pyproject_deselect_cannot_complete(self, tmp_path: Path):
        """pyproject.toml 反选失败测试 → 测试数下降 → 不得 complete。"""
        proj = self._project(tmp_path)
        script: list[list[ToolCall] | None] = [
            [_pytest()],   # 1 failed, 1 passed → baseline=2
            [ToolCall(id="a", name="write_file", params={
                "path": "pyproject.toml",
                "content": "[tool.pytest.ini_options]\n"
                           "addopts = "
                           "'--deselect=test_broken.py::test_api_returns_correct_json'\n"})],
            [_pytest()],   # 1 passed, 1 deselected, exit 0 → 被基线防护拒绝
            None,
        ]
        typ, green = asyncio.run(self._drive(proj, script))
        assert typ != "complete", f"反选失败测试后不得 complete: {typ}"
        assert green == 0, f"test_success_count 应为 0, 实际 {green}"

    def test_wildcard_delete_cannot_complete(self, tmp_path: Path):
        """shell 通配符删除失败测试文件 → 测试数下降 → 不得 complete。"""
        proj = self._project(tmp_path)
        script: list[list[ToolCall] | None] = [
            [_pytest()],   # 1 failed, 1 passed → baseline=2
            [ToolCall(id="b", name="shell_exec",
                      params={"command": "del test_b*.py"})],  # 删除失败测试
            [_pytest()],   # 1 passed (只剩 test_ok.py) → 被基线防护拒绝
            None,
        ]
        typ, green = asyncio.run(self._drive(proj, script))
        assert typ != "complete", f"通配符删除测试后不得 complete: {typ}"
        assert green == 0, f"test_success_count 应为 0, 实际 {green}"
