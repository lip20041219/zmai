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
仍算完整范围 / 7 反向对照（关掉修复后攻击必须重新成功）/ 附：选项判定的白名单表。
"""

from __future__ import annotations

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

#: 缩范围的写法（`flag=值` 与 `flag 值` 两种取值形态）
_SHRINK_COMMANDS = [
    f"python -m pytest -q --ignore={_TAMPERED}",
    f"python -m pytest -q --ignore {_TAMPERED}",
    "python -m pytest -q --deselect=test_bug.py::test_value",
    "python -m pytest -q --deselect test_bug.py::test_value",
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


# ═══════════════════════════════════════════════════════════════════
# 1-4 — 命令行缩范围（四种写法）
# ═══════════════════════════════════════════════════════════════════
class TestCliScopeShrink:
    def test_all_shrink_forms_are_not_full_scope(self):
        for cmd in _SHRINK_COMMANDS:
            assert not _is_full_scope_test_command(cmd), f"不得判为完整范围: {cmd}"
            assert cli_scope_filter(cmd), f"必须识别出缩范围的选项: {cmd}"

    def test_shrink_runs_never_produce_completion(self, tmp_path: Path):
        """四种写法逐一跑真实 step：都不得建立 baseline、不得完成。"""
        for i, cmd in enumerate(_SHRINK_COMMANDS):
            project = tmp_path / f"case{i}"
            project.mkdir()
            _write_split_project(project)
            r = _run(project, [[_shell(cmd)], TEXT], max_steps=3,
                     project=lambda p: None)

            assert "1 passed" in r.text, f"用例前提（{cmd}）：失败的验收测试没被收集"
            assert (project / _TAMPERED).read_text(encoding="utf-8") == _FAILING_TEST
            _assert_no_completion(r)

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
        r = _run(tmp_path, [[_shell("python -m pytest -q")], TEXT], max_steps=3,
                 project=_write_clean_project)

        assert r.kinds[-1] == "complete", f"裸完整套件必须仍能完成: {r.kinds}"
        assert r.ctx.metadata.get("baseline_test_count") == 1
        assert r.completion.tests_complete is True

    def test_collection_neutral_options_stay_full_scope(self, tmp_path: Path):
        """`-q` / `-v` / `-s` / `-x` / `--tb=` 不改变收集范围 → 仍是完整范围。"""
        for cmd in ["python -m pytest -q", "python -m pytest -v", "pytest -x",
                    "python -m pytest -q -s --tb=short", "python -m pytest --durations=5",
                    "python -m pytest -q -rA"]:
            assert _is_full_scope_test_command(cmd), f"不得误判为子集: {cmd}"
            assert cli_scope_filter(cmd) == [], f"不得报出缩范围选项: {cmd}"

    def test_full_suite_with_neutral_flags_still_completes(self, tmp_path: Path):
        r = _run(tmp_path, [[_shell("python -m pytest -q -x --tb=short")], TEXT],
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
        "python -m pytest -q -p no:cacheprovider",
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
                "python -m pytest -q -c alt.ini",
                "python -m pytest -q -p no:cacheprovider"]:
        assert cli_scope_names_exclusions(cmd), f"必须认定为点名排除: {cmd}"

    for cmd in ["python -m pytest -q", "python -m pytest -q -k foo",
                "python -m pytest -q -m slow", "python -m pytest -q test_a.py",
                "python -m pytest -q --collect-only"]:
        assert not cli_scope_names_exclusions(cmd), f"不得认定为点名排除: {cmd}"


# ═══════════════════════════════════════════════════════════════════
# --override-ini：命令行**间接**改 addopts / testpaths 等收集配置
# ═══════════════════════════════════════════════════════════════════
class TestOverrideIniIndirection:
    def test_override_ini_addopts_cannot_shrink_scope(self, tmp_path: Path):
        """`--override-ini=addopts=--ignore=...` 与改配置文件等价 → 同样不得完成。"""
        _write_split_project(tmp_path)
        cmd = "python -m pytest -q --override-ini=addopts=--ignore=test_bug.py"
        r = _run(tmp_path, [[_shell(cmd)], TEXT], max_steps=3, project=lambda p: None)

        assert "1 passed" in r.text, "用例前提：失败的验收测试确实没被收集"
        assert not _is_full_scope_test_command(cmd)
        _assert_no_completion(r)


# ═══════════════════════════════════════════════════════════════════
# 7 — 反向对照：关掉 N-1 修复后，同一条攻击必须重新 COMPLETED
# ═══════════════════════════════════════════════════════════════════
class TestCounterfactualWithoutCliScopeCheck:
    def test_attack_completes_when_cli_check_disabled(
            self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("zmai.swe.agent.cli_scope_filter", lambda command: [])
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_shell(_SHRINK_COMMANDS[0])], TEXT], max_steps=3,
                 project=lambda p: None)

        assert r.kinds[-1] == "complete", \
            f"关掉选项判据后该攻击必须仍然成功（否则本条对照无意义）: {r.kinds}"
        assert r.ctx.metadata.get("baseline_test_count") == 1
        assert (tmp_path / _TAMPERED).read_text(encoding="utf-8") == _FAILING_TEST
