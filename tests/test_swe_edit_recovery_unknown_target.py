"""edit-failure 恢复：测试文件不得成为 recovery target，未知目标改用一次性 grep discovery。

故障（修复前，实测复现）：
  TestGuard 拒绝写入测试文件后，`_handle_edit_failure` 保留上一次源码目标；若从未
  记录过，则以诊断落点 `last_failure_issue.file` 兜底。而**测试失败**的 traceback
  落点就是测试文件本身：

      last_failure_issue.file = 'test_app.py'   ← 断言失败的那个文件
      target                  = 'test_app.py'
      edit_recovery_read_allowance = 1          ← 定向 read 额度发给测试文件

  注入的恢复提示于是自相矛盾（实测原文）：

      target: test_app.py
      注意：……测试文件永远不得修改——target 必须是业务源码文件。
      - read target file first: 先 `read_file` 读 target 的**真实**内容与行号（仅本文件放行一次）
      - retry modification: 下一次必须用 `edit` 或 `write_file` 让 target 真实发生修改

  同一段文字既禁止改测试文件、又把它写成 target 并要求"让它真实发生修改" —— 把模型
  持续推回它刚被拒绝的动作，烧完恢复预算后 0-byte diff（pylint-5859 / 7228）。

修复后必须成立：

  | 条件 | target | read 额度 | grep 额度 |
  |---|---|---|---|
  | TestGuard 拒绝 + 兜底落点是测试文件 | **清空** | 不发放 | **1（一次性）** |
  | TestGuard 拒绝 + 兜底落点是业务源码 | 保留 | 1（原有行为） | 不发放 |
  | 非 force_edit | 空 | 不发放 | 不发放 |

本文件用真实 `SWEAgent` 驱动、真实文件系统与真实工具执行：T1/T2 都是真实
pytest 失败 → 真实诊断器 → 真实 TestGuard 拒绝，不手工填充 `last_failure_issue`。

注：实测确认"诊断器不运行"这一分支不可达 —— 失败型（AssertionError）与收集型
（ImportError）失败都会产出 `FailureIssue`，且 `.file` 均为 traceback 落点。因此
T2 改为验证**过滤器的另一半**：落点是业务源码时不得误伤。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tests.test_swe_edit_failure_recovery import (
    _PYTEST,
    HELPERS,
    _capture_messages,
    _capture_tool_results,
    _diagnostic_reads,
    _read_file,
    _run_agent,
    _ScriptedBackend,
    _write_flask_project,
)
from zmai.agent import AgentContext
from zmai.tool import ToolCall

# TestGuard 只读保护的目标：测试/验收文件永远不得修改
_TEST_WRITE = ToolCall(
    id="tw", name="write_file",
    params={"path": "tests/test_x.py",
            "content": "def test_x():\n    assert True\n"},
)

# 恢复期内用于"发现业务源码目标"的 grep
_GREP = ToolCall(id="g1", name="grep",
                 params={"pattern": "def index", "path": "app.py"})
_GREP_AGAIN = ToolCall(id="g2", name="grep",
                       params={"pattern": "app", "path": "app.py"})

# 业务源码自身抛错：traceback 落点在 app.py（而非测试文件）
_RAISE_APP = 'def index():\n    raise ValueError("boom")\n'
_RAISE_TEST = 'from app import index\n\n\ndef test_index():\n    assert index() == "Hello"\n'


def _write_raise_project(project_dir: Path) -> None:
    """异常从业务源码抛出 → 诊断落点是 app.py，过滤器不得误伤它。"""
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "app.py").write_text(_RAISE_APP, encoding="utf-8")
    (project_dir / "test_app.py").write_text(_RAISE_TEST, encoding="utf-8")
    (project_dir / "helpers.py").write_text(HELPERS, encoding="utf-8")
    (project_dir / "tests").mkdir(exist_ok=True)


def _allowance(ctx: AgentContext, key: str) -> int:
    return int(ctx.metadata.get(key, 0) or 0)


def _tools(results: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [r for r in results if r["name"] == name]


def _run_testguard_after_failure(project: Path, monkeypatch, script_tail) -> tuple:
    """真实流程：pytest 失败 → 诊断 → TestGuard 拒绝写测试文件。"""
    injected = _capture_messages(monkeypatch, "[EDIT_FAILURE_RECOVERY]")
    results = _capture_tool_results(monkeypatch)
    script = [[_PYTEST], _diagnostic_reads(), [_TEST_WRITE]] + list(script_tail)
    ctx, action = asyncio.run(_run_agent(project, _ScriptedBackend(script)))
    return ctx, action, injected, results


# ═══════════════════════════════════════════════════════════════════
# T1 — 真实流程：诊断落点 = 测试文件 → 必须被过滤为空
# ═══════════════════════════════════════════════════════════════════


class TestT1TracebackTestFileIsNotRecoveryTarget:
    def test_diagnostic_test_file_target_is_filtered_out(
            self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_traceback_target"
        _write_flask_project(project, with_tests_dir=True)
        ctx, _a, injected, _r = _run_testguard_after_failure(
            project, monkeypatch, [None])

        # ── 前置：必须真的复现"诊断器已运行且落点是测试文件"这个前提 ──
        issue = ctx.metadata.get("last_failure_issue")
        assert issue is not None, "前置条件：诊断器必须已运行"
        assert getattr(issue, "file", None) == "test_app.py", \
            f"前置条件：诊断落点应为测试文件，实际 {getattr(issue, 'file', None)!r}"
        assert ctx.metadata.get("force_edit") is True, "前置条件：必须处于 force_edit"

        # ── 核心断言：测试文件不得成为 recovery target ──
        assert ctx.metadata.get("edit_failure_target") in (None, ""), \
            "测试文件不得被写进 edit_failure_target"
        assert _allowance(ctx, "edit_recovery_read_allowance") == 0, \
            "定向 read 额度不得发给测试文件"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 1, \
            "target 被过滤为空后必须改发一次 grep discovery 额度"

        # ── 提示安全性（T7 的核心，与 T1 同一现场）──
        assert injected, "必须注入 [EDIT_FAILURE_RECOVERY]"
        msg = injected[0]
        assert "target: test_app.py" not in msg, \
            f"恢复提示不得把测试文件写成 target: {msg}"
        for bad in ("test_app.py", "test_x.py", "tests/"):
            assert bad not in msg.split("error:")[0], \
                f"target 行不得出现测试文件路径 {bad!r}: {msg.splitlines()[1]}"
        assert "已被排除" in msg, "必须说明上次的测试文件目标已被排除"
        assert "只允许一次 `grep`" in msg, "必须明确说明一次性 grep 额度"


# ═══════════════════════════════════════════════════════════════════
# T2 — 过滤器必须精确：诊断落点是业务源码时不得误伤
#
# 这是 T1 缺失的另一半：只断言"测试文件被清空"，一个恒返回 "" 的过滤器也能通过。
# 本用例用真实流程证明合法 target 被完整保留（定向 read 照发、不发 grep 额度）。
# ═══════════════════════════════════════════════════════════════════


class TestT2FilterIsPrecise:
    def test_source_file_target_is_preserved(self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_source_target_kept"
        _write_raise_project(project)
        ctx, _a, injected, _r = _run_testguard_after_failure(
            project, monkeypatch, [None])

        issue = ctx.metadata.get("last_failure_issue")
        assert issue is not None, "前置条件：诊断器必须已运行"
        assert getattr(issue, "file", None) == "app.py", \
            f"前置条件：本用例的 traceback 落点必须是业务源码，实际 " \
            f"{getattr(issue, 'file', None)!r}"
        assert ctx.metadata.get("force_edit") is True, "前置条件：必须处于 force_edit"

        # 合法源码 target 必须被保留（过滤器不得误伤），并走原有的定向 read 路径
        assert ctx.metadata.get("edit_recovery_read_allowance") == 1, \
            "落点是业务源码时，定向 read 额度必须照发（过滤器不得误伤）"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 0, \
            "已有合法 target 时不得再发 grep discovery 额度"
        assert injected, "必须注入 [EDIT_FAILURE_RECOVERY]"
        assert "target: app.py" in injected[0], \
            f"合法源码 target 必须照旧写进提示: {injected[0]}"


# ═══════════════════════════════════════════════════════════════════
# T3 / T4 / T5 — 额度可执行、一次性、且不开放 read_file
# ═══════════════════════════════════════════════════════════════════


class TestT3T4T5DiscoveryIsExecutableOnceAndNarrow:
    def test_first_grep_runs_second_and_read_blocked(self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_discovery_oneshot"
        _write_flask_project(project, with_tests_dir=True)
        ctx, _a, _i, results = _run_testguard_after_failure(
            project, monkeypatch,
            [[_GREP], [_GREP_AGAIN], [_read_file("app.py", "r9")], None])

        greps = _tools(results, "grep")
        assert len(greps) == 2, f"应观察到两次 grep 调用: {len(greps)}"

        # ── T3: 第一次 grep 真的执行，且真的定位到源码 ──
        first, second = greps
        assert first["success"] is True and "[FixDriving]" not in first["text"], \
            f"第一次 grep 必须真的执行: {first['text'][:200]}"
        assert "def index" in first["text"], \
            f"grep 必须真的定位到业务源码内容: {first['text'][:200]}"

        # ── T4: 第二次 grep 必须被拒（一次性额度，不是打开 grep 权限）──
        assert second["success"] is False and "[FixDriving]" in second["text"], \
            f"第二次 grep 必须被 FixDriving 拒绝: {second['text'][:200]}"

        # ── T5: read_file 不受该额度影响 ──
        reads = _tools(results, "read_file")
        assert reads[-1]["success"] is False and "[FixDriving]" in reads[-1]["text"], \
            f"grep discovery 不得放行 read_file: {reads[-1]['text'][:200]}"

        assert _allowance(ctx, "edit_recovery_grep_allowance") == 0, "额度消耗即失效"
        assert ctx.metadata.get("force_edit") is True, "grep 额度不得解除 force_edit"


# ═══════════════════════════════════════════════════════════════════
# T6 — 非 force_edit：不产生任何 recovery 额度
# ═══════════════════════════════════════════════════════════════════


class TestT6NoAllowanceOutsideForceEdit:
    def test_no_allowance_without_force_edit(self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_no_force_edit"
        _write_flask_project(project, with_tests_dir=True)
        injected = _capture_messages(monkeypatch, "[EDIT_FAILURE_RECOVERY]")
        results = _capture_tool_results(monkeypatch)

        # 未跑过 pytest、未攒读取 → force_edit 为 False
        script: list[list[ToolCall] | None] = [[_TEST_WRITE], None]
        ctx, _a = asyncio.run(_run_agent(project, _ScriptedBackend(script)))

        assert ctx.metadata.get("force_edit") is not True, \
            "前置条件：本用例必须不在 force_edit 下"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 0, \
            "非 force_edit 路径不得发放 recovery grep 额度"
        assert _allowance(ctx, "edit_recovery_read_allowance") == 0, \
            "非 force_edit 路径不得发放定向 read 额度"
        for m in injected:
            assert "只允许一次 `grep`" not in m, \
                f"非 force_edit 时不得承诺一次性 grep 额度: {m}"
        # 非 force_edit 下 grep 本就放行（不经过恢复额度）
        assert _tools(results, "write_file"), "应观察到 write_file 调用"


# ═══════════════════════════════════════════════════════════════════
# T8 — TestGuard 原有保护不回归
# ═══════════════════════════════════════════════════════════════════


class TestT8TestGuardStillProtectsTestFiles:
    def test_test_file_write_still_rejected_and_never_created(
            self, tmp_path: Path, monkeypatch):
        project = tmp_path / "rec_testguard_intact"
        _write_flask_project(project, with_tests_dir=True)
        ctx, _a, _i, results = _run_testguard_after_failure(
            project, monkeypatch, [[_GREP], [_TEST_WRITE], None])

        writes = _tools(results, "write_file")
        assert len(writes) == 2, f"应观察到两次 write_file: {len(writes)}"
        for w in writes:
            assert w["success"] is False and "TestGuard" in w["text"], \
                f"测试文件写入必须始终被 TestGuard 拒绝: {w['text'][:200]}"
        assert not (project / "tests" / "test_x.py").exists(), "测试文件仍不得被创建"
        assert _allowance(ctx, "edit_recovery_grep_allowance") <= 1, \
            "grep discovery 额度不得累积"
