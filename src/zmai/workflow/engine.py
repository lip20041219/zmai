"""WorkflowEngine — linear/conditional branch workflow execution engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from zmai.workflow.base import StepStatus, WorkflowStatus, _now

logger = logging.getLogger("zmai.workflow.engine")


@dataclass
class WorkflowStep:
    id: str
    name: str
    handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    input_mapping: dict[str, str] = field(default_factory=dict)
    output_mapping: dict[str, str] = field(default_factory=dict)
    next_on_success: str | None = None
    next_on_failure: str | None = None
    max_retries: int = 0
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    step_id: str
    name: str
    status: StepStatus = StepStatus.PENDING
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    retries: int = 0
    started_at: str = ""
    completed_at: str | None = None


@dataclass
class WorkflowResult:
    workflow_id: str
    name: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    steps: list[StepResult] = field(default_factory=list)
    started_at: str = ""
    completed_at: str | None = None
    error: str | None = None


class WorkflowEngine:
    """工作流执行引擎。支持线性。"""

    def __init__(self) -> None:
        self._results: dict[str, WorkflowResult] = {}

    def execute(
        self,
        workflow_id: str,
        steps: list[WorkflowStep],
        global_context: dict[str, Any] | None = None,
        name: str = "",
    ) -> WorkflowResult:
        ctx = dict(global_context or {})
        result = WorkflowResult(
            workflow_id=workflow_id,
            name=name,
            status=WorkflowStatus.RUNNING,
            started_at=_now(),
        )
        self._results[workflow_id] = result

        step_map = {s.id: s for s in steps}
        # 默认顺序链：没有显式 next_on_success 时走顺序
        default_next = {steps[i].id: steps[i + 1].id for i in range(len(steps) - 1)}
        step_results: list[StepResult] = []
        current_id = steps[0].id if steps else None

        while current_id:
            step = step_map.get(current_id)
            if not step:
                break

            sr = StepResult(step_id=step.id, name=step.name, status=StepStatus.RUNNING, started_at=_now())
            step_results.append(sr)

            for attempt in range(step.max_retries + 1):
                try:
                    # apply input_mapping: from ctx to handler input
                    handler_input = {}
                    for k, v in step.input_mapping.items():
                        handler_input[k] = ctx.get(v, "")

                    if step.handler:
                        output = step.handler(handler_input)
                    else:
                        output = {}

                    # apply output_mapping
                    for k, v in step.output_mapping.items():
                        ctx[k] = output.get(v, "")

                    sr.status = StepStatus.SUCCESS
                    sr.output = output
                    sr.completed_at = _now()
                    current_id = step.next_on_success or default_next.get(step.id)
                    break
                except Exception as e:
                    logger.warning("Step %s 失败 (attempt %d/%d): %s", step.id, attempt + 1, step.max_retries + 1, e)
                    sr.retries = attempt
                    if attempt >= step.max_retries:
                        sr.status = StepStatus.FAILED
                        sr.error = str(e)
                        sr.completed_at = _now()
                        current_id = step.next_on_failure
                    else:
                        continue

        result.steps = step_results
        last_failed = step_results and step_results[-1].status == StepStatus.FAILED
        result.status = WorkflowStatus.COMPLETED if not last_failed else WorkflowStatus.FAILED
        result.completed_at = _now()
        if last_failed:
            failed = [s for s in step_results if s.status == StepStatus.FAILED]
            result.error = failed[0].error if failed else "未知错误"

        self._results[workflow_id] = result
        return result

    def get_progress(self, workflow_id: str) -> WorkflowResult | None:
        return self._results.get(workflow_id)

    def cancel(self, workflow_id: str) -> None:
        r = self._results.get(workflow_id)
        if r and r.status == WorkflowStatus.RUNNING:
            r.status = WorkflowStatus.CANCELLED
            r.completed_at = _now()
