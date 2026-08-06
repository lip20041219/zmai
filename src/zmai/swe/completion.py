"""CompletionState — task completion decision module.

Determines whether the agent should enter the DONE state immediately,
instead of continuing to re-run tests / re-read files / re-verify.

The completion rule mirrors Claude Code / SWE-agent behavior: once the
objective is met and the test suite is green, stop. No more tool calls.

Rules for a fresh green result:
  1. A test command ran with exit code 0 AND the output parsed as passing.
  2. No code modification happened *after* that green result (otherwise the
     result is stale and must be re-validated).
  3. The user's task objective is met (no unresolved failures).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import logging

logger = logging.getLogger("zmai.swe.completion")


@dataclass
class CompletionState:
    """Cumulative, cross-round task completion tracker."""

    # ── True once a test command has exited 0 with a passing parse ──
    tests_passed: bool = False
    tests_exit_code: int | None = None
    last_pass_step: int = 0

    # ── Number of write/edit/git modifications since the last green run ──
    mods_since_pass: int = 0

    # ── Whether the user's task objective is judged achieved ──
    objective_met: bool = False

    # ── Optional log of each test verdict for auditing ──
    history: list[dict] = field(default_factory=list)

    # ── Tool-call budget safety: never mark complete on step 1 without work ──
    steps_done: int = 0

    def record_test_result(self, exit_code: int, passed: bool, step: int) -> None:
        """Record the outcome of a test run.

        A green run (exit 0 + passing parse) becomes the new baseline:
        any modifications that happened earlier are subsumed, so we reset
        ``mods_since_pass`` to 0. A failing run invalidates any previous pass.
        """
        self.steps_done = step
        self.history.append({
            "step": step,
            "exit_code": exit_code,
            "passed": passed,
        })
        if passed and exit_code == 0:
            # 代码修复类任务：测试全绿 + exit 0 即证明任务目标达成。
            self.tests_passed = True
            self.tests_exit_code = exit_code
            self.last_pass_step = step
            self.mods_since_pass = 0
            self.objective_met = True
        elif not passed:
            self.tests_passed = False
            self.tests_exit_code = exit_code

    def record_modification(self, step: int) -> None:
        """Record a code modification.

        Any modification invalidates a previous green result (the tests may
        no longer pass against the changed code), so we clear ``tests_passed``.
        """
        if self.tests_passed:
            self.mods_since_pass += 1
        self.tests_passed = False

    def mark_objective_met(self) -> None:
        """Mark the user task objective as achieved."""
        self.objective_met = True

    def should_complete(self) -> bool:
        """True when the agent should enter DONE immediately.

        Conditions (all must hold):
          1. Task objective is met.
          2. Tests are green (passed, exit code 0).
          3. No modification happened after the green run.
          4. We actually did some work (avoid completing a no-op first step).
        """
        if not self.objective_met:
            return False
        if not self.tests_passed or self.tests_exit_code != 0:
            return False
        if self.mods_since_pass != 0:
            return False
        if self.steps_done <= 0:
            return False
        return True

    def summary(self) -> str:
        return (
            f"tests_passed={self.tests_passed}, "
            f"exit={self.tests_exit_code}, "
            f"mods_after_pass={self.mods_since_pass}, "
            f"objective_met={self.objective_met}, "
            f"last_pass_step={self.last_pass_step}"
        )
