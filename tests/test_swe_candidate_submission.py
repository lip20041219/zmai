"""Candidate submission is an exit with evidence, never a success verdict."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from zmai.agent import AgentContext, AgentState
from zmai.config.config import Config
from zmai.gateway.base import (Backend, BackendCapability, BackendEvent,
                               BackendRequest, BackendResponse, TokenUsage)
from zmai.runtime import Runtime
from zmai.swe.agent import SWEAgent, _git_workspace_fingerprint
from zmai.swe.tools import ShellTool
from zmai.tool import ToolCall, ToolRegistry


def call(name: str, **params) -> ToolCall:
    return ToolCall(id=name, name=name, params=params)


class ScriptedBackend(Backend):
    name = "scripted_candidate"
    script: list[ToolCall | str] = []
    seen: list[str] = []
    last_request: BackendRequest | None = None
    requests: list[BackendRequest] = []

    def __init__(self, config=None):
        self.index = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.__class__.last_request = request
        self.__class__.requests.append(request)
        action = self.script[self.index]
        self.index += 1
        self.seen.append(action.name if isinstance(action, ToolCall) else action)
        return BackendResponse(
            content=action if isinstance(action, str) else "",
            tool_calls=None if isinstance(action, str) else [action],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            stop_reason="end_turn" if isinstance(action, str) else "tool_use",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def project(root: Path, *, extra_failure: bool = False) -> None:
    (root / "app.py").write_text("FIXED = False\n", encoding="utf-8")
    (root / "test_target.py").write_text(
        "import app\ndef test_target():\n    assert app.FIXED\n", encoding="utf-8")
    if extra_failure:
        (root / "test_extra.py").write_text(
            "def test_extra():\n    assert False\n", encoding="utf-8")


def script(*, broad: bool = False) -> list[ToolCall | str]:
    actions: list[ToolCall | str] = [
        call("shell_exec", command="python -m pytest test_target.py -q"),
        call("read_file", path="app.py"),
        call("edit", path="app.py", mode="regex_replace",
             old_text="FIXED = False", new_text="FIXED = True"),
        call("shell_exec", command="python -m pytest test_target.py -q"),
    ]
    if broad:
        actions.append(call("shell_exec", command="python -m pytest -q"))
    return actions + ["SUBMIT_CANDIDATE"]


def test_runtime_submits_candidate_after_target_green_and_unknown_broad_failure(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep
                       + os.environ.get("PATH", ""))
    source = tmp_path / "source"
    source.mkdir()
    project(source, extra_failure=True)
    # Keep delivery-report Git discovery inside this small fixture project.
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    ScriptedBackend.script = script(broad=True)
    ScriptedBackend.seen = []
    cfg = Config(sources=[])
    cfg.set("runtime.max_iterations", 8)
    cfg.set("workspace.root", str(tmp_path / "runtime_workspace"))
    runtime = Runtime(config=cfg)
    runtime._gateway.register(ScriptedBackend.name, ScriptedBackend)

    result = asyncio.run(runtime.run(
        agent_id="candidate_probe", task="Fix the failing public test",
        backend=ScriptedBackend.name, config={"project_path": str(source)},
    ))

    assert result["status"] == "candidate", (result, ScriptedBackend.seen)
    assert runtime._lifecycle.get_state("candidate_probe") == "candidate"
    assert result["metadata"]["candidate_verification"] == "incomplete"
    assert result["metadata"]["candidate_test_evidence"]["tests"] == 1
    assert result["metadata"]["test_baseline_comparison"]["status"] == "unknown"
    assert (source / "app.py").read_text(encoding="utf-8") == "FIXED = True\n"
    assert ScriptedBackend.seen == ["shell_exec", "read_file", "edit", "shell_exec",
                                    "shell_exec", "SUBMIT_CANDIDATE"]
    feedback = "\n".join(str(m.get("content", ""))
                         for m in ScriptedBackend.last_request.messages)
    assert "[UNBASELINED_FULL_SUITE_FAILURE]" in feedback
    assert "接口直接相关的现有测试" in feedback
    assert "SUBMIT_CANDIDATE" in feedback
    assert "existing tests for the changed interface" in ScriptedBackend.last_request.system_prompt
    assert "do not broaden accepted types" in ScriptedBackend.last_request.system_prompt
    log = tmp_path / "runtime_workspace" / "candidate_probe" / ".state" / "execution_log.json"
    assert log.is_file()
    events = json.loads(log.read_text(encoding="utf-8"))
    assert '"status": "candidate"' in log.read_text(encoding="utf-8")
    assert events["steps"][-1]["success"] is False


async def run_direct(root: Path, actions: list[ToolCall | str]):
    ScriptedBackend.script = actions
    ScriptedBackend.seen = []
    agent = SWEAgent("candidate_guard")
    ctx = AgentContext(agent_id="candidate_guard", task="Fix the public bug",
                       backend=ScriptedBackend(), tools=ToolRegistry(),
                       config={"project_path": str(root)}, metadata={})
    await agent.initialize(ctx)
    last = None
    for _ in actions:
        last = await agent.step(ctx)
        if last.type in ("candidate", "complete", "fail"):
            break
    return ctx, last


def test_repeated_preedit_full_suite_is_skipped_but_postedit_run_is_allowed(
    tmp_path: Path, monkeypatch,
):
    project(tmp_path, extra_failure=True)
    ScriptedBackend.requests = []
    executed: list[str] = []
    real_execute = ShellTool.execute

    def record_execute(self, context, params):
        executed.append(params["command"])
        return real_execute(self, context, params)

    monkeypatch.setattr(ShellTool, "execute", record_execute)
    actions: list[ToolCall | str] = [
        call("shell_exec", command="python -m pytest -q --tb=no"),
        call("shell_exec", command="python -m pytest -q"),
        call("shell_exec", command="python -m pytest test_target.py -q"),
        call("read_file", path="app.py"),
        call("edit", path="app.py", mode="regex_replace",
             old_text="FIXED = False", new_text="FIXED = True"),
        call("shell_exec", command="python -m pytest -q"),
        call("shell_exec", command="python -m pytest test_target.py -q"),
        "SUBMIT_CANDIDATE",
    ]
    ctx, action = asyncio.run(run_direct(tmp_path, actions))

    assert action.type == "candidate"
    assert executed.count("python -m pytest -q --tb=no") == 1
    assert executed.count("python -m pytest -q") == 1  # only after edit
    assert executed.count("python -m pytest test_target.py -q") == 2
    assert "pre_edit_full_suite_failure" in ctx.metadata
    assert "build a narrow reproduction" in ScriptedBackend.requests[0].system_prompt
    assert "pre-edit baseline" in ScriptedBackend.requests[1].system_prompt
    assert "CURRENT TARGET" not in ScriptedBackend.requests[1].system_prompt
    assert "Last working pytest command" not in ScriptedBackend.requests[1].system_prompt
    assert any("[REPEATED_FULL_SUITE]" in str(m.get("content", ""))
               for m in ScriptedBackend.last_request.messages)


def test_runtime_trace_growth_is_not_mistaken_for_a_source_edit(tmp_path: Path, monkeypatch):
    project(tmp_path)
    ScriptedBackend.requests = []
    real_invoke = ScriptedBackend.invoke

    def invoke_with_runtime_logs(self, request):
        for name in (".model_trace.jsonl",
                     ".zmai_workspace/agent/.state/execution_log.json",
                     ".zmai/memory/agent/tools.jsonl"):
            file = tmp_path / name
            file.parent.mkdir(parents=True, exist_ok=True)
            with file.open("a", encoding="utf-8") as stream:
                stream.write("runtime record\n")
        return real_invoke(self, request)

    monkeypatch.setattr(ScriptedBackend, "invoke", invoke_with_runtime_logs)
    actions = [
        call("shell_exec", command="echo preflight"),
        call("shell_exec", command="python -m pytest test_target.py -q"),
        call("read_file", path="app.py"),
    ]
    ctx, _ = asyncio.run(run_direct(tmp_path, actions))

    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "FIXED = False\n"
    assert ctx.metadata.get("ever_modified") is not True
    assert ctx.metadata["test_failed"] is True
    assert "tests are FAILING" in ScriptedBackend.requests[2].system_prompt
    assert "[UNBASELINED_TEST_FAILURE]" not in str(ctx.metadata["messages"])


def test_git_fingerprint_ignores_runtime_records(tmp_path: Path, monkeypatch):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    class Status:
        returncode = 0
        stdout = ("?? .model_trace.jsonl\n"
                  "?? .zmai_workspace/agent/.state/execution_log.json\n"
                  "?? .zmai/memory/agent/tools.jsonl\n"
                  " M app.py\n")

    monkeypatch.setattr("zmai.swe.agent.subprocess.run", lambda *a, **k: Status())
    fingerprint = _git_workspace_fingerprint(tmp_path)
    assert list(fingerprint) == ["git: M:app.py"]


def test_candidate_rejected_without_edit_or_test(tmp_path: Path):
    project(tmp_path)
    ctx, action = asyncio.run(run_direct(tmp_path, ["SUBMIT_CANDIDATE"]))
    assert action.type == "continue"
    assert not ctx.metadata.get("candidate_submitted")
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "FIXED = False\n"


def test_candidate_rejected_after_post_test_edit(tmp_path: Path):
    project(tmp_path)
    actions = script()[:-1] + [
        call("edit", path="app.py", mode="regex_replace",
             old_text="FIXED = True", new_text="FIXED = False"),
        "SUBMIT_CANDIDATE",
    ]
    ctx, action = asyncio.run(run_direct(tmp_path, actions))
    assert action.type not in ("candidate", "complete")
    assert not ctx.metadata.get("candidate_test_evidence")


def test_candidate_rejected_when_target_test_still_fails(tmp_path: Path):
    project(tmp_path)
    # A second real edit invalidates the earlier green; the retest fails.
    actions = script()[:4] + [
        call("edit", path="app.py", mode="regex_replace",
             old_text="FIXED = True", new_text="FIXED = False"),
        call("shell_exec", command="python -m pytest test_target.py -q"),
        "SUBMIT_CANDIDATE",
    ]
    ctx, action = asyncio.run(run_direct(tmp_path, actions))
    assert action.type not in ("candidate", "complete")
    assert not ctx.metadata.get("candidate_test_evidence")


def test_candidate_rejected_with_known_new_regression(tmp_path: Path):
    project(tmp_path)
    (tmp_path / "test_regression.py").write_text(
        "import app\ndef test_regression():\n    assert not app.FIXED\n", encoding="utf-8")
    actions = [
        call("shell_exec", command="python -m pytest test_regression.py -q"),
        *script()[:4],
        call("shell_exec", command="python -m pytest test_regression.py -q"),
        call("shell_exec", command="python -m pytest test_target.py -q"),
        "SUBMIT_CANDIDATE",
    ]
    ctx, action = asyncio.run(run_direct(tmp_path, actions))
    assert action.type not in ("candidate", "complete")
    assert ctx.metadata["unresolved_regressions"]
    assert ctx.metadata["test_progress"] != "regression"
    assert not ctx.metadata.get("candidate_submitted")


def test_related_behavior_checked_before_candidate(tmp_path: Path):
    project(tmp_path)
    (tmp_path / "test_existing.py").write_text(
        "import app\ndef test_existing_behavior():\n    assert app.FIXED\n",
        encoding="utf-8",
    )
    actions = script()[:-1] + [
        call("shell_exec", command="python -m pytest test_existing.py -q"),
        "SUBMIT_CANDIDATE",
    ]
    ctx, action = asyncio.run(run_direct(tmp_path, actions))
    assert action.type == "candidate"
    assert ctx.metadata["candidate_test_evidence"]["command"] == (
        "python -m pytest test_existing.py -q"
    )
    assert ctx.metadata["candidate_verification"] == "incomplete"
    assert "You do not need to run the full suite before submitting a candidate" in (
        ScriptedBackend.last_request.system_prompt
    )
    feedback = "\n".join(str(m.get("content", ""))
                         for m in ScriptedBackend.last_request.messages)
    assert "提交候选前无需先运行全量测试" in feedback


def test_filtered_targeted_test_retries_same_scope_before_candidate(tmp_path: Path):
    project(tmp_path)
    ScriptedBackend.requests = []
    actions = [
        call("read_file", path="app.py"),
        call("edit", path="app.py", mode="regex_replace",
             old_text="FIXED = False", new_text="FIXED = True"),
        call("shell_exec", command=(
            'python -m pytest test_target.py -q | '
            'python -c "import sys; sys.stdin.read(); print(\'filtered\')"')),
        "SUBMIT_CANDIDATE",
        call("shell_exec", command="python -m pytest test_target.py -q"),
        "SUBMIT_CANDIDATE",
    ]
    ctx, action = asyncio.run(run_direct(tmp_path, actions))

    assert action.type == "candidate"
    assert ScriptedBackend.seen == [
        "read_file", "edit", "shell_exec", "SUBMIT_CANDIDATE",
        "shell_exec", "SUBMIT_CANDIDATE",
    ]
    feedback = "\n".join(str(m.get("content", ""))
                         for m in ScriptedBackend.requests[3].messages)
    assert "[NO_TEST_EVIDENCE]" in feedback
    assert "仅重跑同一目标" in feedback
    assert "不要为补齐这次定向证据而运行全量测试" in feedback
    assert ctx.metadata["candidate_test_evidence"]["tests"] == 1


def test_unbaselined_post_edit_targeted_failure_does_not_force_blind_edit(
    tmp_path: Path,
):
    project(tmp_path)
    (tmp_path / "test_environment.py").write_text(
        "def test_environment():\n    assert False\n", encoding="utf-8",
    )
    actions = script()[:-1] + [
        call("shell_exec", command="python -m pytest test_environment.py -q"),
    ]
    ctx, action = asyncio.run(run_direct(tmp_path, actions))
    assert action.type == "continue"
    assert ctx.metadata["test_baseline_comparison"]["status"] == "unknown"
    assert ctx.metadata.get("test_failed") is False
    assert ctx.metadata.get("force_edit") is not True
    assert ctx.metadata.get("tests_passed") is not True
    assert "[UNBASELINED_TEST_FAILURE]" in "\n".join(
        str(m.get("content", "")) for m in ctx.metadata["messages"]
    )


def test_candidate_state_is_terminal_but_not_completed():
    assert AgentState.CANDIDATE.is_terminal
    assert AgentState.CANDIDATE != AgentState.COMPLETED


def test_budget_timeout_overrides_candidate(tmp_path: Path):
    project(tmp_path)
    ctx, action = asyncio.run(run_direct(tmp_path, script()))
    assert action.type == "candidate"
    ctx.metadata["timed_out"] = True
    result = asyncio.run(SWEAgent("candidate_guard").finalize(ctx))
    assert result.status == AgentState.TIMEOUT
