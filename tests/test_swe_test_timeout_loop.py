"""B-1: 测试命令超时在 **Loop 层** 不得被当成"零测试证据"。

修复前：ShellTool 对超时返回 `exit_code=124` + `error="timeout (600s)"`（工具层语义
正确，已由 test_swe_test_cmd_timeout 的 D3 覆盖），但 `SWEAgent.step` 只看测试计数 ——
超时的计数同样是 0，于是落进 `[NO_TEST_EVIDENCE]` 分支：

    "[NO_TEST_EVIDENCE] 本次测试运行没有执行任何测试……下一步必须运行完整测试套件：
     python -m pytest -q"

模型被指令推着重跑**同一条必然再次超时**的命令；该路径既不置 `test_failed`、也不进
FixDriving，没有任何超时语义能把模型拉出来，只能烧墙钟（每次 600s）直到 max_steps 或
force_edit 预算耗尽。

修复后：超时被识别为**独立状态**（不是零证据），照旧既不产生通过证据也不产生失败
证据（fail-closed），指令换成可执行方向，并复用 completion_block_count 收敛到有界失败。

覆盖（对应用户要求的 A–G）：
  A/B  超时不产生失败证据 / 通过证据（CompletionState、test_failed 均不受影响）
  C    不再注入"必须重跑完整套件"的误导指令（且零证据老路径不受影响）
  D    超时不解除 force_edit，也不放行完成
  E    连续超时有界失败（复用 completion_block_count，非 max_steps）
  F    真实测试失败行为不变（真跑 pytest，不 patch subprocess）
  G    正常 full-scope 全绿的完成行为不变（真跑 pytest）
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.test_swe_autoverify_bound import (
    TEXT,
    _ScriptedBackend,
    _write_clean_project,
    _write_persistent_fail_project,
)
from tests.test_swe_test_cmd_timeout import _patch_run
from zmai.agent import AgentContext
from zmai.swe.agent import MAX_COMPLETION_BLOCKS, SWEAgent
from zmai.tool import ToolCall, ToolRegistry

# 完整套件命令（超时判定与 scope 判定无关，用最朴素的形态即可）。
_PYTEST_FULL = ToolCall(id="pt", name="shell_exec",
                        params={"command": "python -m pytest -q"})


def _pytest_timeout_calls(n: int) -> list[list[ToolCall]]:
    return [[ToolCall(id=f"pt{i}", name="shell_exec",
                      params={"command": "python -m pytest -q"})]
            for i in range(n)]


class _Run:
    def __init__(self, ctx, backend, actions):
        self.ctx = ctx
        self.backend = backend
        self.actions = actions

    @property
    def kinds(self) -> list[str]:
        return [a.type for a in self.actions]

    @property
    def text(self) -> str:
        """当前注入模型上下文的全部文本（用于断言注入了什么/没注入什么）。"""
        return "\n".join(str(m.get("content", ""))
                         for m in self.ctx.metadata.get("messages", []))


def _run(tmp_path: Path, script, *, max_steps: int = 6,
         project=_write_clean_project, pre_meta: dict | None = None) -> _Run:
    project(tmp_path)
    backend = _ScriptedBackend(script)
    ctx = AgentContext(
        agent_id="b1_timeout",
        task="修复 bug 使全部测试通过",
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata=dict(pre_meta or {}),
    )
    agent = SWEAgent("b1_timeout")
    asyncio.run(agent.initialize(ctx))
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action)
        if action.type in ("complete", "fail"):
            break
    return _Run(ctx, backend, actions)


# ═══════════════════════════════════════════════════════════════════
# A/B — 超时既非失败证据，也非通过证据
# ═══════════════════════════════════════════════════════════════════
class TestABTimeoutIsNotTestEvidence:
    def test_timeout_is_not_failure_evidence(self, monkeypatch, tmp_path: Path):
        _patch_run(monkeypatch, timeout_for="pytest")
        r = _run(tmp_path, _pytest_timeout_calls(1), max_steps=1)

        assert r.ctx.metadata.get("test_failed") is not True, \
            "超时不得被记成测试失败（没有失败证据）"
        assert r.ctx.metadata.get("repair_phase") not in ("diagnose", "plan"), \
            "超时不得进入修复态/注入修复计划"
        assert r.ctx.metadata.get("tests_ever_failed") is not True
        # 也不得有 failed check 流入验证证据
        v = r.ctx.metadata.get("verification")
        assert v is None or v.passed

    def test_timeout_is_not_success_evidence(self, monkeypatch, tmp_path: Path):
        _patch_run(monkeypatch, timeout_for="pytest")
        r = _run(tmp_path, _pytest_timeout_calls(1), max_steps=1)

        comp = r.ctx.metadata["completion"]
        assert comp.tests_passed is False, "超时不得产生通过证据"
        assert comp.tests_complete is False
        assert r.ctx.metadata.get("test_success_count", 0) == 0
        assert "complete" not in r.kinds, f"超时不得完成: {r.kinds}"


# ═══════════════════════════════════════════════════════════════════
# C — 不再注入"重跑完整套件"的误导指令；零证据老路径不受影响
# ═══════════════════════════════════════════════════════════════════
class TestNoMisleadingFullSuiteDirective:
    def test_timeout_does_not_ask_for_full_suite_rerun(self, monkeypatch, tmp_path: Path):
        _patch_run(monkeypatch, timeout_for="pytest")
        r = _run(tmp_path, _pytest_timeout_calls(1), max_steps=1)

        assert "[TEST_TIMEOUT]" in r.text, "必须给出超时专属指令"
        assert "[NO_TEST_EVIDENCE]" not in r.text, \
            "超时不是零测试证据，不得走零证据分支"
        assert "必须运行完整测试套件" not in r.text, \
            "不得指令模型重跑同一条必然超时的命令"
        assert r.ctx.metadata.get("required_next_action") == "narrow_test_scope"
        assert r.ctx.metadata.get("test_scope_incomplete") is not True

    def test_zero_evidence_path_unchanged(self, monkeypatch, tmp_path: Path):
        """对照组：真正"没跑测试"（exit 0、计数为 0）仍走原有零证据门禁。"""
        _patch_run(monkeypatch)      # 不超时：假 run 返回 exit 0 / 空输出
        r = _run(tmp_path, [[ToolCall(id="co", name="shell_exec",
                                      params={"command": "python -m pytest --collect-only -q"})]],
                 max_steps=1)

        assert "[NO_TEST_EVIDENCE]" in r.text, "零证据分支不得被 B-1 改动影响"
        assert "[TEST_TIMEOUT]" not in r.text
        assert r.ctx.metadata.get("required_next_action") == "run_full_test_suite"
        assert r.ctx.metadata.get("test_scope_incomplete") is True


# ═══════════════════════════════════════════════════════════════════
# D — 超时不解除 force_edit，也不放行完成（fail-closed 保持）
# ═══════════════════════════════════════════════════════════════════
class TestForceEditSemanticsPreserved:
    def test_timeout_keeps_force_edit_and_cannot_complete(self, monkeypatch, tmp_path: Path):
        _patch_run(monkeypatch, timeout_for="pytest")
        r = _run(tmp_path, _pytest_timeout_calls(1), max_steps=1,
                 pre_meta={"force_edit": True})

        assert r.ctx.metadata.get("force_edit") is True, \
            "超时不得解除强制修改期（它既不是进展也不是失败证据）"
        assert r.kinds == ["continue"], f"超时后的动作只能是 cont: {r.kinds}"
        assert r.ctx.metadata["completion"].tests_passed is False
        # 测试命令在强制修改期是放行的（否则模型无法验证），因此这次调用确实执行了
        assert r.backend._i == 1


# ═══════════════════════════════════════════════════════════════════
# E — 连续超时有界失败（不是烧到 max_steps 的 TIMEOUT）
# ═══════════════════════════════════════════════════════════════════
class TestConsecutiveTimeoutsFailBounded:
    def test_consecutive_timeouts_fail_bounded(self, monkeypatch, tmp_path: Path):
        _patch_run(monkeypatch, timeout_for="pytest")
        r = _run(tmp_path, _pytest_timeout_calls(MAX_COMPLETION_BLOCKS + 3),
                 max_steps=MAX_COMPLETION_BLOCKS + 3)

        assert r.kinds.count("fail") == 1, f"必须有界失败: {r.kinds}"
        assert r.kinds[-1] == "fail"
        # 每步一次超时 → 第 MAX+1 次超时即失败，不碰 max_steps
        assert len(r.actions) == MAX_COMPLETION_BLOCKS + 1, \
            f"应在 {MAX_COMPLETION_BLOCKS + 1} 步内收敛: {r.kinds}"
        assert "timed out" in (r.actions[-1].error or ""), r.actions[-1].error

    def test_progress_between_timeouts_resets_budget(self, monkeypatch, tmp_path: Path):
        """预算按"最近一次真实工作区进展"重置：改一次 → 超时一次，不得累积到失败。"""
        _patch_run(monkeypatch, timeout_for="pytest")
        script = _pytest_timeout_calls(2)
        # 在第 1 次超时之后插入一次真实修改（工作区指纹变化 → 预算归零）
        script.insert(1, [ToolCall(id="ed", name="edit",
                                   params={"path": "app.py", "mode": "regex_replace",
                                           "old_text": "return 1",
                                           "new_text": "return 1  # touched"})])
        r = _run(tmp_path, script, max_steps=3)

        assert "fail" not in r.kinds, f"有真实进展时不得判失败: {r.kinds}"
        assert r.ctx.metadata.get("ever_modified") is True
        assert r.ctx.metadata.get("completion_block_count", 0) <= MAX_COMPLETION_BLOCKS


# ═══════════════════════════════════════════════════════════════════
# F — 真实测试失败行为不变（真跑 pytest，不 patch subprocess）
# ═══════════════════════════════════════════════════════════════════
class TestRealFailureUnchanged:
    def test_real_test_failure_still_enters_repair(self, tmp_path: Path):
        r = _run(tmp_path, [[_PYTEST_FULL], TEXT], max_steps=2,
                 project=_write_persistent_fail_project)

        assert r.ctx.metadata.get("test_failed") is True, "真实失败必须进入修复态"
        assert r.ctx.metadata.get("tests_ever_failed") is True
        assert "[Repair Plan]" in r.text, "真实失败仍必须注入修复计划"
        assert "[TEST_TIMEOUT]" not in r.text
        assert "complete" not in r.kinds, f"失败未修复不得完成: {r.kinds}"


# ═══════════════════════════════════════════════════════════════════
# G — 正常 full-scope 全绿的完成行为不变（真跑 pytest）
# ═══════════════════════════════════════════════════════════════════
class TestGreenCompletionUnchanged:
    def test_full_scope_green_completes(self, tmp_path: Path):
        r = _run(tmp_path, [[_PYTEST_FULL], TEXT], max_steps=3)

        assert r.kinds[-1] == "complete", f"完整套件全绿必须能完成: {r.kinds}"
        comp = r.ctx.metadata["completion"]
        assert comp.tests_passed is True and comp.tests_complete is True
        assert r.ctx.metadata.get("test_success_count") == 1
        assert r.ctx.metadata.get("completion_block_count", 0) == 0
