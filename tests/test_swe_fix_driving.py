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
