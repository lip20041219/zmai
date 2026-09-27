"""P1-2: 测试命令的独立超时预算，以及"超时 ≠ 测试证据"。

背景：普通 shell 的 30s 预算被套用到**完整测试套件**上时，一次还没跑完的 pytest
会在工具层变成失败结果，再被 auto_generate_checks 读成一条 `Command failed`
**失败证据** —— "没跑完"被当成"测试失败"，并烧掉 completion_block 预算。

同时覆盖分类口径：判定的对象必须是"**实际被执行**的那条 runner 命令"，
shell 重定向/管道是输出处理，不是测试目标。

  C  实际执行形态与分类口径一致（`2>&1` / `> log` 不改变 full-scope 判定）
  D1 普通 shell 命令仍走 context.timeout（行为不变）
  D2 测试命令走独立预算，可被 config["timeout.test"] 覆盖
  D3 超时既不产生通过证据，也不产生失败证据
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from zmai.swe.agent import _is_full_scope_test_command
from zmai.swe.tools import (
    TEST_COMMAND_TIMEOUT,
    ShellTool,
    _strip_trailing_pager,
    _translate_cmd,
)
from zmai.swe.verifier import auto_generate_checks, parse_test_totals, verify_test_output
from zmai.tool import ToolContext


class _FakeCompleted:
    returncode = 0
    stdout = ""
    stderr = ""


def _ctx(tmp_path: Path, timeout: int = 30, **cfg) -> ToolContext:
    return ToolContext(
        agent_id="t",
        workspace_path=tmp_path,
        project_path=tmp_path,
        config={"_quiet": True, **cfg},
        timeout=timeout,
    )


def _patch_run(monkeypatch, *, timeout_for: str | None = None) -> dict:
    """替换 subprocess.run，记录收到的 timeout；必要时对匹配的命令抛超时。"""
    seen: dict = {}

    def _fake_run(cmd, **kw):
        seen["timeout"] = kw.get("timeout")
        seen["cmd"] = cmd
        if timeout_for and timeout_for in str(cmd):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return seen


# ── C：实际执行形态与分类口径一致 ────────────────────────────────
def test_executed_form_classifies_like_raw_form():
    """ShellTool 执行的命令（_translate_cmd + _strip_trailing_pager 之后）必须与
    模型发来的原始串同判定——否则"执行的是完整套件、分类看到的却是子集"。"""
    for raw in [
        "python -m pytest -q",
        "python -m pytest -q 2>&1",
        "python -m pytest -q 2>&1 | tail -50",
        "python -m pytest -q | more",
        "python -m pytest tests/test_x.py -q",
        "python -m pytest tests/test_x.py -q 2>&1 | tail -20",
    ]:
        executed = _strip_trailing_pager(_translate_cmd(raw))
        assert _is_full_scope_test_command(raw) == \
            _is_full_scope_test_command(executed), raw


def _write_stub_pytest(root: Path) -> None:
    """放一个假 pytest 模块：`python -m pytest` 会执行它而不是真的 pytest。"""
    (root / "pytest.py").write_text(
        "import sys\nprint('2 passed in 0.01s')\nsys.exit(0)\n", encoding="utf-8")


@pytest.mark.parametrize("raw", [
    "python -m pytest -q",
    "python -m pytest -q 2>&1",
    "python -m pytest -q > pytest_stub.log 2>&1",
])
def test_full_scope_command_really_executes(raw, tmp_path):
    """带重定向的完整套件命令照样真的跑起来，且分类仍是 full-scope。"""
    _write_stub_pytest(tmp_path)
    assert _is_full_scope_test_command(raw) is True

    result = ShellTool().execute(_ctx(tmp_path), {"command": raw})

    assert result.success, (raw, result.error)
    assert (result.metadata or {}).get("exit_code") == 0


def test_subset_command_stays_subset_and_executes(tmp_path):
    _write_stub_pytest(tmp_path)
    raw = "python -m pytest tests/test_x.py -q 2>&1"
    assert _is_full_scope_test_command(raw) is False

    result = ShellTool().execute(_ctx(tmp_path), {"command": raw})

    assert result.success, (raw, result.error)
    assert "2 passed" in (result.output or "")


# ── D1/D2：超时预算 ────────────────────────────────────────────
def test_plain_shell_keeps_context_timeout(monkeypatch, tmp_path):
    seen = _patch_run(monkeypatch)
    ShellTool().execute(_ctx(tmp_path, timeout=45), {"command": "python report.py"})
    assert seen["timeout"] == 45


def test_test_command_uses_dedicated_timeout(monkeypatch, tmp_path):
    seen = _patch_run(monkeypatch)
    ShellTool().execute(_ctx(tmp_path, timeout=30), {"command": "python -m pytest -q"})
    assert seen["timeout"] == TEST_COMMAND_TIMEOUT
    assert TEST_COMMAND_TIMEOUT > 30


def test_test_timeout_is_configurable(monkeypatch, tmp_path):
    seen = _patch_run(monkeypatch)
    ShellTool().execute(
        _ctx(tmp_path, timeout=30, **{"timeout.test": 123}),
        {"command": "python -m pytest -q 2>&1 | tail -20"},
    )
    assert seen["timeout"] == 123


def test_explicit_timeout_from_model_still_wins(monkeypatch, tmp_path):
    seen = _patch_run(monkeypatch)
    ShellTool().execute(_ctx(tmp_path),
                        {"command": "python -m pytest -q", "timeout": 7})
    assert seen["timeout"] == 7


# ── D3：超时不是测试证据 ────────────────────────────────────────
def test_timeout_is_neither_pass_nor_fail_evidence(monkeypatch, tmp_path):
    _patch_run(monkeypatch, timeout_for="pytest")
    result = ShellTool().execute(_ctx(tmp_path, timeout=30),
                                 {"command": "python -m pytest -q"})

    assert result.success is False
    # 超时不得看起来像一次 exit 0 的干净运行
    assert (result.metadata or {}).get("exit_code") == 124

    text = (result.output or "") + (result.error or "")
    totals = parse_test_totals(text)
    assert (totals["passed"], totals["failed"], totals["errors"]) == (0, 0, 0)
    assert verify_test_output(text, exit_code=124).passed is False

    # 不产生失败证据（没有测试被跑到，不是"测试失败"）
    vresult = auto_generate_checks(
        [],
        [{"name": "shell_exec", "success": False, "output": result.output or "",
          "error": result.error, "exit_code": 124}],
        tmp_path,
    )
    assert all(c.passed for c in vresult.checks), \
        [c.name for c in vresult.checks if not c.passed]


def test_real_test_failure_still_produces_failure_evidence(monkeypatch, tmp_path):
    """反向护栏：真正的测试失败（有计数）不得被 timeout 信号放过。"""
    _patch_run(monkeypatch)
    vresult = auto_generate_checks(
        [],
        [{"name": "shell_exec", "success": False, "output": "",
          "error": "1 failed, 2 passed in 0.30s", "exit_code": 1}],
        tmp_path,
    )
    assert not all(c.passed for c in vresult.checks)
