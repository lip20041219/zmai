"""Workflow 引擎测试。"""

from __future__ import annotations

import pytest

from zmai.workflow.base import StepStatus, Workflow, WorkflowStatus
from zmai.workflow.engine import WorkflowEngine, WorkflowStep


class TestWorkflowStatus:
    def test_enum_values(self):
        assert WorkflowStatus.PENDING.value == "pending"
        assert WorkflowStatus.RUNNING.value == "running"
        assert WorkflowStatus.COMPLETED.value == "completed"
        assert WorkflowStatus.FAILED.value == "failed"
        assert WorkflowStatus.CANCELLED.value == "cancelled"


class TestStepStatus:
    def test_enum_values(self):
        assert StepStatus.SUCCESS.value == "success"
        assert StepStatus.FAILED.value == "failed"
        assert StepStatus.SKIPPED.value == "skipped"


class TestWorkflowEngine:
    def test_linear_execution(self):
        engine = WorkflowEngine()
        steps = [
            WorkflowStep(id="s1", name="step1", handler=lambda ctx: {"result": "a"}),
            WorkflowStep(id="s2", name="step2", handler=lambda ctx: {"result": "b"},
                         input_mapping={"prev": "s1_result"}),
        ]
        r = engine.execute("wf_1", steps, name="test")
        assert r.status == WorkflowStatus.COMPLETED
        assert len(r.steps) == 2
        assert r.steps[0].status == StepStatus.SUCCESS
        assert r.steps[1].status == StepStatus.SUCCESS

    def test_step_failure_triggers_next_on_failure(self):
        engine = WorkflowEngine()

        def fail_handler(ctx):
            raise ValueError("step failed")

        steps = [
            WorkflowStep(id="s1", name="fail_step", handler=fail_handler,
                         next_on_failure="s2", max_retries=0),
            WorkflowStep(id="s2", name="fallback",
                         handler=lambda ctx: {"result": "recovered"}),
        ]
        r = engine.execute("wf_2", steps)
        assert r.status == WorkflowStatus.COMPLETED
        assert r.steps[0].status == StepStatus.FAILED
        assert r.steps[1].status == StepStatus.SUCCESS

    def test_all_steps_fail(self):
        engine = WorkflowEngine()

        def fail(ctx):
            raise ValueError("err")

        steps = [
            WorkflowStep(id="s1", name="fail1", handler=fail, max_retries=0),
        ]
        r = engine.execute("wf_3", steps)
        assert r.status == WorkflowStatus.FAILED
        assert r.steps[0].status == StepStatus.FAILED

    def test_cancel(self):
        engine = WorkflowEngine()
        steps = [
            WorkflowStep(id="s1", name="step", handler=lambda ctx: {"ok": 1}),
        ]
        r = engine.execute("wf_4", steps)
        engine.cancel("wf_4")
        # 已完成的工作流不会被取消
        assert r.status == WorkflowStatus.COMPLETED

    def test_get_progress(self):
        engine = WorkflowEngine()
        steps = [WorkflowStep(id="s1", name="step", handler=lambda ctx: {})]
        engine.execute("wf_5", steps)
        p = engine.get_progress("wf_5")
        assert p is not None
        assert p.workflow_id == "wf_5"

    def test_context_passing(self):
        engine = WorkflowEngine()
        steps = [
            WorkflowStep(id="s1", name="produce",
                         handler=lambda ctx: {"value": "hello"},
                         output_mapping={"global_val": "value"}),
            WorkflowStep(id="s2", name="consume",
                         handler=lambda ctx: {"used": ctx.get("prev", "")},
                         input_mapping={"prev": "global_val"}),
        ]
        r = engine.execute("wf_6", steps, global_context={"start": 1})
        assert r.status == WorkflowStatus.COMPLETED
        assert r.steps[1].output.get("used") == "hello"


class TestWorkflowABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            Workflow()  # type: ignore

    def test_concrete_workflow(self):
        class MyWF(Workflow):
            name = "my_wf"
            description = "test"

            def build(self):
                return [{"id": "s1", "name": "step1", "handler": None}]

        wf = MyWF()
        steps = wf.build()
        assert len(steps) == 1
        assert steps[0]["id"] == "s1"
