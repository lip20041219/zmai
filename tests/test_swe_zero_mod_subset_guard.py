"""N-2: 零修改 + 非完整 scope 全绿不得作为完成证据。

修复前的实证攻击（本文件 1-3 号用例即它的回归）：

    shell_exec: python -m pytest -q test_app.py    # 模型自己挑的子集，全绿
    → baseline_test_count = None
    → test_success_count = 1
    → actions = ['complete']                        # COMPLETED

而同一条 `-k foo` / `-m smoke` 形态也一样。攻击的前提是：**子集全绿**但完整套件
并不全绿（剩下的测试正是失败的）—— 模型不改任何业务代码，只挑自己会通过的那部分
跑一遍，就能宣布整个任务完成。

根因不在 scope 分类（N-1 已修），而在**证据判定**：partial_green 分支里有一条
I2′ 例外 —— `_baseline is None`（首次运行即子集）+ 零修改 + 验收文件完好 ⇒ 把这次
子集全绿记成 `test_success_count += 1`，并在完成门禁的 `_has_completion_evidence`
里用同一条"未建立基线 ⇒ 也算证据"的旁路放行。两处都已被删除。

修复后的判据（唯一出口）：**可证明覆盖完整范围的运行全绿** —— 未指定目标、不带
缩范围选项（N-1）、配置与 run 起始一致（CR-5）、验收文件未被改动（CR-1/CR-3/CR-4）。
"跑一次测试，通过就停"（autostop / verify-only）仍然成立，只是那一次必须是完整范围：
见 4 号用例。

覆盖：1 位置目标 / 2 `-k` / 3 `-m` / 4 零修改 + 完整套件（仍可完成）/ 5 改过源码后
局部验证不被误杀 / 6 反事实（重放被删掉的那次 +1 状态后出口确实存在）/
7 所有完成出口（post-tool / text-only / finalize）逐一确认无旁路。
"""

from __future__ import annotations

from pathlib import Path

from tests.test_swe_acceptance_window_guard import _fix_app, _run, _write_bug_project
from tests.test_swe_autoverify_bound import _write_clean_project
from tests.test_swe_test_content_guard import _FAILING_TEST, TEXT, _write_split_project
from zmai.tool import ToolCall

_SUBSET_POSITIONAL = "python -m pytest -q test_app.py"
_SUBSET_SELECTOR = "python -m pytest -q -k test_app"
_SUBSET_MARK = "python -m pytest -q -m smoke"
_FULL = "python -m pytest -q"


def _shell(cmd: str) -> ToolCall:
    return ToolCall(id="sh", name="shell_exec", params={"command": cmd})


def _text(r) -> str:
    return "\n".join(str(m.get("content", ""))
                     for m in r.ctx.metadata.get("messages", []))


def _assert_no_completion(r) -> None:
    assert "complete" not in r.kinds, f"零修改 + 非完整 scope 不得完成: {r.kinds}"
    assert r.ctx.metadata.get("baseline_test_count") is None, \
        "子集运行不得锁定 baseline"
    assert r.ctx.metadata.get("test_success_count", 0) == 0, \
        "子集全绿不得计入完成证据"
    assert r.completion is None or r.completion.tests_complete is False
    assert r.ctx.metadata.get("tests_passed") is not True


# ═══════════════════════════════════════════════════════════════════
# 1-3 — 零修改 + 自选子集全绿：三种形态都不得完成
# ═══════════════════════════════════════════════════════════════════
class TestZeroModificationSubset:
    def test_positional_target_does_not_complete(self, tmp_path: Path):
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_shell(_SUBSET_POSITIONAL)], TEXT], max_steps=3,
                 project=lambda p: None)

        assert "1 passed" in r.text, "用例前提：子集确实全绿（完整套件不然）"
        assert (tmp_path / "test_bug.py").read_text(encoding="utf-8") == _FAILING_TEST
        _assert_no_completion(r)
        assert "[TEST_SCOPE_INCOMPLETE]" in _text(r)

    def test_k_selector_does_not_complete(self, tmp_path: Path):
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_shell(_SUBSET_SELECTOR)], TEXT], max_steps=3,
                 project=lambda p: None)

        assert "1 passed" in r.text, "用例前提：子集确实全绿"
        _assert_no_completion(r)

    def test_m_mark_selector_does_not_complete(self, tmp_path: Path):
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_shell(_SUBSET_MARK)], TEXT], max_steps=3,
                 project=lambda p: None)

        _assert_no_completion(r)


# ═══════════════════════════════════════════════════════════════════
# 4 — 反事实：零修改 + **完整套件**全绿 → 仍然可以完成（autostop 语义）
# ═══════════════════════════════════════════════════════════════════
class TestFullScopeStillCompletes:
    def test_zero_modification_full_suite_completes(self, tmp_path: Path):
        r = _run(tmp_path, [[_shell(_FULL)], TEXT], max_steps=3,
                 project=_write_clean_project)

        assert r.kinds[-1] == "complete", f"完整套件全绿必须能完成: {r.kinds}"
        assert r.ctx.metadata.get("ever_modified") is not True
        assert r.completion.tests_complete is True

    def test_subset_then_full_suite_completes(self, tmp_path: Path):
        """同一 run：先跑子集（不完成）→ 再跑完整套件 → 完成。"""
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_shell(_SUBSET_POSITIONAL)], [_shell(_FULL)], TEXT],
                 max_steps=4, project=lambda p: None)

        _assert_no_completion(r) if "complete" in r.kinds else None
        # 完整套件里 test_bug.py 仍然失败 → 正确结果是不完成，且这次是**失败证据**
        assert "complete" not in r.kinds
        assert "1 failed" in r.text or "failed" in r.text


# ═══════════════════════════════════════════════════════════════════
# 5 — 改过源码后的局部验证：按现有设计判断，不得误杀
# ═══════════════════════════════════════════════════════════════════
class TestLegitLocalVerificationNotKilled:
    def test_modified_subset_then_full_completes(self, tmp_path: Path):
        """改源码 → 子集验证（不完成）→ 完整套件全绿（完成）。"""
        r = _run(tmp_path, [[_fix_app()], [_shell("python -m pytest -q test_app.py")],
                            [_shell(_FULL)], TEXT],
                 max_steps=5, project=_write_bug_project)

        assert r.ctx.metadata.get("ever_modified") is True
        assert r.kinds[-1] == "complete", f"合法路径必须仍能完成: {r.kinds}"
        assert r.completion.tests_complete is True

    def test_modified_subset_alone_does_not_complete(self, tmp_path: Path):
        """只做局部验证就收工 → 仍然不得完成（既有语义，P1-4）。"""
        r = _run(tmp_path, [[_fix_app()], [_shell("python -m pytest -q test_app.py")],
                            TEXT],
                 max_steps=4, project=_write_bug_project)

        assert "complete" not in r.kinds, f"局部验证不得当完成证据: {r.kinds}"
        assert r.completion.tests_complete is False


# ═══════════════════════════════════════════════════════════════════
# 6 — 反事实：重放"被删掉的那次 +1"后，完成出口确实存在
# ═══════════════════════════════════════════════════════════════════
class TestCounterfactualOldEvidenceRule:
    def test_attack_completes_when_old_evidence_rule_restored(
            self, tmp_path: Path, monkeypatch):
        """关掉 N-2 的完成证据判据（恢复"未建立基线 ⇒ 子集全绿也算证据"）→
        同一条攻击必须重新 COMPLETED。"""
        def _old_rule(context) -> bool:
            comp = context.metadata.get("completion")
            return bool(comp and comp.tests_passed
                        and (comp.tests_complete
                             or context.metadata.get("baseline_test_count") is None))

        monkeypatch.setattr("zmai.swe.agent._has_full_scope_green_evidence",
                            _old_rule)
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_shell(_SUBSET_POSITIONAL)], TEXT], max_steps=3,
                 project=lambda p: None)

        assert r.kinds[-1] == "complete", \
            f"恢复旧判据后该攻击必须仍然成功（否则本条对照无意义）: {r.kinds}"
        assert (tmp_path / "test_bug.py").read_text(encoding="utf-8") == _FAILING_TEST


# ═══════════════════════════════════════════════════════════════════
# 7 — 所有完成出口逐一确认无旁路（post-tool / text-only / finalize）
# ═══════════════════════════════════════════════════════════════════
class TestNoCompletionExitBypass:
    def test_all_exits_blocked_for_zero_mod_subset(self, tmp_path: Path):
        import asyncio

        _write_split_project(tmp_path)
        # 子集全绿后连续只回文本：post-tool 完成点与 text-only 完成门禁都会经过
        r = _run(tmp_path, [[_shell(_SUBSET_POSITIONAL)], TEXT, TEXT, TEXT, TEXT],
                 max_steps=5, project=lambda p: None)

        assert "complete" not in r.kinds, f"任何出口都不得放行: {r.kinds}"
        assert r.ctx.metadata.get("test_success_count", 0) == 0

        # finalize：按 Runtime 的真实收尾记账（runtime.py:251 对 AgentAction.fail 写
        # step_failed；步数耗尽写 timed_out），两条路径都不得判成 completed。
        assert r.kinds[-1] == "fail", f"守卫连续拦截后应明确失败: {r.kinds}"
        r.ctx.metadata["step_failed"] = True
        assert asyncio.run(r.agent.finalize(r.ctx)).status.value == "failed"

        r.ctx.metadata.pop("step_failed")
        r.ctx.metadata["timed_out"] = True
        assert asyncio.run(r.agent.finalize(r.ctx)).status.value == "timeout"
