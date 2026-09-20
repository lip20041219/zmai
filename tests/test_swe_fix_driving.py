"""SWE Fix-Driving — 测试失败后 Agent 必须进入修改阶段。

覆盖：
  1. 真实 Flask bug（@app.route 缺失 → 404）端到端修复闭环：
     pytest失败 → 读相关文件 → edit 修改 → pytest通过 → complete
  2. 测试失败后只读不修达到阈值 → 强制注入"必须修改"提示
  3. pytest 失败（exit_code!=0）也会被识别为测试失败（触发修复态）

这些测试用真实文件系统 + 真实工具执行（mock backend 仅决定"下一步做什么"，
工具本身真实读文件、真实跑 pytest、真实 edit）。
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

# ═══════════════════════════════════════════════════════════════════
# 辅助: 搭建一个缺失 @app.route 的 Flask 项目
# ═══════════════════════════════════════════════════════════════════


APP_BUGGY = '''\
from flask import Flask

app = Flask(__name__)


def index():
    return "Hello"


if __name__ == "__main__":
    app.run(debug=True)
'''

APP_FIXED_MARKER = "@app.route('/')"

TEST_APP = '''\
import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_home_returns_200(client):
    rv = client.get("/")
    assert rv.status_code == 200
'''


HELPERS = '''\
"""与 bug 无关的旁支模块：让"读取 N 个不同文件"能真实发生。"""

DEFAULT_TIMEOUT = 30
'''


def _write_flask_project(project_dir: Path) -> None:
    """写入一个缺失 @app.route 的 Flask 项目（测试先失败）。"""
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "app.py").write_text(APP_BUGGY, encoding="utf-8")
    (project_dir / "test_app.py").write_text(TEST_APP, encoding="utf-8")
    # 第三个文件：同一文件重复读取会被 ReadCache 判定为无新信息、不再计入
    # reads_after_fail，因此"攒满读取阈值"必须靠读取不同文件。
    (project_dir / "helpers.py").write_text(HELPERS, encoding="utf-8")


def _read_file(path: str, tid: str = "r") -> ToolCall:
    return ToolCall(id=tid, name="read_file", params={"path": path})


def _diagnostic_reads() -> list[ToolCall]:
    """3 次读取不同文件的诊断读取 → 攒满 fix.read_limit（默认 3）。"""
    return [_read_file("app.py", "r1"), _read_file("test_app.py", "r2"),
            _read_file("helpers.py", "r3")]


class _ScriptedBackend(Backend):
    """按预写脚本依次返回工具调用；无脚本时返回 end_turn。

    工具调用会真实执行（真实读/写/跑 pytest），backend 只决定"下一步做什么"。
    """

    name = "scripted_fix"

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


async def _run_agent(project_dir: Path, backend: Backend, max_steps: int = 12) -> tuple[AgentContext, Any]:  # noqa: E501
    agent = SWEAgent("fix_drive_test")
    registry = ToolRegistry()
    ctx = AgentContext(
        agent_id="fix_drive_test",
        task=(
            f"项目在 {project_dir} 目录下。\n"
            f"任务: test_app.py 中 test_home_returns_200 失败，页面返回 404。"
            f"请读取代码，修复 app.py，使测试通过。"
        ),
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


# ═══════════════════════════════════════════════════════════════════
# 测试 1: Flask bug 端到端修复闭环
# ═══════════════════════════════════════════════════════════════════


class TestFlaskFixEndToEnd:
    def test_missing_route_fixed_and_tests_pass(self, tmp_path: Path):
        """缺失 @app.route → agent 走完 失败→分析→修改→重测→完成。"""
        project = tmp_path / "flask_app"
        _write_flask_project(project)

        # 脚本: pytest(失败) → 读app → 读test → edit加route → pytest(通过) → 结束
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="read_file", params={"path": "test_app.py"})],
            [ToolCall(id="4", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [ToolCall(id="5", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 1) agent 成功完成
        assert action.type == "complete", f"应 complete, 实际 {action.type}: {action.output}"

        # 2) app.py 被真实修改，加上了路由
        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert APP_FIXED_MARKER in app_text, "app.py 应包含 @app.route('/')"

        # 3) 真实 pytest 通过（用与 agent 相同的解释器 sys.executable）
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=str(project), capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"修复后 pytest 应通过: {r.stdout}{r.stderr}"

        # 4) 脚本完整走完：读→修→测 顺序正确
        calls = backend.calls_seen
        assert "edit" in calls, f"应调用 edit 修改文件: {calls}"
        # edit 必须在第二次 pytest 之前
        assert calls.index("edit") < calls.index("shell_exec", calls.index("edit")), "edit 应在重测前"  # noqa: E501


# ═══════════════════════════════════════════════════════════════════
# 测试 2: fix-driving 强制修改
# ═══════════════════════════════════════════════════════════════════


class TestFixDrivingEnforcement:
    def test_read_only_after_fail_triggers_force_modify(self, tmp_path: Path):
        """测试失败后只读不修达阈值 → 注入"必须修改"提示。"""
        project = tmp_path / "flask_app2"
        _write_flask_project(project)

        # 脚本: pytest(失败) → 反复 read_file 不修改（读取不同文件，才有诊断信息量）
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
        ]
        for i in range(6):  # 连续 6 个 step 只读，不修改
            name = ["app.py", "test_app.py", "helpers.py"][i % 3]
            script.append([ToolCall(id=f"r{i}", name="read_file",
                                    params={"path": name})])
        script.append(None)
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 达到阈值后应触发 FixDriving 强制修改（return cont, 提示包含"必须修改"）
        messages = ctx.metadata.get("messages", [])
        texts = [getattr(m, "content", "") or m.get("content", "")
                 if isinstance(m, dict) else getattr(m, "content", "")
                 for m in messages] if messages else []
        joined = " ".join(texts)
        assert "[FixDriving]" in joined, "应注入 FixDriving 强制修改提示"
        assert "edit" in joined or "write_file" in joined, "提示应指向 edit/write_file"

    def test_modification_exits_fix_state(self, tmp_path: Path):
        """修改成功后，test_failed 应清空，不再触发 fix-driving。"""
        project = tmp_path / "flask_app3"
        _write_flask_project(project)

        # 脚本: pytest(失败) → read → edit(修改成功) → read(此时已退出修复态,不再累计)
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [ToolCall(id="4", name="read_file", params={"path": "app.py"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 修改后 read 不应触发 fix-driving
        messages = ctx.metadata.get("messages", [])
        texts = [getattr(m, "content", "") if not isinstance(m, dict) else m.get("content", "")
                 for m in messages] if messages else []
        joined = " ".join(texts)
        assert "[FixDriving]" not in joined, "修改成功后不应再触发 FixDriving"


# ═══════════════════════════════════════════════════════════════════
# 测试 3: 修复计划注入 + 修复阶段状态机
# ═══════════════════════════════════════════════════════════════════


def _messages_text(ctx) -> str:
    """拼接所有消息文本，便于断言。"""
    messages = ctx.metadata.get("messages", [])
    parts = []
    for m in messages:
        if isinstance(m, dict):
            parts.append(m.get("content", "") or "")
        else:
            parts.append(getattr(m, "content", "") or "")
    return " ".join(parts)


class TestRepairPlanAndPhases:
    def test_first_test_failure_injects_repair_plan(self, tmp_path: Path):
        """第一次测试失败 → 注入 [Repair Plan]，提示制定修改方案并指向 edit/write_file。"""
        project = tmp_path / "flask_app4"
        _write_flask_project(project)

        # 脚本: pytest(失败) → read → edit(修复) → pytest(通过) → 结束
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
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        joined = _messages_text(ctx)
        assert action.type == "complete", f"应 complete, 实际 {action.type}"
        assert "[Repair Plan]" in joined, "测试失败后应注入 Repair Plan 提示"
        assert "edit" in joined and "write_file" in joined, "Repair Plan 应指向 edit/write_file"

    def test_repair_phase_reaches_verify_after_fix(self, tmp_path: Path):
        """修复闭环后，repair_phase 状态机应推进并停留在 verify/done。"""
        project = tmp_path / "flask_app5"
        _write_flask_project(project)

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
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 经历 edit(→edit) 与通过 pytest(→verify)；末轮应处于 verify 或 idle
        phase = ctx.metadata.get("repair_phase", "idle")
        assert phase in ("verify", "idle", "done"), \
            f"修复闭环后阶段应为 verify/idle/done, 实际 {phase}"
        # 确保曾进入修改阶段
        assert backend.calls_seen.count("edit") >= 1, "应实际调用 edit 修改"

    def test_repair_plan_injected_once_not_spammed(self, tmp_path: Path):
        """连续多次测试失败只应注入一次 Repair Plan（避免刷屏）。"""
        project = tmp_path / "flask_app6"
        _write_flask_project(project)

        # 脚本: 失败 → read → edit(无效) → 失败 → read → edit(无效) → 失败 → 结束
        script: list[list[ToolCall] | None] = []
        for i in range(3):
            script.append([ToolCall(id=f"t{i}a", name="shell_exec",
                                    params={"command": "python -m pytest -q"})])
            script.append([ToolCall(id=f"t{i}b", name="read_file",
                                    params={"path": "app.py"})])
            script.append([ToolCall(id=f"t{i}c", name="edit",
                                    params={"path": "app.py", "mode": "regex_replace",
                                            "old_text": r"def index\(\):",
                                            "new_text": "@app.route('/')\ndef index():"})])
        script.append(None)
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        joined = _messages_text(ctx)
        assert joined.count("[Repair Plan]") == 1, "Repair Plan 应只注入一次，避免重复刷屏"


# ═══════════════════════════════════════════════════════════════════
# 测试 4: 动态修复状态注入 system prompt（面向真实 LLM）
# ═══════════════════════════════════════════════════════════════════


class _CaptureBackend(_ScriptedBackend):
    """记录每次 backend 收到的 system_prompt，用于验证动态修复指令。"""

    def __init__(self, script: list[list[ToolCall] | None]):
        super().__init__(script)
        self.system_prompts: list[str] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        if request.system_prompt:
            self.system_prompts.append(request.system_prompt)
        return super().invoke(request)


class TestFixStateDirective:
    def test_system_prompt_injects_fix_directive_after_failure(self, tmp_path: Path):
        """测试失败后，下一个 step 的 system prompt 必须包含修复状态指令（逼模型修改）。"""
        project = tmp_path / "flask_app7"
        _write_flask_project(project)

        # step1: pytest 失败 → 持久化 test_failed
        # step2: end_turn —— 此时构建 system prompt，应含"Current Repair State / MUST emit"
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert len(backend.system_prompts) >= 2, "应至少发起 2 次 backend 调用"
        # 第 2 次调用发生在 pytest 失败之后，其 system prompt 应带修复指令
        post_failure = backend.system_prompts[1]
        assert "(dynamic, act on this now)" in post_failure, \
            "失败后 system prompt 应包含动态修复状态块"
        assert "MUST emit" in post_failure and "edit" in post_failure, \
            "修复指令应强制 edit/write_file"

    def test_no_directive_before_failure(self, tmp_path: Path):
        """尚未失败时不应注入修复指令（避免噪音）。"""
        project = tmp_path / "flask_app8"
        _write_flask_project(project)

        # 只 read（无 pytest），repair_phase 保持 idle，不应有修复指令
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="read_file", params={"path": "app.py"})],
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert len(backend.system_prompts) >= 1
        assert "(dynamic, act on this now)" not in backend.system_prompts[0], \
            "未进入修复态前不应注入动态修复指令"

    def test_force_edit_phase_declares_reads_disabled(self, tmp_path: Path):
        """force_edit 置位后，模型必须被告知"读取已被禁用"。

        真实故障（e2e_run_after_fix2，agent_7068）：FixDriving 拦截 read 并把它
        的计数清零（这是刻意的，防止拦截反过来喂数），但动态状态块仍然照读
        reads_after_fail，于是每步都告诉模型 "you have used 0/3 reads since the
        failure"。模型据此认为诊断预算还满着 → 反复 read/shell 被拒 → 8 步零修改
        → Force-edit 预算耗尽。拦截是硬性的，但模型看到的状态不是。
        """
        project = tmp_path / "flask_app12"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            _diagnostic_reads(),            # 攒满阈值 → 本步结束时 force_edit=True
            [_read_file("app.py")],         # force_edit 生效 → 被拒绝
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=4))

        assert ctx.metadata["force_edit"] is True, "本用例必须真的进入强制修改期"

        # 1) 拒绝结果必须让模型知道这是结构性禁用，而不是一次可重试的失败
        rejected = _messages_text(ctx)
        assert "[FixDriving]" in rejected
        assert "DISABLED" in rejected, "被拒的调用必须显式说明读取已被禁用"
        assert "重复调用不会成功" in rejected, \
            "必须说明重复调用不会成功（否则模型只会换个参数重试）"

        # 2) force_edit 生效后的每个 system prompt 都必须声明禁用，
        #    且不得再宣称还剩读取预算（幽灵预算正是空转的燃料）
        armed = backend.system_prompts[2:]
        assert armed, "force_edit 生效后应至少还有一次 backend 调用"
        for prompt in armed:
            assert "(dynamic, act on this now)" in prompt, "强制期仍应有动态状态块"
            assert "DISABLED" in prompt, \
                f"强制期的 system prompt 必须声明读取已禁用:\n{prompt[-600:]}"
            assert "reads since the failure" not in prompt, \
                "读取已被结构性拒绝，不得再向模型宣称还有读取预算"


# ═══════════════════════════════════════════════════════════════════
# 测试 5: 修改后不重测 → 不得错误判定 completed（release-readiness 审计项）
# ═══════════════════════════════════════════════════════════════════


class TestNoFalseCompletionAfterEdit:
    def test_edit_without_retest_does_not_complete(self, tmp_path: Path):
        """edit 后 end_turn（未重测）→ Agent 必须强制重测，不得误判 completed。

        对应审计项：测试失败 → 修改 → 停止（不重测）绝不能 claim 完成。
        """
        project = tmp_path / "flask_app9"
        _write_flask_project(project)

        # 脚本: pytest(失败) → read → edit(修复但【不重测】) → end_turn
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=8))

        # 关键断言：绝不 claim 完成（测试从未全绿）
        assert action.type != "complete", \
            f"测试从未全绿却误判 completed: {action.output}"
        # 且必须注入"强制重测"指令（而不是放行完成）
        assert "全绿运行" in _messages_text(ctx), \
            "应注入 [Workflow] 强制重测提示"
        # 反复拦截而模型始终不推进 → 有界失败（不空转到 max_steps 伪装成 timeout）
        assert action.type == "fail", f"应明确失败: {action.type}"

    def test_edit_then_green_retest_completes(self, tmp_path: Path):
        """对比：edit 后【重测全绿】→ 正常 complete（正向闭环不被破坏）。"""
        project = tmp_path / "flask_app10"
        _write_flask_project(project)

        # 脚本: pytest(失败) → read → edit → pytest(通过) → 完成
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
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        assert action.type == "complete", f"重测全绿应正常完成: {action.type}: {action.output}"


# ═══════════════════════════════════════════════════════════════════
# 测试 6: FixDriving 粘性 —— 失败 pytest 重跑不能逃逸强制修改
# ═══════════════════════════════════════════════════════════════════
#
# 真实故障：执着的 LLM 可反复用 shell_exec 重跑失败的 pytest 来"逃逸"
# （失败重跑会清零 reads_after_fail），导致 agent 无限 read/重跑、
# 永远不到 edit，直到 LoopGuard → 超时。
#
# 逃逸的封堵方式随 RC-3 调整：force_edit 不再拦截 pytest 重跑（完成守卫要求
# "修改后重跑验证"，两条指令必须能同时满足），改为**状态层面**封堵 ——
# 重跑既不解除 force_edit，也不重置读取计数，因此不会重新触发 FixDriving，
# agent 仍被确定性地逼入 edit/write_file，最终修复并全绿完成。


class TestFixDrivingStickyEscape:
    def test_failing_pytest_rerun_cannot_escape_force_edit(self, tmp_path: Path):
        """force_edit 下重跑失败 pytest 允许执行但无法逃逸 → 仍被逼入 edit。"""
        project = tmp_path / "flask_app11"
        _write_flask_project(project)

        # step1 pytest(失败) → step2 连续3次read(触发force_edit)
        # step3 再次shell_exec pytest(必须被拦截，证明无法逃逸)
        # step4 edit(唯一被允许的推进动作) → step5 pytest(全绿) → 完成
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            _diagnostic_reads(),
            [ToolCall(id="5", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            [ToolCall(id="6", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [ToolCall(id="7", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 1) 逃逸闭合（RC-3 语义）：force_edit 下失败 pytest 重跑**允许执行**
        #    （完成守卫要求"修改后重跑验证"，两条指令必须能同时满足），
        #    但它不能成为逃逸口 —— 重跑既不解除 force_edit，也不重置读取计数，
        #    因此 FixDriving 不会被反复喂数，agent 仍被确定性地逼入 edit。
        assert ctx.metadata["swe_stats"]["pytest_calls"] == 3, \
            "force_edit 激活后重跑 pytest 必须真正执行（用于验证）"
        assert ctx.metadata["swe_stats"]["fixdriving_activations"] == 1, \
            "重跑失败 pytest 不得重新触发 FixDriving（否则可无限逃逸）"

        # 2) agent 被逼入 edit：app.py 被真实修改，加上了路由
        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert APP_FIXED_MARKER in app_text, "app.py 应包含 @app.route('/')"

        # 3) 最终完成
        assert action.type == "complete", f"应 complete, 实际 {action.type}: {action.output}"

        # 4) 真实 pytest 全绿
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=str(project), capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"修复后 pytest 应通过: {r.stdout}{r.stderr}"


# ═══════════════════════════════════════════════════════════════════
# 测试 7: P0-1 根因定位链 —— 闩锁生命周期 / 不伪造诊断态 / 失败分析截断
# ═══════════════════════════════════════════════════════════════════


APP_TWO_BUGS = '''\
from flask import Flask

app = Flask(__name__)


def index():
    return "Hello"


def greet():
    return "Hi"
'''

TEST_TWO_BUGS = '''\
import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_home_returns_200(client):
    assert client.get("/").status_code == 200


def test_greeting_returns_200(client):
    assert client.get("/greet").status_code == 200
'''

_PYTEST = ToolCall(id="p", name="shell_exec",
                   params={"command": "python -m pytest -q"})

# 只给 index 补路由（greet 仍然 404）→ 第二次重测必然产生**新的**失败
_FIX_INDEX_ROUTE = ToolCall(
    id="e1", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "def index", "new_text": """@app.route('/')
def index"""})

def _capture_messages(monkeypatch, marker: str) -> list[str]:
    """在注入点截获含 marker 的 user 消息原文。

    不能事后从 ctx.metadata["messages"] 取：上下文预算会把消息压缩成摘要 / 挤出
    recent window，观察到的已不是模型当时收到的内容。
    """
    from zmai.context.manager import ContextManager

    captured: list[str] = []
    original = ContextManager.add_message

    def _spy(self, role, content, metadata=None):
        if role == "user" and content and marker in content:
            captured.append(content)
        return original(self, role, content, metadata)

    monkeypatch.setattr(ContextManager, "add_message", _spy)
    return captured


def _capture_repair_plans(monkeypatch) -> list[str]:
    """在注入点截获 [Repair Plan] 消息原文。"""
    return _capture_messages(monkeypatch, "[Repair Plan]")


class TestRootCauseLatchLifecycle:
    def test_parse_exception_does_not_arm_fix_driving(self, tmp_path: Path, monkeypatch):
        """P0-1 A: 解析异常不得伪造"诊断已完成"，也不得因此进入强制修改阶段。"""
        project = tmp_path / "flask_parse_boom"
        _write_flask_project(project)

        import zmai.swe.failure as failure_mod
        real = failure_mod.parse_test_failure
        seen = {"n": 0}

        def _flaky(text, project_root=None):
            seen["n"] += 1
            if seen["n"] == 1:
                raise RuntimeError("parser exploded")
            return real(text, project_root=project_root)

        monkeypatch.setattr(failure_mod, "parse_test_failure", _flaky)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],            # 失败 → 解析抛异常
            _diagnostic_reads(),  # 攒满 fix.read_limit
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert ctx.metadata["swe_stats"].get("fixdriving_activations", 0) == 0, \
            "诊断未就绪时不得触发 FixDriving 强制修改"
        assert "[FixDriving]" not in _messages_text(ctx), "不得注入强制修改提示"
        assert ctx.metadata.get("repair_plan_injected") is False, \
            "解析异常不得伪造『计划已就绪』状态"

    def test_parse_exception_does_not_burn_latch(self, tmp_path: Path, monkeypatch):
        """P0-1 A: 解析异常后，下一次失败必须能重试解析（闩锁未被烧掉）。"""
        project = tmp_path / "flask_parse_retry"
        _write_flask_project(project)

        import zmai.swe.failure as failure_mod
        real = failure_mod.parse_test_failure
        seen = {"n": 0}

        def _flaky(text, project_root=None):
            seen["n"] += 1
            if seen["n"] == 1:
                raise RuntimeError("parser exploded")
            return real(text, project_root=project_root)

        monkeypatch.setattr(failure_mod, "parse_test_failure", _flaky)

        script: list[list[ToolCall] | None] = [[_PYTEST], [_PYTEST], None]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert seen["n"] >= 2, "下一次失败必须重试解析，不得永久锁死"
        assert ctx.metadata["swe_stats"].get("failure_parser_used", 0) == 1, \
            "只有第二次（成功的）解析应计入失败分析"
        # 闩锁置位即证明 _issue 非空且 _plan_msg（含 file:line/源码上下文）已生成；
        # 消息本身可能被上下文预算压缩，故不断言消息文本（见
        # test_failure_injects_semantic_analysis_and_plan 对文本的覆盖）。
        assert ctx.metadata.get("repair_plan_injected") is True, \
            "重试成功后应真正产出计划"

    def test_second_real_failure_reenters_analysis(self, tmp_path: Path):
        """P0-1 B: 第一次失败修复后的第二次真实失败必须能重新进入失败分析。"""
        project = tmp_path / "flask_two_bugs"
        project.mkdir(parents=True, exist_ok=True)
        (project / "app.py").write_text(APP_TWO_BUGS, encoding="utf-8")
        (project / "test_app.py").write_text(TEST_TWO_BUGS, encoding="utf-8")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],            # 失败 #1（两个测试都失败）
            [_FIX_INDEX_ROUTE],   # 只修 index
            [_PYTEST],            # 失败 #2（greet 仍 404）→ 必须重新分析
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert "@app.route('/')" in app_text, "第一次修改应已生效"
        # failure_parser_used 只在 _issue 非空时 +1：计数为 2 即证明第二次真实失败
        # 重新走完了 parse → plan 全链路，未被首次失败的闩锁阻断。
        assert ctx.metadata["swe_stats"].get("failure_parser_used", 0) == 2, \
            "第二次真实失败必须重新进入 failure analysis，不得被首次闩锁永久阻断"


class TestFailureAnalysisTruncation:
    def test_failure_analysis_keeps_traceback_tail(self, tmp_path: Path, monkeypatch):
        """P0-1 C: 失败分析段必须携带 traceback 尾部/file:line，而非只有 pytest 头部。"""
        project = tmp_path / "flask_noisy"
        _write_flask_project(project)
        # collection 阶段打印大量噪声（-s 使其进入 stdout），把 FAILURES 段挤出输出头部
        (project / "conftest.py").write_text(
            'def pytest_configure(config):\n'
            '    for _ in range(140):\n'
            '        print("[noise] " + "-" * 60)\n',
            encoding="utf-8",
        )
        captured = _capture_repair_plans(monkeypatch)

        script: list[list[ToolCall] | None] = [
            [ToolCall(id="p", name="shell_exec",
                      params={"command": "python -m pytest -q -s"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        asyncio.run(_run_agent(project, backend))

        assert captured, "应注入 [Repair Plan]"
        section = captured[0].split("失败分析：", 1)[1]
        assert len(section) > 1200, \
            f"失败分析应保留尾部（旧实现只留头部 800 字符）: {len(section)}"
        assert "FAILURES" in section, "应含 traceback 尾部（FAILURES 段）"
        assert "test_app.py:" in section, "应含 file:line"


# ═══════════════════════════════════════════════════════════════════
# 测试 8: LoopGuard 升级门控 —— "诊断未成功不得强制进入普通 edit 路径"
# ═══════════════════════════════════════════════════════════════════

# 不存在的文件：read 必然失败且失败签名稳定 → 用于廉价地攒满 LoopGuard 阈值
_MISSING = "no_such_file_xyz.py"


def _missing_reads(n: int, prefix: str) -> list[ToolCall]:
    return [ToolCall(id=f"{prefix}{i}", name="read_file",
                     params={"path": _MISSING}) for i in range(n)]


class TestLoopGuardEscalationGate:
    def test_no_diagnosis_no_edit_cannot_force_edit(self, tmp_path: Path, monkeypatch):
        """A: 解析持续失败且从未修改过代码 → LoopGuard 达标也不得强制 edit。

        必须同时证明"阈值确实达到了"（loop_recovery_count >= recover_limit），
        否则本用例会退化成"路径没走到"的假通过。
        """
        project = tmp_path / "flask_lg_no_target"
        _write_flask_project(project)

        import zmai.swe.failure as failure_mod

        def _boom(*a, **k):
            raise RuntimeError("parser exploded")

        monkeypatch.setattr(failure_mod, "parse_test_failure", _boom)
        loopguard_msgs = _capture_messages(monkeypatch, "[LoopGuard]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],                    # 失败 → 解析抛异常（诊断不成功）
            _missing_reads(5, "a"),       # 5 次相同失败 → LoopGuard 阻断（恢复 1）
            _missing_reads(5, "b"),       # 再 5 次 → 恢复 2 = 达到 recover_limit
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        # 前提：升级阈值确实被触达（否则断言无意义）
        assert ctx.metadata.get("loop_recovery_count", 0) >= 2, \
            "本用例必须真的走到 LoopGuard 升级阈值"
        # 门控生效：不进入强制修改 / 不伪装成 plan 态
        assert ctx.metadata.get("repair_plan_injected") is False
        assert ctx.metadata.get("force_edit") is not True, \
            "诊断未成功且从未修改过代码时不得强制进入普通 edit 路径"
        assert ctx.metadata.get("repair_phase") == "diagnose", \
            f"应保持诊断态, 实际 {ctx.metadata.get('repair_phase')}"
        assert ctx.metadata["swe_stats"].get("fixdriving_activations", 0) == 0
        joined = _messages_text(ctx)
        assert "[FixDriving]" not in joined, "不得注入 FixDriving 强制修改提示"
        # 恢复提示本身仍应注入（LoopGuard 未被删除），但不得宣告"升级/必须改代码"
        assert loopguard_msgs, "LoopGuard 恢复提示应照常注入"
        assert not any("进入修复升级" in m for m in loopguard_msgs), \
            "未升级时不得声称进入修复升级"

    def test_with_diagnosis_and_edit_still_escalates(self, tmp_path: Path, monkeypatch):
        """B: 已有成功诊断 + 已有真实 edit → LoopGuard 升级行为保持原样。"""
        project = tmp_path / "flask_lg_with_target"
        _write_flask_project(project)
        loopguard_msgs = _capture_messages(monkeypatch, "[LoopGuard]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],                                  # 失败 → 诊断成功（闩锁置位）
            [ToolCall(id="e1", name="edit",             # 真实修改 → ever_modified
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": "def index",
                              "new_text": "@app.route('/')\ndef index"})],
            _missing_reads(5, "a"),                     # 恢复 1
            _missing_reads(5, "b"),                     # 恢复 2 → 应升级
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert ctx.metadata.get("repair_plan_injected") is True, "前提：诊断成功"
        assert ctx.metadata.get("ever_modified") is True, "前提：已发生过真实修改"
        assert ctx.metadata.get("loop_recovery_count", 0) >= 2
        assert ctx.metadata.get("force_edit") is True, \
            "有诊断/有修改时 LoopGuard 升级不得被错误封堵"
        assert ctx.metadata.get("repair_phase") == "plan"
        assert any("进入修复升级" in m for m in loopguard_msgs), \
            "升级时应注入升级提示"

    def test_force_edit_budget_still_bounds_escalation(self, tmp_path: Path):
        """C: 升级置位后 MAX_FORCE_EDIT_STEPS 仍然生效，不产生无限强制修改。"""
        project = tmp_path / "flask_lg_budget"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [ToolCall(id="e1", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": "def index",
                              "new_text": "@app.route('/')\ndef index"})],
            _missing_reads(5, "a"),
            _missing_reads(5, "b"),     # 此处升级 force_edit
        ]
        # 升级后只发被拒绝的调用：force_edit 预算应把强制期截断为有界失败
        script += [_missing_reads(1, f"s{i}") for i in range(12)]
        script.append(None)

        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=30))

        assert ctx.metadata.get("force_edit") is True, "前提：确实进入强制修改期"
        assert action.type == "fail", \
            f"强制期内无修改应有界失败, 实际 {action.type}: {action.output}"
        assert "force_edit armed" in (action.error or ""), \
            f"应由 force_edit 预算终止: {action.error}"


# ═══════════════════════════════════════════════════════════════════
# 测试 9: P1-A — auto-verify 只能使用与当前 workspace 相关的证据
# ═══════════════════════════════════════════════════════════════════

_FIX_INDEX = ToolCall(
    id="e1", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "def index", "new_text": """@app.route('/')
def index"""})


class TestAutoVerifyEvidenceScope:
    def test_stale_failure_does_not_block_after_edit(self, tmp_path: Path, monkeypatch):
        """A/B/C/D: 旧 failure → edit → 未重测 → auto-verify 不得再拿旧 failure 判失败。

        观测：auto-verify 通过（无 [Verification Results]），从而走到 completion
        守卫并注入"必须全绿重测"指令 —— 完成仍然被阻断（fail-closed 不变）。
        """
        project = tmp_path / "flask_av_stale"
        _write_flask_project(project)
        verify_msgs = _capture_messages(monkeypatch, "[Verification Results]")
        workflow_msgs = _capture_messages(monkeypatch, "[Workflow]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],        # 失败（edit 之前 = 旧证据）
            [_FIX_INDEX],     # 真实修改工作区
            None,             # 未重测直接 end_turn
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=8))

        assert not verify_msgs, \
            f"edit 前的旧 failure 不得再被当成当前 workspace 的失败: {verify_msgs[:1]}"
        assert any("全绿运行" in m for m in workflow_msgs), \
            "应走到 completion 守卫并要求全绿重测"
        assert action.type != "complete", "未重测不得完成"
        assert ctx.metadata.get("ever_modified") is True

    def test_retest_still_failing_keeps_blocking(self, tmp_path: Path, monkeypatch):
        """E: edit 后重测仍失败 → 必须继续阻断（新失败证据仍参与判定）。"""
        project = tmp_path / "flask_av_retest_fail"
        _write_flask_project(project)
        verify_msgs = _capture_messages(monkeypatch, "[Verification Results]")
        workflow_msgs = _capture_messages(monkeypatch, "[Workflow]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],        # 失败
            [ToolCall(id="e1", name="edit",   # 真实修改了工作区，但没有修好 bug
                      params={"path": "app.py", "mode": "append",
                              "new_text": "# no-op change"})],
            [_PYTEST],        # 改后重测：仍然失败（这就是"当前 workspace 的失败证据"）
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=8))

        assert action.type != "complete", "重测仍失败时不得完成"
        assert verify_msgs, "改后产生的失败证据必须继续参与 auto-verify 判定"
        assert not any("全绿运行" in m for m in workflow_msgs), \
            "重测仍失败时不应注入『去重测』指令（已经在重测）"

    def test_green_retest_still_completes(self, tmp_path: Path):
        """F: edit 后测试通过 → 不得被旧 failure 阻断。"""
        project = tmp_path / "flask_av_green"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [_FIX_INDEX],
            [ToolCall(id="p2", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=8))

        assert action.type == "complete", \
            f"改后全绿应可完成, 实际 {action.type}: {action.error or action.output}"
