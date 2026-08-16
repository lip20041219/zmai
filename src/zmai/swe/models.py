"""Plan data model — structured task execution plan."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PlanStep:
    """A single execution step in a Plan.

    Attributes:
        id: Step number (starting from 1).
        action: Human-readable description of the action.
        tool: Expected tool name (optional, None means no tool needed).
        params: Expected tool parameters (optional placeholder).
        expected_outcome: Expected result after completing this step.
        verification_strategy: How to verify this step's success.
    """

    id: int
    action: str
    tool: str | None = None
    params: dict[str, Any] | None = None
    expected_outcome: str = ""
    verification_strategy: str = ""
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanStep:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Plan:
    """Complete execution plan.

    Attributes:
        goal: One-sentence task objective.
        assumptions: Prerequisites about the project (derived from analysis).
        steps: List of execution steps (at least 1).
        files_to_modify: Files planned for modification.
        expected_outcome: Expected result after plan completion.
        verification_strategy: Overall verification strategy.
        estimated_complexity: Estimated complexity (simple / medium / complex).
        risks: Known risk points.
        replan_count: Number of replanning attempts (initial 0, capped at MAX_REPLANS).
        created_at: Plan creation timestamp.
    """

    goal: str
    steps: list[PlanStep]
    assumptions: list[str] = field(default_factory=list)
    files_to_modify: list[str] = field(default_factory=list)
    expected_outcome: str = ""
    verification_strategy: str = ""
    estimated_complexity: str = "medium"
    risks: list[str] = field(default_factory=list)
    replan_count: int = 0
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()

    @property
    def completed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == "completed")

    @property
    def failed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == "failed")

    @property
    def is_finished(self) -> bool:
        """Are all steps finished (including failed and skipped)?"""
        return all(s.status in ("completed", "failed", "skipped") for s in self.steps)

    @property
    def current_step(self) -> PlanStep | None:
        """Get the first uncompleted step."""
        for s in self.steps:
            if s.status == "pending":
                return s
        return None

    def mark_step(self, step_id: int, status: str) -> None:
        """Mark a specific step's status."""
        for s in self.steps:
            if s.id == step_id:
                s.status = status
                return

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "assumptions": self.assumptions,
            "steps": [s.to_dict() for s in self.steps],
            "files_to_modify": self.files_to_modify,
            "expected_outcome": self.expected_outcome,
            "verification_strategy": self.verification_strategy,
            "estimated_complexity": self.estimated_complexity,
            "risks": self.risks,
            "replan_count": self.replan_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        steps = [PlanStep.from_dict(s) for s in d.get("steps", [])]
        return cls(
            goal=d.get("goal", ""),
            steps=steps,
            assumptions=d.get("assumptions", []),
            files_to_modify=d.get("files_to_modify", []),
            expected_outcome=d.get("expected_outcome", ""),
            verification_strategy=d.get("verification_strategy", ""),
            estimated_complexity=d.get("estimated_complexity", "medium"),
            risks=d.get("risks", []),
            replan_count=d.get("replan_count", 0),
            created_at=d.get("created_at", ""),
        )


# ── Constants ────────────────────────────────────────────────

MAX_REPLANS = 3
"""Maximum number of replanning attempts. Beyond this, the Agent enters FAILED state."""


# ── Validation ───────────────────────────────────────────────


def validate_plan_dict(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate whether raw JSON constitutes a valid Plan.

    Returns:
        (valid: bool, reason: str)
    """
    if not isinstance(data, dict):
        return False, "Plan must be a JSON object"

    goal = data.get("goal", "")
    if not goal or not isinstance(goal, str):
        return False, "Plan missing 'goal' or goal is not a string"

    steps = data.get("steps", [])
    if not steps or not isinstance(steps, list):
        return False, "Plan missing 'steps' or steps is not an array"

    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            return False, f"Steps[{i}] is not an object"
        if "id" not in s:
            return False, f"Steps[{i}] missing 'id'"
        action = s.get("action", "")
        if not action or not isinstance(action, str):
            return False, f"Steps[{i}] missing 'action' or action is not a string"

    return True, ""


def format_plan_summary(plan: Plan) -> str:
    """Format as readable plan summary (for injection into system prompt)."""
    lines = [
        "## Execution Plan",
        f"Objective: {plan.goal}",
        f"Complexity: {plan.estimated_complexity}",
    ]
    if plan.assumptions:
        lines.append("Assumptions:")
        for a in plan.assumptions:
            lines.append(f"  - {a}")
    if plan.files_to_modify:
        lines.append(f"Files: {', '.join(plan.files_to_modify)}")
    if plan.expected_outcome:
        lines.append(f"Expected: {plan.expected_outcome[:120]}")
    if plan.verification_strategy:
        lines.append(f"Verification: {plan.verification_strategy[:120]}")
    lines.append("Steps:")
    for s in plan.steps:
        status_mark = {
            "pending": "⏳",
            "running": "→",
            "completed": "✅",
            "failed": "❌",
            "skipped": "⏭",
        }.get(s.status, "⏳")
        tool_str = f" [{s.tool}]" if s.tool else ""
        lines.append(f"  {status_mark} {s.id}. {s.action}{tool_str}")
        if s.expected_outcome:
            lines.append(f"     Expect: {s.expected_outcome[:100]}")
        if s.verification_strategy:
            lines.append(f"     Verify: {s.verification_strategy[:100]}")

    if plan.risks:
        lines.append("Risks:")
        for r in plan.risks:
            lines.append(f"  - {r}")

    return "\n".join(lines)
