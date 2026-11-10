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
import time
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
from zmai.tool import Tool, ToolContext, ToolRegistry, ToolResult


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

    monkeypatch.setattr("zmai.swe.tools._run_shell_command", _fake_run)
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


# ── E：`| head -N` / `| tail -N` 不得把测试失败吞成 success ──────
# 实测 eval_pylint-dev__pylint-5859：整场唯一一次真实测试失败跑的是
# `python -m pytest tests/checkers/unittest_misc.py -x -q 2>&1 | head -60`，
# 输出恰好 60 行且不含 pytest 汇总行，退出码是 head 的 0，于是被判成
# success=True —— test_failed / FixDriving 从未激活，模型 25 次 read_file、0 次 edit。
_FAILING_PYTEST = "\n".join(
    [".....F", "=========== FAILURES ==========="]
    + [f"E   assert 1 == 0  # frame {i}" for i in range(57)]
    + ["=============================== short test summary info ===============================",
       "FAILED tests/checkers/unittest_misc.py::TestFixme::test_non_alphanumeric_codetag",
       "1 failed, 5 passed in 0.51s"]
)


def _patch_shell_semantics(monkeypatch) -> dict:
    """模拟真实 shell 的管道语义：末段是 head 时截断输出并返回它自己的 0。"""
    seen: dict = {}

    def _fake_run(cmd, **kw):
        seen["cmd"] = cmd
        if "head" in str(cmd):
            cut = "\n".join(_FAILING_PYTEST.splitlines()[:60])
            return subprocess.CompletedProcess(cmd, 0, stdout=cut, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout=_FAILING_PYTEST, stderr="")

    monkeypatch.setattr("zmai.swe.tools._run_shell_command", _fake_run)
    return seen


def test_head_pipe_does_not_hide_test_failure(monkeypatch, tmp_path):
    """`| head -60` 曾让失败整轮变成 success；现在必须在执行前摘掉。"""
    seen = _patch_shell_semantics(monkeypatch)
    result = ShellTool().execute(_ctx(tmp_path), {
        "command": "python -m pytest tests/checkers/unittest_misc.py -x -q 2>&1 | head -60"})

    assert "head" not in seen["cmd"], "head 掩盖退出码且切掉汇总行，必须在执行前剥离"
    assert result.success is False, (
        "真实测试失败不得因为 `| head -60` 被报成 success", result.output)
    assert "1 failed" in (result.error or ""), "失败计数必须回到模型可见的输出里"


def test_tail_pipe_also_restores_real_exit_code(monkeypatch, tmp_path):
    """`| tail -N` 保留汇总行但同样替换退出码，一并摘掉后判定不变且更直接。"""
    seen = _patch_shell_semantics(monkeypatch)
    result = ShellTool().execute(_ctx(tmp_path), {
        "command": "python -m pytest tests/test_x.py -q 2>&1 | tail -30"})

    assert "tail" not in seen["cmd"]
    assert result.success is False


def test_non_test_command_keeps_its_output_filter(monkeypatch, tmp_path):
    """非测试命令的 head/tail 是模型有意的输出裁剪，行为必须不变。"""
    seen = _patch_run(monkeypatch)
    ShellTool().execute(_ctx(tmp_path), {"command": "dir /s /b | tail -5"})
    assert "| tail -5" in seen["cmd"]


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


# ── E：Registry 集成 —— 外层守护线程超时不得截断工具自报的专用预算 ──
#
# 此前的用例全部**直连** `ShellTool.execute(...)`，因此"内层 600s"一直是绿的，
# 而生产路径（`ToolRegistry.execute_tool` → `_execute_with_timeout(context.timeout)`）
# 仍按默认 30s 掐断。以下用例走真实 Registry 调用链，只 spy 外层超时值、
# 照常执行，用来钉住组合语义。


def _spy_outer_timeout(monkeypatch) -> dict:
    """包裹 `ToolRegistry._execute_with_timeout`，记录外层实际使用的超时后照常执行。"""
    seen: dict = {}
    real = ToolRegistry._execute_with_timeout

    def _spy(tool, context, params, timeout):
        seen["outer"] = timeout
        return real(tool, context, params, timeout)

    monkeypatch.setattr(ToolRegistry, "_execute_with_timeout", staticmethod(_spy))
    return seen


def _registry_with_shell() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ShellTool())
    return registry


class _NoopTool(Tool):
    name = "noop_tool"
    description = "测试用空工具：不覆盖 timeout_for，用于验证默认超时语义未变"
    parameters = {"type": "object", "properties": {}}

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        return ToolResult.ok(output="noop")


def test_registry_does_not_cap_test_command_budget(monkeypatch, tmp_path):
    """E1 默认测试命令：Registry 外层必须用测试预算，而不是默认 30s。"""
    seen = _spy_outer_timeout(monkeypatch)
    _patch_run(monkeypatch)
    _registry_with_shell().execute_tool(
        "shell_exec", {"command": "python -m pytest -q"}, _ctx(tmp_path, timeout=30))
    assert seen["outer"] >= TEST_COMMAND_TIMEOUT, \
        f"测试命令在真实 Registry 路径上仍被外层截断: {seen['outer']}s"


def test_registry_keeps_context_timeout_for_plain_shell(monkeypatch, tmp_path):
    """E2 普通命令：仍等于 context.timeout，不被测试预算放大。"""
    seen = _spy_outer_timeout(monkeypatch)
    _patch_run(monkeypatch)
    _registry_with_shell().execute_tool(
        "shell_exec", {"command": "python report.py"}, _ctx(tmp_path, timeout=30))
    assert seen["outer"] == 30


def test_registry_honours_explicit_timeout_end_to_end(monkeypatch, tmp_path):
    """E3 模型显式 timeout=120：内外层一致，且内层仍是既有语义（显式优先）。"""
    seen = _spy_outer_timeout(monkeypatch)
    run = _patch_run(monkeypatch)
    _registry_with_shell().execute_tool(
        "shell_exec", {"command": "python -m pytest -q", "timeout": 120},
        _ctx(tmp_path, timeout=30))
    assert run["timeout"] == 120, "模型显式 timeout 必须传到真实执行路径"
    assert seen["outer"] == 120, f"外层应等于内层预算: {seen['outer']}s"


def test_explicit_timeout_above_default_test_budget_is_not_capped(monkeypatch, tmp_path):
    """E6 上轮遗留边界：显式 900s > 默认测试预算 600s 时，外层不得按 600s 掐断。

    600 只是 `TEST_COMMAND_TIMEOUT` 默认值（可被 `timeout.test` 覆盖），不是全局
    硬上限，因此外层必须跟随同一有效预算，而不是停在默认值上。
    """
    seen = _spy_outer_timeout(monkeypatch)
    run = _patch_run(monkeypatch)
    _registry_with_shell().execute_tool(
        "shell_exec", {"command": "python -m pytest -q", "timeout": 900},
        _ctx(tmp_path, timeout=30))
    assert run["timeout"] == 900, "内层应使用显式预算"
    assert seen["outer"] == 900, f"外层被默认预算截断: {seen['outer']}s < 900s"


def test_configured_test_budget_drives_both_layers(monkeypatch, tmp_path):
    """E7 配置 timeout.test=900：默认测试预算走配置，外层跟随同一值（非 600）。"""
    seen = _spy_outer_timeout(monkeypatch)
    run = _patch_run(monkeypatch)
    _registry_with_shell().execute_tool(
        "shell_exec", {"command": "python -m pytest -q"},
        _ctx(tmp_path, timeout=30, **{"timeout.test": 900}))
    assert run["timeout"] == 900, "内层应遵循 timeout.test 配置"
    assert seen["outer"] == 900, f"外层应遵循 timeout.test 配置: {seen['outer']}s"


def test_smaller_explicit_timeout_never_widens_outer_budget(monkeypatch, tmp_path):
    """E8 显式 timeout 小于调用方预算时：内层变小，外层不随之放大（外层≥内层即可）。"""
    seen = _spy_outer_timeout(monkeypatch)
    run = _patch_run(monkeypatch)
    _registry_with_shell().execute_tool(
        "shell_exec", {"command": "python report.py", "timeout": 10},
        _ctx(tmp_path, timeout=30))
    assert run["timeout"] == 10, "普通命令的显式 timeout 仍生效（内层语义不变）"
    assert seen["outer"] == 30, f"外层沿用调用方预算: {seen['outer']}s"


def test_registry_leaves_other_tools_untouched(monkeypatch, tmp_path):
    """E4 非 Shell 工具：未覆盖 timeout_for → 外层仍等于 context.timeout。"""
    seen = _spy_outer_timeout(monkeypatch)
    registry = ToolRegistry()
    registry.register(_NoopTool())
    result = registry.execute_tool("noop_tool", {}, _ctx(tmp_path, timeout=45))
    assert result.success is True
    assert seen["outer"] == 45


def test_registry_timeout_result_is_not_test_evidence(monkeypatch, tmp_path):
    """E5 超时结果：经真实 Registry 路径后仍是"既非通过也非失败证据"。"""
    _spy_outer_timeout(monkeypatch)
    _patch_run(monkeypatch, timeout_for="pytest")
    result = _registry_with_shell().execute_tool(
        "shell_exec", {"command": "python -m pytest -q"}, _ctx(tmp_path, timeout=30))
    assert result.success is False
    assert result.metadata.get("exit_code") == 124
    assert "timeout" in (result.error or "").lower()


def test_wall_budget_caps_model_requested_timeout(monkeypatch, tmp_path):
    seen = _spy_outer_timeout(monkeypatch)
    run = _patch_run(monkeypatch)
    deadline = time.monotonic() + 60
    context = _ctx(tmp_path, **{"timeout.max": 600,
                                "_wall_deadline_monotonic": deadline})
    result = _registry_with_shell().execute_tool(
        "shell_exec", {"command": "python -m pytest -q", "timeout": 2400}, context)
    assert result.success
    assert 0 < run["timeout"] <= 57
    assert seen["outer"] <= 60


def test_insufficient_wall_budget_does_not_start_shell(monkeypatch, tmp_path):
    monkeypatch.setattr("zmai.swe.tools._run_shell_command",
                        lambda *a, **kw: pytest.fail("shell started without budget"))
    context = _ctx(tmp_path, **{"_wall_deadline_monotonic": time.monotonic() + 2})
    result = _registry_with_shell().execute_tool(
        "shell_exec", {"command": "python -m pytest -q", "timeout": 2400}, context)
    assert not result.success
    assert result.metadata == {"exit_code": 124, "timeout_kind": "task_budget"}
    assert context.config["_wall_budget_exhausted"] is True


def test_shell_finishes_early_with_wall_budget(tmp_path):
    context = _ctx(tmp_path, **{"_wall_deadline_monotonic": time.monotonic() + 10})
    result = _registry_with_shell().execute_tool(
        "shell_exec", {"command": 'python -c "print(123)"', "timeout": 2400}, context)
    assert result.success and "123" in result.output
    assert result.metadata["exit_code"] == 0


def test_shell_timeout_kills_child_process_tree(tmp_path):
    marker = tmp_path / "child_survived.txt"
    child = tmp_path / "child.py"
    child.write_text("import time\nfrom pathlib import Path\ntime.sleep(3)\n"
                     f"Path({str(marker)!r}).write_text('survived')\n", encoding="utf-8")
    parent = tmp_path / "parent.py"
    parent.write_text("import subprocess,sys,time\n"
                      f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
                      "time.sleep(10)\n", encoding="utf-8")
    result = _registry_with_shell().execute_tool(
        "shell_exec", {"command": f'python "{parent}"', "timeout": 1},
        _ctx(tmp_path, timeout=5))
    assert not result.success
    assert result.metadata == {"exit_code": 124, "timeout_kind": "tool"}
    time.sleep(3.2)
    assert not marker.exists(), "timed-out shell left a live pytest-like child"
