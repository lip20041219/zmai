"""P0-2 — 状态一致性：EvalGuard 不得饿死 backend；green 不得跨修改存活。

真实复现过的两个 P0：

  P0-2A EvalGuard livelock
    eval.require_code_change=true + 初始测试全绿 + 未修改代码
    → pre-step EvalGuard 在调用 backend **之前** return cont
    → 模型永远拿不到决策机会 → ever_modified 永远 False → 自锁到 max_steps
    （实测：status=timeout, backend 只收到 shell_exec, edit 从未执行）
    修复后：EvalGuard 只抑制 COMPLETE，不阻止 agent 获得下一次 backend 决策机会。

  P0-2B stale green
    同一步内 [pytest 全绿, edit] → completion.should_complete() 已因修改变 False，
    但 `or _green_once` 旁路仍判完成（metadata["tests_passed"]=True 与
    completion.tests_passed=False 互相矛盾），独立复跑 1 failed。
    修复后：任何 modification（以及失败 / partial_green）都使 green 计数清零。

测试用真实工具执行（真实 pytest / edit / 真实文件系统），backend 只决定下一步动作。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.test_swe_fix_driving import _ScriptedBackend
from zmai.agent import AgentContext
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolCall, ToolRegistry

# ═══════════════════════════════════════════════════════════════════
# 夹具：初始测试全绿（SWE-bench base repo 形态）
# ═══════════════════════════════════════════════════════════════════

GREEN_BUG = "VALUE = 1\n"
GREEN_TEST = "import bug\n\n\ndef test_value():\n    assert bug.VALUE == 1\n"
TOUCHED = "VALUE = 1  # touched"

EVAL_CFG = {"eval.require_code_change": "true"}


def _write_green_project(tmp_path: Path) -> None:
    (tmp_path / "bug.py").write_text(GREEN_BUG, encoding="utf-8")
    (tmp_path / "test_all.py").write_text(GREEN_TEST, encoding="utf-8")


def _pytest() -> ToolCall:
    return ToolCall(id="pt", name="shell_exec",
                    params={"command": "python -m pytest -q"})


def _touch_edit() -> ToolCall:
    """真实改写 bug.py：工作区产生新内容，语义不变（测试仍通过）。"""
    return ToolCall(id="fix", name="edit",
                    params={"path": "bug.py", "mode": "regex_replace",
                            "old_text": "VALUE = 1", "new_text": TOUCHED})


class _CountingBackend(_ScriptedBackend):
    """统计 backend 被真正咨询的次数（livelock 的判据）。"""

    def __init__(self, script):
        super().__init__(script)
        self.invocations = 0

    def invoke(self, request):
        self.invocations += 1
        return super().invoke(request)


@dataclass
class Step:
    action: str
    test_success_count: int
    tests_passed_meta: Any
    ever_modified: bool
    completion_tests_passed: bool
    completion_tests_complete: bool
    should_complete: bool


def _run_steps(tmp_path: Path, script, max_steps: int = 8,
               extra_config: dict | None = None,
               backend: _CountingBackend | None = None):
    """真实执行 agent；返回 (backend, [Step], app 源码)."""
    _write_green_project(tmp_path)
    backend = backend or _CountingBackend(script)
    ctx = AgentContext(
        agent_id="p0_2_state",
        task="修复 bug 使全部测试通过",
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50, **(extra_config or {})},
        metadata={},
    )
    agent = SWEAgent("p0_2_state")
    asyncio.run(agent.initialize(ctx))

    steps: list[Step] = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        comp = ctx.metadata.get("completion")
        steps.append(Step(
            action=action.type,
            test_success_count=ctx.metadata.get("test_success_count", 0),
            tests_passed_meta=ctx.metadata.get("tests_passed"),
            ever_modified=bool(ctx.metadata.get("ever_modified")),
            completion_tests_passed=bool(comp and comp.tests_passed),
            completion_tests_complete=bool(comp and comp.tests_complete),
            should_complete=bool(comp and comp.should_complete()),
        ))
        if action.type in ("complete", "fail"):
            break
    return backend, steps, (tmp_path / "bug.py").read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# Test A — EvalGuard 不再 livelock：backend 必须能再次拿到决策机会
# ═══════════════════════════════════════════════════════════════════

class TestEvalGuardDoesNotStarveBackend:
    def test_model_can_edit_then_complete_under_eval_guard(self, tmp_path: Path):
        """eval + 初始全绿：模型仍能被咨询 → 真实 edit → 重测通过 → complete。"""
        backend, steps, src = _run_steps(
            tmp_path,
            [[_pytest()], [_touch_edit()], [_pytest()], None],
            max_steps=8, extra_config=EVAL_CFG,
        )
        actions = [s.action for s in steps]
        assert actions[-1] == "complete", f"应在真实修改+重测后完成: {actions}"
        assert "edit" in backend.calls_seen, (
            f"backend 必须再次拿到决策机会（edit 被执行），实际: {backend.calls_seen}")
        assert steps[-1].ever_modified is True
        assert TOUCHED in src, "edit 必须真实落到工作区"
        assert len(actions) < 8, f"不得空转到 max_steps: {actions}"

    def test_original_repro_no_livelock(self, tmp_path: Path):
        """原样复现 P0-2A：全绿之后每一步都必须真正咨询 backend。

        修复前：pre-step EvalGuard 每步 return cont，backend.invocations == 1 而
        steps == 8（模型永远拿不到决策机会，跑到 max_steps 后报 timeout）。
        修复后：invocations == steps —— EvalGuard 只抑制完成，不再饿死 backend。

        注：本用例不断言终止动作。模型始终不产出 tool call 时，会先被 auto-verify
        分支（`_auto_verify` 失败 → cont）接住，那是一条与本 P0 无关的既有路径；
        EvalGuard 自身不再自锁已由 invocations == steps 证明。
        """
        backend = _CountingBackend([[_pytest()]] + [None] * 10)
        _, steps, _ = _run_steps(tmp_path, None, max_steps=6,
                                 extra_config=EVAL_CFG, backend=backend)
        assert backend.invocations == len(steps) >= 3, (
            f"每一步都必须咨询 backend（修复前 invocations=1）: "
            f"invocations={backend.invocations}, steps={len(steps)}")
        assert "complete" not in [s.action for s in steps], "未修改代码不得完成"

    def test_eval_guard_still_blocks_completion_without_modification(self, tmp_path: Path):
        """保留 EvalGuard 语义：未修改代码 → 现有测试全绿也不得 complete。"""
        backend = _CountingBackend([[_pytest()]] + [None] * 10)
        _, steps, _ = _run_steps(tmp_path, None, max_steps=8,
                                 extra_config=EVAL_CFG, backend=backend)
        assert "complete" not in [s.action for s in steps]
        assert steps[0].ever_modified is False


# ═══════════════════════════════════════════════════════════════════
# Test B — green 之后同 step 修改：不得 complete，green 必须失效
# ═══════════════════════════════════════════════════════════════════

class TestStaleGreenInvalidated:
    def test_same_step_green_then_edit_cannot_complete(self, tmp_path: Path):
        """[pytest 全绿, edit] 同一步 → 不得 complete，两个真相源必须一致。"""
        _, steps, src = _run_steps(tmp_path, [[_pytest(), _touch_edit()]], max_steps=1)
        first = steps[0]
        assert first.action != "complete", "全绿之后又被修改，旧 green 已失效"
        assert TOUCHED in src, "edit 必须真实执行（否则测的不是本场景）"
        assert first.test_success_count == 0, "modification 必须清零 green 计数"
        assert first.completion_tests_passed is False
        assert first.completion_tests_complete is False
        assert first.should_complete is False
        assert first.tests_passed_meta is not True, (
            "metadata['tests_passed'] 不得与 completion.tests_passed 矛盾")

    def test_partial_green_clears_previous_full_green(self, tmp_path: Path):
        """非 full_green 的通过（子集）同样使历史 green 失效。

        子集全绿时 record_test_result(passed=True, scope_complete=False) 会置
        tests_passed=True 但 tests_complete=False；若 green 计数仍是 1，
        `or _green_once` 旁路就会在 scope 未覆盖时宣布完成。
        """
        (tmp_path / "extra_test.py").write_text(
            "import bug\n\n\ndef test_extra():\n    assert bug.VALUE == 1\n",
            encoding="utf-8")
        subset = ToolCall(id="sub", name="shell_exec",
                          params={"command": "python -m pytest -q test_all.py"})
        _, steps, _ = _run_steps(tmp_path, [[_pytest()], [subset]], max_steps=3,
                                 extra_config=EVAL_CFG)
        assert steps[0].test_success_count == 1          # 完整套件全绿
        assert steps[1].action != "complete"
        assert steps[1].test_success_count == 0, "子集通过必须清零历史 green 计数"
        assert steps[1].completion_tests_complete is False


# ═══════════════════════════════════════════════════════════════════
# Test C — 修改后重新测试：可以完成，且状态一致
# ═══════════════════════════════════════════════════════════════════

class TestRetestThenComplete:
    def test_complete_after_retest_with_consistent_state(self, tmp_path: Path):
        """[全绿, edit] → [全绿] → complete，且 metadata 与 CompletionState 一致。"""
        _, steps, _ = _run_steps(
            tmp_path, [[_pytest(), _touch_edit()], [_pytest()]], max_steps=4)
        assert [s.action for s in steps] == ["continue", "complete"], \
            f"必须先 continue（旧 green 失效）再 complete: {[s.action for s in steps]}"
        final = steps[-1]
        assert final.should_complete is True
        assert final.completion_tests_passed is True
        assert final.completion_tests_complete is True
        assert final.tests_passed_meta is True, "两个真相源必须一致"
        assert final.test_success_count == 1
