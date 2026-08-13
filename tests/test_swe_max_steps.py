"""SWE 最大执行步数（max_steps）回归测试。

覆盖：
  1. 默认 max_steps == 300。
  2. 用户可通过 CLI/config 设置 100/300/500/1000。
  3. max_steps 仍可阻止无限循环（不删除安全上限）。
  4. 达到 max_steps → completion != success（TIMEOUT）。
  5. 提高 max_steps 后 FixDriving 仍然工作（真实文件 + 真实 pytest）。
  6. TestGuard / Baseline Guard / LoopGuard 不受影响（由各自测试文件覆盖，
     这里用一次完整修复闭环确认修复路径不被 max_steps 打断）。

不使用 mock 伪造行为：工具真实读写、真实跑 pytest；backend 仅决定下一步。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest

from zmai.agent import AgentContext
from zmai.gateway.base import (
    Backend, BackendCapability, BackendEvent,
    BackendRequest, BackendResponse, TokenUsage,
)
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolCall, ToolRegistry


# ═══════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════

def _drive_until_stop(agent: SWEAgent, ctx: AgentContext,
                      max_steps: int) -> tuple[object, object, int]:
    """复刻 runtime.py 的 step 循环 + max_steps 耗尽标记 + finalize。

    返回 (final_action, finalize_result, step_count)。
    """
    action = None
    step_count = 0
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        step_count += 1
        if action.type in ("complete", "fail"):
            break
    if action is not None and action.type not in ("complete", "fail"):
        ctx.metadata["timed_out"] = True
    result = asyncio.run(agent.finalize(ctx))
    return action, result, step_count


class _NeverCompletingBackend(Backend):
    """每次返回一个 read_file 调用，永远不 complete（用于测试 max_steps 终止）。"""
    name = "never_complete"

    def invoke(self, request: BackendRequest) -> BackendResponse:
        return BackendResponse(
            content="",
            tool_calls=[ToolCall(id="r", name="read_file", params={"path": "app.py"})],
            usage=TokenUsage(1, 1), stop_reason="tool_use")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


# ═══════════════════════════════════════════════════════════════════
# 1 & 2. 默认值与可配置性
# ═══════════════════════════════════════════════════════════════════

class TestMaxStepsConfig:
    def test_default_max_steps_is_300(self):
        ctx = AgentContext(agent_id="a", task="t")
        assert ctx.max_steps == 300

    @pytest.mark.parametrize("n", [100, 300, 500, 1000])
    def test_max_steps_configurable(self, n):
        ctx = AgentContext(agent_id="a", task="t", max_steps=n)
        assert ctx.max_steps == n

    @pytest.mark.parametrize("n", [100, 300, 500, 1000])
    def test_cli_flag_parses(self, n):
        from zmai.cli.main import _build_parser
        ns = _build_parser().parse_args(["--max-steps", str(n), "fix the app"])
        assert ns.max_steps == n

    @pytest.mark.parametrize("n", [100, 300, 500, 1000])
    def test_cli_value_flows_to_config(self, n):
        # 模拟 main() 里的接线：--max-steps → config.runtime.max_iterations
        from zmai.cli.main import _build_parser
        from zmai.config import Config
        ns = _build_parser().parse_args(["--max-steps", str(n), "t"])
        cfg = Config()
        cfg.set("runtime.max_iterations", ns.max_steps)
        assert cfg.get("runtime.max_iterations") == n


# ═══════════════════════════════════════════════════════════════════
# 3 & 4. max_steps 仍能阻止无限循环；达到上限 → 非 success
# ═══════════════════════════════════════════════════════════════════

class TestMaxStepsTermination:
    def test_stops_within_max_steps_and_not_success(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("def x():\n    pass\n", encoding="utf-8")
        agent = SWEAgent("never_done")
        ctx = AgentContext(
            agent_id="never_done",
            task="任务永不完成",
            backend=_NeverCompletingBackend(),
            tools=ToolRegistry(),
            config={"project_path": str(tmp_path), "timeout": 10},
            metadata={},
            max_steps=5,
        )
        asyncio.run(agent.initialize(ctx))
        action, result, step_count = _drive_until_stop(agent, ctx, ctx.max_steps)

        # 必须在 max_steps 内停止，绝不无限循环
        assert step_count <= 5, f"应在 max_steps 内停止, 实际 {step_count}"
        # 达到上限 → 非 success（TIMEOUT），绝不误报 COMPLETED
        assert action.type != "complete"
        assert result.status.value in ("TIMEOUT", "timeout"), \
            f"应为 TIMEOUT, 实际 {result.status}"
        assert ctx.metadata.get("timed_out") is True


# ═══════════════════════════════════════════════════════════════════
# 5. 提高 max_steps 后 FixDriving 完整修复闭环仍工作
# ═══════════════════════════════════════════════════════════════════

APP_BUGGY = "from flask import Flask\napp = Flask(__name__)\n\n\ndef index():\n    return \"Hello\"\n"
TEST_APP = (
    "from app import app\n"
    "def test_home():\n"
    "    c = app.test_client()\n"
    "    assert c.get('/').status_code == 200\n"
)


class _ScriptedFix(Backend):
    name = "scripted_fix"

    def __init__(self, script: list[list[ToolCall] | None]):
        self._script = script
        self._idx = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None
        if self._idx < len(self._script):
            calls = self._script[self._idx]
        self._idx += 1
        return BackendResponse(content="", tool_calls=calls,
                               usage=TokenUsage(1, 1),
                               stop_reason="tool_use" if calls else "end_turn")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


class TestFixDrivingAtRaisedMaxSteps:
    def test_full_fix_completes_at_300(self, tmp_path: Path):
        project = tmp_path / "flask"
        project.mkdir()
        (project / "app.py").write_text(APP_BUGGY, encoding="utf-8")
        (project / "test_app.py").write_text(TEST_APP, encoding="utf-8")

        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [ToolCall(id="4", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            None,
        ]
        backend = _ScriptedFix(script)
        agent = SWEAgent("fix300")
        ctx = AgentContext(
            agent_id="fix300",
            task=f"修复 {project} 的 app.py，使测试通过",
            backend=backend,
            tools=ToolRegistry(),
            config={"project_path": str(project), "timeout": 30},
            metadata={},
            max_steps=300,  # 提高后的默认上限下，修复闭环应正常完成
        )
        asyncio.run(agent.initialize(ctx))
        action, result, step_count = _drive_until_stop(agent, ctx, ctx.max_steps)

        # FixDriving / completion 在 max_steps=300 下仍工作 → complete
        assert action.type == "complete", f"应 complete, 实际 {action.type}: {action.output}"
        assert result.status.value in ("COMPLETED", "completed"), result.status
        # 真实 pytest 全绿
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=str(project), capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"pytest 应通过: {r.stdout}{r.stderr}"
        # 使用步数远小于上限（说明并非靠提高上限才勉强完成）
        assert step_count < 300


# ═══════════════════════════════════════════════════════════════════
# 6-8. 反选逃逸在提高 max_steps 后仍被 Baseline Guard 拦截（不伪造成功）
# ═══════════════════════════════════════════════════════════════════

class TestGuardsStillHoldAtRaisedMaxSteps:
    def test_pyproject_deselect_still_cannot_complete(self, tmp_path: Path):
        """max_steps=300 下，pyproject 反选失败测试仍被 Baseline Guard 拦截。"""
        project = tmp_path / "shrink"
        project.mkdir()
        (project / "app.py").write_text(
            "from flask import Flask, jsonify\napp = Flask(__name__)\n"
            'users=[{"id":1,"name":"Alice"}]\n'
            '@app.route("/api/health")\ndef health():\n'
            '    return jsonify({"status":"ok"})\n'
            '@app.route("/api/users")\ndef get_users():\n'
            '    return jsonify({"user": users})\n',
            encoding="utf-8")
        (project / "test_ok.py").write_text(
            "from app import app\n"
            "def test_health():\n"
            "    c=app.test_client()\n"
            "    assert c.get('/api/health').status_code==200\n",
            encoding="utf-8")
        (project / "test_broken.py").write_text(
            "from app import app\n"
            "def test_users():\n"
            "    c=app.test_client()\n"
            '    assert "users" in c.get("/api/users").get_json()\n',
            encoding="utf-8")

        script: list[list[ToolCall] | None] = [
            [ToolCall(id="p1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            [ToolCall(id="w", name="write_file", params={
                "path": "pyproject.toml",
                "content": "[tool.pytest.ini_options]\n"
                           "addopts = '--deselect=test_broken.py::test_users'\n"})],
            [ToolCall(id="p2", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            None,
        ]
        backend = _ScriptedFix(script)
        agent = SWEAgent("shrink300")
        ctx = AgentContext(
            agent_id="shrink300",
            task=f"修复 {project} 的业务代码",
            backend=backend,
            tools=ToolRegistry(),
            config={"project_path": str(project), "timeout": 30},
            metadata={},
            max_steps=300,
        )
        asyncio.run(agent.initialize(ctx))
        action, _result, _steps = _drive_until_stop(agent, ctx, ctx.max_steps)

        assert action.type != "complete", \
            "反选失败测试后不得 complete（Baseline Guard 仍生效）"
        assert ctx.metadata.get("test_success_count", 0) == 0
