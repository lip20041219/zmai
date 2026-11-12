"""N-1: pytest 命令行参数不能绕开"完整 scope"判定。

修复前的实证攻击序列（本文件 1 号用例即它的回归）：

    shell_exec: python -m pytest -q --ignore=test_bug.py
    → 1 passed（失败的验收测试根本没被收集）
    → baseline_test_count = 1, tests_complete = True, COMPLETED
    （磁盘上的 test_bug.py 仍是失败版本）

根因在 `test_scope_targets()`：它把 `-` 开头的 token 全部过滤掉，于是
`--ignore=test_bug.py` / `--deselect=...` 这类 **flag=值** 形态不产生位置目标，
`_is_full_scope_test_command()` 误判为"完整范围"。CR-5 修的是**配置文件**改范围，
这里是同一根因的**命令行**载体 —— 两者共用同一条完整 scope 证据链。

修复：在同一个判据里补上选项维度，且判据是**白名单**（`_FULL_SCOPE_SAFE_OPTIONS`）：
只有已知不改变收集范围的选项（`-q` / `-v` / `-s` / `-x` / `--tb=...` …）保留完整范围
资格，其余选项一律 fail-closed。此外，"零修改 + 首次子集全绿 = 本 run 自己的证据"
这一例外不再适用于**命令行显式过滤过**的运行：它的前提是"没有证据表明套件更大"，
而模型自己的命令就是那条证据（位置目标子集 `pytest test_a.py` 的语义不变）。

覆盖：1-4 四种缩范围写法（`=` 与分离取值）/ 5 裸命令仍可完成 / 6 不改变范围的选项
仍算完整范围 / 7 反向对照（关掉修复后攻击必须重新成功）/ 附：选项判定的白名单表 /
包装器、子 shell、PowerShell 目录切换的执行上下文不可证明 → 不给完整范围证据。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from tests.test_swe_acceptance_window_guard import _run
from tests.test_swe_autoverify_bound import _write_clean_project
from tests.test_swe_test_content_guard import (
    _FAILING_TEST,
    TEXT,
    _write_split_project,
)
from zmai.swe.agent import _is_full_scope_test_command
from zmai.swe.tools import cli_scope_filter, cli_scope_names_exclusions
from zmai.tool import ToolCall

_TAMPERED = "test_bug.py"
_PYTHON = f'"{sys.executable}"'

#: 缩范围的写法（`flag=值` 与 `flag 值` 两种取值形态）
_SHRINK_COMMANDS = [
    f"{_PYTHON} -m pytest -q --ignore={_TAMPERED}",
    f"{_PYTHON} -m pytest -q --ignore {_TAMPERED}",
    f"{_PYTHON} -m pytest -q --deselect=test_bug.py::test_value",
    f"{_PYTHON} -m pytest -q --deselect test_bug.py::test_value",
]


def _shell(cmd: str) -> ToolCall:
    return ToolCall(id="sh", name="shell_exec", params={"command": cmd})


def _assert_no_completion(r) -> None:
    assert "complete" not in r.kinds, f"缩范围后不得完成: {r.kinds}"
    assert r.ctx.metadata.get("baseline_test_count") is None, \
        "不得把缩小后的套件规模锁成 baseline"
    assert r.ctx.metadata.get("test_success_count", 0) == 0
    assert r.completion is None or r.completion.tests_complete is False
    assert r.completion is None or r.completion.tests_passed is False
    assert r.ctx.metadata.get("tests_passed") is not True


def _assert_blocked_before_run(r, option: str) -> None:
    """命令必须在 subprocess 之前被 TestGuard 阻断：没有执行，就没有测试证据。"""
    assert "TestGuard" in r.text, f"必须在执行前阻断（{option}）: {r.text[-300:]}"
    assert option in r.text, f"错误信息必须点名被拒选项: {option}"
    assert "1 passed" not in r.text, "被阻断的命令不得真的执行"
    _assert_no_completion(r)


def _assert_no_full_scope_completion(r) -> None:
    """子集全绿可记为 partial_green（tests_passed=True），但不得成为完成证据。"""
    assert "complete" not in r.kinds, f"子集全绿不得完成: {r.kinds}"
    assert r.ctx.metadata.get("baseline_test_count") is None, \
        "不得把子目录规模锁成完整套件 baseline"
    assert r.ctx.metadata.get("test_success_count", 0) == 0
    assert r.completion is None or r.completion.tests_complete is False
    assert r.ctx.metadata.get("tests_passed") is not True


# ═══════════════════════════════════════════════════════════════════
# 1-4 — 命令行缩范围（四种写法）
# ═══════════════════════════════════════════════════════════════════
class TestCliScopeShrink:
    def test_all_shrink_forms_are_not_full_scope(self):
        for cmd in _SHRINK_COMMANDS:
            assert not _is_full_scope_test_command(cmd), f"不得判为完整范围: {cmd}"
            assert cli_scope_filter(cmd), f"必须识别出缩范围的选项: {cmd}"

    def test_shrink_runs_never_produce_completion(self, tmp_path: Path):
        """四种写法逐一跑真实 step：执行前被阻断，不得建立 baseline、不得完成。"""
        for i, cmd in enumerate(_SHRINK_COMMANDS):
            project = tmp_path / f"case{i}"
            project.mkdir()
            _write_split_project(project)
            r = _run(project, [[_shell(cmd)], TEXT], max_steps=3,
                     project=lambda p: None)

            _assert_blocked_before_run(
                r, "--ignore" if "--ignore" in cmd else "--deselect")
            assert (project / _TAMPERED).read_text(encoding="utf-8") == _FAILING_TEST

    def test_zero_modification_shrink_run_still_does_not_complete(self, tmp_path: Path):
        """零修改也照样不得完成：例外的前提被模型自己的命令否掉了。"""
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_shell(_SHRINK_COMMANDS[0])], TEXT], max_steps=3,
                 project=lambda p: None)

        assert r.ctx.metadata.get("ever_modified") is not True
        _assert_no_completion(r)


# ═══════════════════════════════════════════════════════════════════
# 5-6 — 反事实：不缩范围的运行照旧
# ═══════════════════════════════════════════════════════════════════
class TestUnaffectedCommands:
    def test_bare_full_suite_still_completes(self, tmp_path: Path):
        r = _run(tmp_path, [[_shell(f"{_PYTHON} -m pytest -q")], TEXT], max_steps=3,
                 project=_write_clean_project)

        assert r.kinds[-1] == "complete", f"裸完整套件必须仍能完成: {r.kinds}"
        assert r.ctx.metadata.get("baseline_test_count") == 1
        assert r.completion.tests_complete is True

    def test_collection_neutral_options_stay_full_scope(self, tmp_path: Path):
        """`-q` / `-v` / `-s` / `-x` / `--tb=` 不改变收集范围 → 仍是完整范围。"""
        for cmd in ["python -m pytest -q", "python -m pytest -v", "pytest -x",
                    "python -m pytest -q -s --tb=short", "python -m pytest --durations=5",
                    "python -m pytest -q -rA",
                    # 只关缓存插件的 `-p` 不改变收集范围，不得被误伤。
                    "python -m pytest -q -p no:cacheprovider",
                    "python -m pytest -q -pno:cacheprovider"]:
            assert _is_full_scope_test_command(cmd), f"不得误判为子集: {cmd}"
            assert cli_scope_filter(cmd) == [], f"不得报出缩范围选项: {cmd}"

    def test_full_suite_with_neutral_flags_still_completes(self, tmp_path: Path):
        r = _run(tmp_path, [[_shell(f"{_PYTHON} -m pytest -q -x --tb=short")], TEXT],
                 max_steps=3, project=_write_clean_project)

        assert r.kinds[-1] == "complete", f"中性选项不得误伤完成: {r.kinds}"

    def test_pipe_and_redirect_forms_stay_full_scope(self):
        """口径不变：重定向/管道是输出处理，不是范围收缩。"""
        assert _is_full_scope_test_command("python -m pytest -q 2>&1 | tail -50")
        assert _is_full_scope_test_command("python -m pytest -q > pytest.log 2>&1")
        assert _is_full_scope_test_command("python -m unittest discover")
        # 位置目标仍是子集（既有语义，不是 N-1 的一部分）
        assert not _is_full_scope_test_command("python -m pytest -q test_a.py")
        assert not _is_full_scope_test_command("python -m pytest -q -k foo")


# ═══════════════════════════════════════════════════════════════════
# 白名单表 —— 用户点名的缩范围选项必须全部被认出来
# ═══════════════════════════════════════════════════════════════════
def test_scope_changing_options_are_all_detected():
    for cmd in [
        "python -m pytest -q --ignore=test_bug.py",
        "python -m pytest -q --ignore test_bug.py",
        "python -m pytest -q --deselect=test_bug.py::test_value",
        "python -m pytest -q --deselect test_bug.py::test_value",
        "python -m pytest -q --ignore-glob=test_*.py",
        "python -m pytest -q --ignore-glob test_*.py",
        "python -m pytest -q --confcutdir=tests",
        "python -m pytest -q --rootdir=.",
        "python -m pytest -q --override-ini=addopts=",
        "python -m pytest -q -o addopts=",
        "python -m pytest -q -c pytest-alt.ini",
        "python -m pytest -q -k foo",
        "python -m pytest -q -m slow",
        "python -m pytest -q --lf",
        "python -m pytest -q --collect-only",
        "python -m pytest -q -p no:randomly",
        "python -m pytest -q --unknown-future-option=1",
    ]:
        assert not _is_full_scope_test_command(cmd), f"必须 fail-closed: {cmd}"


# ═══════════════════════════════════════════════════════════════════
# 两档判据的边界 —— 点名排除 vs 选择表达式
# ═══════════════════════════════════════════════════════════════════
def test_declared_exclusion_boundary():
    """点名"跳过谁 / 换哪份配置"的选项 → 本次运行完全作废（含 I2′ 例外）。

    选择表达式（`-k` / `-m`）与位置目标不在此列：它们仍走既有的 partial_green
    语义（提示跑完整套件），本轮不改变那部分行为。
    """
    for cmd in ["python -m pytest -q --ignore=x",
                "python -m pytest -q --ignore-glob=x",
                "python -m pytest -q --deselect=x::y",
                "python -m pytest -q --rootdir=.",
                "python -m pytest -q --confcutdir=tests",
                "python -m pytest -q --override-ini=addopts=",
                "python -m pytest -q -o addopts=",
                "python -m pytest -q -c alt.ini"]:
        assert cli_scope_names_exclusions(cmd), f"必须认定为点名排除: {cmd}"

    for cmd in ["python -m pytest -q", "python -m pytest -q -k foo",
                "python -m pytest -q -m slow", "python -m pytest -q test_a.py",
                "python -m pytest -q --collect-only",
                # no:cacheprovider 只关缓存插件，不改变收集范围。
                "python -m pytest -q -p no:cacheprovider",
                "python -m pytest -q -pno:cacheprovider"]:
        assert not cli_scope_names_exclusions(cmd), f"不得认定为点名排除: {cmd}"


# ═══════════════════════════════════════════════════════════════════
# --override-ini：命令行**间接**改 addopts / testpaths 等收集配置
# ═══════════════════════════════════════════════════════════════════
class TestOverrideIniIndirection:
    def test_override_ini_addopts_cannot_shrink_scope(self, tmp_path: Path):
        """`--override-ini=addopts=--ignore=...` 与改配置文件等价 → 执行前阻断、不得完成。"""
        _write_split_project(tmp_path)
        cmd = "python -m pytest -q --override-ini=addopts=--ignore=test_bug.py"
        r = _run(tmp_path, [[_shell(cmd)], TEXT], max_steps=3, project=lambda p: None)

        _assert_blocked_before_run(r, "--override-ini")
        assert not _is_full_scope_test_command(cmd)


# ═══════════════════════════════════════════════════════════════════
# 7 — 反向对照：关掉 N-1 修复后，同一条攻击必须重新 COMPLETED
# ═══════════════════════════════════════════════════════════════════
class TestCounterfactualWithoutCliScopeCheck:
    def test_attack_is_blocked_by_evidence_guard_when_cli_checks_disabled(
            self, tmp_path: Path, monkeypatch):
        # 关闭 CLI 范围检测后，P0-4 的独立证据门仍应拒绝该不可信结果。
        monkeypatch.setattr("zmai.swe.agent.cli_scope_filter", lambda command: [])
        monkeypatch.setattr("zmai.swe.tools.cli_scope_names_exclusions",
                            lambda command: False)
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_shell(_SHRINK_COMMANDS[0])], TEXT], max_steps=3,
                 project=lambda p: None)

        assert "complete" not in r.kinds, r.kinds
        assert "[TEST_SCOPE_INCOMPLETE]" in r.text
        assert "[EvidenceGuard]" in r.text
        assert r.ctx.metadata.get("baseline_test_count") is None
        assert r.ctx.metadata.get("tests_passed") is not True
        assert (tmp_path / _TAMPERED).read_text(encoding="utf-8") == _FAILING_TEST

    def test_attack_completes_only_when_evidence_gate_is_also_disabled(
            self, tmp_path: Path, monkeypatch):
        """反事实：关闭 CLI 检查和独立证据门后，保留攻击成功的判别对照。"""
        monkeypatch.setattr("zmai.swe.agent.cli_scope_filter", lambda command: [])
        monkeypatch.setattr("zmai.swe.tools.cli_scope_names_exclusions",
                            lambda command: False)
        monkeypatch.setattr("zmai.swe.agent.cli_scope_names_exclusions",
                            lambda command: False)
        monkeypatch.setattr("zmai.swe.agent._completion_evidence_trusted",
                            lambda context: (True, "counterfactual disabled"))
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_shell(_SHRINK_COMMANDS[0])], TEXT], max_steps=3,
                 project=lambda p: None)

        assert r.kinds[-1] == "complete", r.kinds
        assert r.ctx.metadata.get("baseline_test_count") == 1
        assert (tmp_path / _TAMPERED).read_text(encoding="utf-8") == _FAILING_TEST


# ═══════════════════════════════════════════════════════════════════
# 包装器 / 子 shell / PowerShell 目录切换 —— 执行上下文不可静态证明
# ═══════════════════════════════════════════════════════════════════
def _write_subdir_project(root: Path) -> None:
    """根目录的验收测试失败；`tests/` 子目录里的测试是绿的 → 缩到子目录即可假绿。"""
    (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (root / "test_app.py").write_text(_FAILING_TEST, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "sub_test.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")


#: 目录切换被藏进包装体/分组的写法：第二段看起来是项目根的裸 runner，
#: 实际却在子目录执行（`cmd /c "cd tests && …"` 与子 shell 同根因）。
_OPAQUE_DIR_CHANGE_COMMANDS = [
    "(cd tests && python -m pytest -q --tb=short)",
    'bash -c "cd tests && python -m pytest -q --tb=short"',
    'cmd /c "cd tests && python -m pytest -q --tb=short"',
    'powershell -Command "Set-Location tests; python -m pytest -q --tb=short"',
    "Set-Location tests; python -m pytest -q --tb=short",
]

def _bash_python() -> str | None:
    """返回可在 bash 子环境导入 pytest 和 ZMAI 的解释器，否则跳过 E2E。"""
    exe = shutil.which("bash")
    if not exe:
        return None
    for interpreter in ("python", "python3"):
        command = (f"{interpreter} -c \"import pytest; "
                   "import zmai.swe.acceptance_guard\"")
        try:
            probe = subprocess.run([exe, "-c", command], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        if probe.returncode == 0:
            return interpreter
    return None


_BASH_PYTHON = _bash_python()


#: e2e 只跑当前平台真能执行的包装器（其余写法仍有上面的判定级断言）。
_E2E_DIR_CHANGE_COMMANDS = [
    f'({_PYTHON} -m pytest -q --tb=short)',
    *([f'bash -c "cd tests && {_BASH_PYTHON} -m pytest -q --tb=short"']
      if _BASH_PYTHON else []),
    *([f'powershell -Command "Set-Location tests; & \'{sys.executable}\' '
       '-m pytest -q --tb=short"'] if shutil.which("powershell") else []),
]


class TestWrapperAndSubshellScope:
    def test_opaque_context_is_not_full_scope(self):
        for cmd in _OPAQUE_DIR_CHANGE_COMMANDS:
            assert not _is_full_scope_test_command(cmd), \
                f"执行上下文不可证明时不得给完整范围资格: {cmd}"

    def test_subdir_green_behind_wrapper_never_completes(self, tmp_path: Path):
        """子目录里的全绿不得被当成完整套件证据（修复前均错误 COMPLETED）。"""
        for i, cmd in enumerate(_E2E_DIR_CHANGE_COMMANDS):
            project = tmp_path / f"wrap{i}"
            project.mkdir()
            r = _run(project, [[_shell(cmd)], TEXT], max_steps=3,
                     project=_write_subdir_project)

            assert "1 passed" in r.text, f"用例前提：子目录子集确实全绿: {cmd}"
            _assert_no_full_scope_completion(r)
            assert (project / "test_app.py").read_text(encoding="utf-8") == _FAILING_TEST

    def test_exclusion_hidden_in_wrapper_yields_no_test_evidence(self, tmp_path: Path):
        """包装体里的 `--ignore` 不会被 agent 当成测试运行 → 不产生任何证据（fail-closed）。"""
        commands = [f'({_PYTHON} -m pytest -q --ignore=test_bug.py)']
        if _BASH_PYTHON:
            commands.append(
                f'bash -c "{_BASH_PYTHON} -m pytest -q --ignore=test_bug.py"')
        for i, cmd in enumerate(commands):
            project = tmp_path / f"excl{i}"
            project.mkdir()
            r = _run(project, [[_shell(cmd)], TEXT], max_steps=3,
                     project=_write_split_project)

            # TestGuard may now reject the embedded exclusion before pytest runs.
            # Either way, this opaque wrapper must provide no usable test evidence.
            _assert_no_completion(r)
            assert "1 passed" not in r.text, f"guarded command unexpectedly ran: {cmd}"
            assert (project / _TAMPERED).read_text(encoding="utf-8") == _FAILING_TEST


# ═══════════════════════════════════════════════════════════════════
# 测试: 声明排除参数执行前阻断（ShellExecTool 直接单元测试）
# ═══════════════════════════════════════════════════════════════════


class TestDeclaredExclusionPreExecutionBlock:
    """-p/--ignore 等声明排除参数必须在命令执行前被阻断，不能执行后才报告。

    根因：原实现仅在 agent.py 结果处理阶段检测声明排除参数，此时命令已执行完毕
    （插件已加载、源码已转储），且工具返回 success=True 让模型认为违规命令成功。
    修复：ShellExecTool.execute() 在 subprocess.run 之前检测并阻断。
    """

    @staticmethod
    def _make_ctx(tmp_path: Path):
        from zmai.tool.base import ToolContext
        return ToolContext(
            agent_id="test_pre_block",
            workspace_path=tmp_path,
            project_path=tmp_path,
            config={},
            timeout=10,
        )

    def test_p_flag_blocked_before_execution(self, tmp_path: Path):
        """pytest -p plugin 必须在执行前被阻断，返回失败，不产生副作用。"""
        from zmai.swe.tools import ShellTool
        tool = ShellTool()
        ctx = self._make_ctx(tmp_path)
        # 在工作区创建一个假的插件文件，确认它不会被加载
        (tmp_path / "fake_plugin.py").write_text("raise RuntimeError('should not load')")
        result = tool.execute(ctx, {"command": f"{_PYTHON} -m pytest -p fake_plugin -q"})
        assert result.success is False, "被阻断的命令必须返回失败"
        assert "TestGuard" in (result.error or "")
        assert "-p" in (result.error or "")
        # 验证没有执行 pytest（工作区不应有 __pycache__ 等副作用）
        assert not (tmp_path / "__pycache__").exists()

    def test_ignore_flag_blocked_before_execution(self, tmp_path: Path):
        """pytest --ignore=test_x.py 必须在执行前被阻断。"""
        from zmai.swe.tools import ShellTool
        tool = ShellTool()
        ctx = self._make_ctx(tmp_path)
        result = tool.execute(
            ctx, {"command": f"{_PYTHON} -m pytest --ignore=test_bug.py -q"})
        assert result.success is False
        assert "TestGuard" in (result.error or "")
        assert "--ignore" in (result.error or "")

    def test_deselect_flag_blocked_before_execution(self, tmp_path: Path):
        """pytest --deselect 必须在执行前被阻断。"""
        from zmai.swe.tools import ShellTool
        tool = ShellTool()
        ctx = self._make_ctx(tmp_path)
        result = tool.execute(
            ctx, {"command": f"{_PYTHON} -m pytest --deselect=test_a.py::test_x"})
        assert result.success is False
        assert "TestGuard" in (result.error or "")

    def test_bare_pytest_not_blocked(self, tmp_path: Path):
        """正常的 python -m pytest -q 不应被阻断（即使执行失败，也是测试本身的失败）。"""
        from zmai.swe.tools import ShellTool
        tool = ShellTool()
        ctx = self._make_ctx(tmp_path)
        # 创建一个空的测试文件，pytest 会收集但没有测试（exit 0 或 collection error）
        (tmp_path / "test_empty.py").write_text("")
        result = tool.execute(ctx, {"command": f"{_PYTHON} -m pytest -q"})
        # 命令应该被执行（不会因为 TestGuard 被阻断）
        # 结果可能是 success 或 failure，取决于 pytest 是否能运行，但错误信息不应包含 TestGuard
        assert "TestGuard" not in (result.error or ""), \
            "正常 pytest 命令不应被 TestGuard 阻断"

    def test_collect_only_not_blocked(self, tmp_path: Path):
        """pytest --collect-only 不是声明排除参数，不应被执行前阻断。"""
        from zmai.swe.tools import ShellTool
        tool = ShellTool()
        ctx = self._make_ctx(tmp_path)
        (tmp_path / "test_empty.py").write_text("")
        result = tool.execute(
            ctx, {"command": f"{_PYTHON} -m pytest --collect-only -q"})
        assert "TestGuard" not in (result.error or ""), \
            "--collect-only 不应被声明排除参数检查阻断"

    def test_non_test_command_not_blocked(self, tmp_path: Path):
        """非测试命令（如 dir / echo）不应被声明排除参数检查影响。"""
        from zmai.swe.tools import ShellTool
        tool = ShellTool()
        ctx = self._make_ctx(tmp_path)
        result = tool.execute(ctx, {"command": "dir /b"})
        assert "TestGuard" not in (result.error or "")

    def test_p_flag_with_pipe_still_blocked(self, tmp_path: Path):
        """pytest -p plugin | findstr 即使带管道，-p 仍应被执行前阻断。"""
        from zmai.swe.tools import ShellTool
        tool = ShellTool()
        ctx = self._make_ctx(tmp_path)
        result = tool.execute(ctx, {
            "command": 'python -m pytest -q -p _dump_all_plugin | findstr /C:"FILE:"'
        })
        assert result.success is False
        assert "TestGuard" in (result.error or "")
        assert "-p" in (result.error or "")
