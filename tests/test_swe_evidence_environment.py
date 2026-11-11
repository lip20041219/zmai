"""M1/P0-3：测试环境指纹与验证覆盖保护回归测试。

覆盖：
  * 正常完整验证：环境身份可证 → completed
  * pytest 配置变化：旧证据污染，候选/完成被拒绝
  * 外部插件文件变化：工作区看不到，但环境指纹/回放能拒绝候选
  * 环境快照缺失：覆盖 unknown → 只能 candidate，不得 completed
  * 合法非 pytest 配置修改：旧证据 stale，新完整验证仍可 completed
  * 工作区修改：由 stale 判定覆盖（本文件第 5 条也验证）
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from tests.test_swe_evidence_decision import ScriptedBackend, call, pytest_cmd
from zmai.agent import AgentContext
from zmai.swe.agent import SWEAgent
from zmai.swe.evidence import decide_terminal
from zmai.tool import ToolCall, ToolRegistry

_SEP = " & " if sys.platform == "win32" else " ; "
_SIMPLE = "def test_target():\n    assert True\n"
_DYNAMIC_TEST = (
    "import importlib\n"
    "def test_target():\n"
    "    importlib.import_module('app')\n"
    "    assert True\n"
)


def _project(tmp_path: Path, test_source: str, *, pyproject: str | None = None,
             conftest: str | None = None) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("FIXED = False\n", encoding="utf-8")
    (root / "test_target.py").write_text(test_source, encoding="utf-8")
    if pyproject:
        (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if conftest:
        (root / "conftest.py").write_text(conftest, encoding="utf-8")
    return root


def _edit() -> ToolCall:
    return call("edit", path="app.py", mode="regex_replace",
                old_text="FIXED = False", new_text="FIXED = True")


def _run_agent(project: Path, script, *, steps: int = 4, before=None, **config):
    ScriptedBackend.script = script
    agent = SWEAgent("env_probe")
    ctx = AgentContext(
        agent_id="env_probe", task="fix", backend=ScriptedBackend(),
        tools=ToolRegistry(),
        config={"project_path": str(project), "timeout": 60,
                "loop_guard.threshold": 50, **config},
        metadata={},
    )

    async def run():
        await agent.initialize(ctx)
        if before:
            before(ctx)
        actions = []
        for _ in range(steps):
            action = await agent.step(ctx)
            actions.append(action)
            if action.type in ("complete", "candidate", "fail"):
                break
        return actions

    return ctx, asyncio.run(run())


def _records(ctx: AgentContext):
    return ctx.metadata["evidence_ledger"].records


def test_full_green_with_proven_environment_completes(tmp_path):
    project = _project(tmp_path, _SIMPLE)
    ctx, actions = _run_agent(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("-q")),
    ], steps=2)

    assert actions[-1].type == "complete", actions
    record = _records(ctx)[-1]
    assert record.coverage == "full"
    assert record.unverified_scope == ""
    assert record.environment_fingerprint
    assert record.replay.verified is True
    assert record.replay.environment_unchanged is True
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind == "completed"


def test_pytest_scope_config_change_pollutes_prior_evidence(tmp_path):
    project = _project(
        tmp_path, _SIMPLE,
        pyproject='[tool.pytest.ini_options]\naddopts = ""\n')
    ctx, actions = _run_agent(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("test_target.py", "-q")),
        call("write_file", path="pyproject.toml",
             content='[tool.pytest.ini_options]\naddopts = "--strict-markers"\n'),
        "SUBMIT_CANDIDATE",
    ], steps=4)

    record = _records(ctx)[0]
    assert record.evidence_status == "polluted"
    assert "scope config" in record.invalid_reason
    assert actions[-1].type == "continue", actions
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind not in (
        "completed", "candidate")


def test_external_plugin_change_invalidates_candidate_evidence(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    plugin = plugins / "localplugin.py"
    plugin.write_text("# plugin v1\n", encoding="utf-8")
    old_path = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH",
                       str(plugins) + (os.pathsep + old_path if old_path else ""))
    project = _project(tmp_path, _SIMPLE,
                       conftest='pytest_plugins = ["localplugin"]\n')
    target = str(plugin).replace("\\", "/")
    rewrite = (f'python -c "open(r\'{target}\',\'w\')'
               f'.write(\'# plugin changed to a different length\\n\')"')

    ctx, actions = _run_agent(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("test_target.py", "-q")),
        call("shell_exec", command=rewrite),
        "SUBMIT_CANDIDATE",
    ], steps=4)

    record = _records(ctx)[0]
    assert record.environment_fingerprint
    assert record.replay.attempted is True
    assert record.replay.verified is False
    assert "environment changed since evidence" in record.replay.reason
    assert record.evidence_status == "polluted"
    assert actions[-1].type == "continue", actions
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind not in (
        "completed", "candidate")


def test_missing_environment_snapshot_downgrades_to_candidate(tmp_path):
    project = _project(tmp_path, _SIMPLE)

    def block_snapshot(ctx: AgentContext) -> None:
        guard = ctx.metadata["__acceptance_guard__"]
        # 让守卫写快照时失败（路径是目录），模拟环境身份不可获取。
        (Path(guard["dir"]) / "env_snapshot.json").mkdir()

    ctx, actions = _run_agent(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("-q")),
    ], steps=2, before=block_snapshot)

    assert actions[-1].type == "continue", actions
    record = _records(ctx)[-1]
    assert record.coverage == "unknown"
    assert record.unverified_scope == "test environment identity"
    assert record.environment_fingerprint == ""
    assert record.replay.verified is True
    assert ctx.metadata.get("test_success_count", 0) == 1
    decision = decide_terminal(ctx.metadata["evidence_ledger"])
    assert decision.kind == "candidate", decision


def test_text_only_completion_cannot_bypass_environment_coverage(tmp_path):
    """工具步被拦下后，下一步只回文本也不能绕过同一证据门禁。"""
    project = _project(tmp_path, _SIMPLE)

    def block_snapshot(ctx: AgentContext) -> None:
        guard = ctx.metadata["__acceptance_guard__"]
        (Path(guard["dir"]) / "env_snapshot.json").mkdir()

    ctx, actions = _run_agent(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("-q")),
        "done",
    ], steps=3, before=block_snapshot)

    assert actions[-1].type == "continue", actions
    text = "\n".join(str(m.get("content", ""))
                     for m in ctx.metadata.get("messages", []))
    assert "[EvidenceGuard]" in text
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind == "candidate"


def test_dynamic_import_targeted_coverage_is_unknown(tmp_path):
    project = _project(tmp_path, _DYNAMIC_TEST)
    ctx, actions = _run_agent(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("test_target.py", "-q")),
        "SUBMIT_CANDIDATE",
    ], steps=3)

    assert actions[-1].type == "candidate", actions
    record = _records(ctx)[0]
    assert record.coverage == "unknown"
    assert "dynamic imports" in record.unverified_scope
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind == "candidate"


def test_dynamic_import_full_suite_still_completes(tmp_path):
    project = _project(tmp_path, _DYNAMIC_TEST)
    ctx, actions = _run_agent(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("-q")),
    ], steps=2)

    assert actions[-1].type == "complete", actions
    record = _records(ctx)[-1]
    assert record.coverage == "full"
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind == "completed"


def test_legitimate_non_pytest_config_change_still_completes(tmp_path):
    project = _project(tmp_path, _SIMPLE,
                       pyproject='[project]\nname = "x"\n')
    ctx, actions = _run_agent(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("test_target.py", "-q")),
        call("write_file", path="pyproject.toml",
             content='[project]\nname = "x"\ndescription = "y"\n'),
        call("shell_exec", command=pytest_cmd("-q")),
    ], steps=4)

    assert actions[-1].type == "complete", actions
    targeted, full = _records(ctx)[0], _records(ctx)[-1]
    # 工作区修改使编辑后的定向证据过期；新完整验证仍然合法。
    assert targeted.evidence_status == "stale"
    assert full.evidence_status == "valid"
    assert full.coverage == "full"
    assert full.environment_fingerprint
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind == "completed"
