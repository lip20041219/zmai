"""P1-ANSI：彩色 pytest 输出必须与无色输出解析出完全相同的结果。

FORCE_COLOR / PY_COLORS / `--color=yes` / CI 强制颜色时，pytest 会把 ANSI 转义
插进被解析的语义单元中间：

    \\x1b[1m\\x1b[31mapp.py\\x1b[0m:2: KeyError          ← 路径被转义包住
    \\x1b[31mFAILED\\x1b[0m test_app.py::test_a        ← "FAILED " 后紧跟转义而非空格

修复前实测（真实 Runtime）：error_type 退化成 'Error'、file:line 变成 '' :0、
源码片段为空、上下文里 P0-1 的 `>>` 根因标记消失。

本文件用**真实 pytest 进程**产出彩色/无色两种输出做对照，不手写转义样本冒充。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from zmai.swe.failure import parse_test_failure
from zmai.swe.verifier import (
    classify_test_progress,
    parse_test_totals,
    strip_ansi,
    verify_test_output,
)

FAILING_APP = 'def parse_config(cfg):\n    return cfg["missing_key"]\n'
FAILING_TEST = (
    "from app import parse_config\n\n\n"
    "def test_a():\n    assert parse_config({'a': 1}) == 1\n\n\n"
    "def test_b():\n    assert 1 == 1\n"
)
PASSING_APP = "def value():\n    return 1\n"
PASSING_TEST = (
    "from app import value\n\n\n"
    "def test_a():\n    assert value() == 1\n\n\n"
    "def test_b():\n    assert value() == 1\n"
)


def _write_project(root: Path, app_src: str, test_src: str) -> None:
    (root / "app.py").write_text(app_src, encoding="utf-8")
    (root / "test_app.py").write_text(test_src, encoding="utf-8")


def _pytest(project: Path, *args: str) -> str:
    """真实 pytest 进程输出（stdout+stderr 合并，与 ShellTool 一致）。"""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=project, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    return (r.stdout or "") + (r.stderr or "")


# ── A：彩色 pytest summary 正确解析 ─────────────────────────────
def test_colored_summary_parsed_identically_to_plain(tmp_path):
    """--color=yes 的计数必须与无色完全一致。"""
    _write_project(tmp_path, FAILING_APP, FAILING_TEST)
    colored = _pytest(tmp_path, "--color=yes")
    plain = _pytest(tmp_path)

    assert "\x1b" in colored, "夹具必须真的产出 ANSI 转义（否则本用例空洞通过）"
    assert "\x1b" not in plain, "无颜色运行不应含转义"
    assert parse_test_totals(colored) == parse_test_totals(plain)
    assert parse_test_totals(colored) == {
        "passed": 1, "failed": 1, "errors": 0,
        "skipped": 0, "deselected": 0, "ignored": 0, "collected": 0,
    }


def test_colored_green_summary_parsed(tmp_path):
    """全绿项目在彩色下同样解析为 2 passed / 0 failed。"""
    _write_project(tmp_path, PASSING_APP, PASSING_TEST)
    colored = _pytest(tmp_path, "--color=yes")

    assert "\x1b" in colored
    totals = parse_test_totals(colored)
    assert totals["passed"] == 2 and totals["failed"] == 0 and totals["errors"] == 0


# ── B：彩色 traceback frame 正确提取 file:line ──────────────────
def test_colored_traceback_frame_yields_file_line(tmp_path):
    """彩色 traceback 必须解析出与无色相同的 file:line / error_type。"""
    _write_project(tmp_path, FAILING_APP, FAILING_TEST)
    colored = _pytest(tmp_path, "--color=yes")
    plain = _pytest(tmp_path)

    c_issue = parse_test_failure(colored, project_root=tmp_path)
    p_issue = parse_test_failure(plain, project_root=tmp_path)
    assert c_issue is not None and p_issue is not None

    assert (c_issue.file, c_issue.line) == (p_issue.file, p_issue.line) == ("app.py", 2), (
        f"彩色输出必须定位到与无色相同的根因帧: {c_issue.file}:{c_issue.line}"
    )
    assert c_issue.error_type == p_issue.error_type == "KeyError"
    assert c_issue.test_name == p_issue.test_name == "test_a"
    assert c_issue.issue_type == p_issue.issue_type
    # 根因源码片段与 >> 标记必须存在（P0-1 链路）
    assert ">> " in c_issue.source_snippet, "彩色下根因行标记不得丢失"
    assert c_issue.source_snippet == p_issue.source_snippet


# ── C：彩色输出不得制造假 failure / 假 regression ────────────────
def test_colored_green_does_not_create_false_failure(tmp_path):
    """全绿彩色输出不得被判为失败。"""
    _write_project(tmp_path, PASSING_APP, PASSING_TEST)
    colored = _pytest(tmp_path, "--color=yes")

    assert verify_test_output(colored).passed is True, (
        f"彩色全绿不得误判为失败: {verify_test_output(colored).error}"
    )


def test_colored_failed_marker_alone_is_detected(tmp_path):
    """只剩 FAILED 标记（summary 行被截断）时，彩色输出不得漏判为**通过**。

    "FAILED " 后紧跟转义序列是 ANSI 下最典型的丢标记形态。summary 行还在时会被
    "N failed" 兜住；summary 被截掉后，FAILED 标记就是唯一证据 —— 此时若标记
    因转义丢失，输出里只剩 "test session starts" 这个通过信号 → **假通过**。
    """
    colored = (
        "\x1b[1m===== test session starts =====\x1b[0m\n"
        "\x1b[36mcollected 2 items\x1b[0m\n"
        "\x1b[36m\x1b[1m===== short test summary info =====\x1b[0m\n"
        "\x1b[31mFAILED\x1b[0m test_app.py::\x1b[1mtest_a\x1b[0m - x\n"
    )
    assert "FAILED " not in colored, "夹具必须复现'标记后紧跟转义'的形态"
    assert verify_test_output(colored).passed is False, (
        "FAILED 标记不得因转义丢失 —— 丢失后只剩通过信号，会假通过"
    )


def test_colored_output_does_not_create_false_regression(tmp_path):
    """彩色输出参与的 regression 判定必须与无色一致。"""
    _write_project(tmp_path, PASSING_APP, PASSING_TEST)
    c1 = _pytest(tmp_path, "--color=yes")
    c2 = _pytest(tmp_path, "--color=yes")
    p1 = _pytest(tmp_path)

    # 彩色 → 彩色：状态不变，既不是 progress 也不是 regression
    assert classify_test_progress(
        parse_test_totals(c1), parse_test_totals(c2)) == "no_progress"
    # 彩色基线 vs 无色当前：同一套计数，同样不得凭空产生 regression
    assert classify_test_progress(
        parse_test_totals(c1), parse_test_totals(p1)) == "no_progress"


def test_colored_failure_does_not_create_false_regression(tmp_path):
    """失败在彩色下的计数仍被正确识别（regression 语义不变）。"""
    _write_project(tmp_path, FAILING_APP, FAILING_TEST)
    colored = _pytest(tmp_path, "--color=yes")
    totals = parse_test_totals(colored)

    # 2 passed/0 failed → 1 passed/1 failed 判 regression（与无色同源）
    assert classify_test_progress(
        {"passed": 2, "failed": 0, "errors": 0, "collected": 0}, totals) == "regression"


# ── D：无 ANSI 的原有输出完全不回归 ─────────────────────────────
def test_strip_ansi_leaves_plain_text_untouched():
    """无转义的文本必须原样返回（不做无谓改写）。"""
    plain = "2 passed, 1 failed in 0.16s\napp.py:2: KeyError\n"
    assert strip_ansi(plain) == plain
    assert strip_ansi("") == ""
    assert strip_ansi(None) == ""


def test_plain_output_parsing_unchanged(tmp_path):
    """无颜色输出解析结果与既有语义一致（回归护栏）。"""
    _write_project(tmp_path, FAILING_APP, FAILING_TEST)
    plain = _pytest(tmp_path)

    assert parse_test_totals(plain)["failed"] == 1
    assert verify_test_output(plain).passed is False
    issue = parse_test_failure(plain, project_root=tmp_path)
    assert (issue.file, issue.line, issue.error_type) == ("app.py", 2, "KeyError")


def test_strip_ansi_handles_common_escape_forms():
    """常见转义形态（SGR / 重置 / 组合参数 / 非 CSI）都要剥掉。"""
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"
    assert strip_ansi("\x1b[1m\x1b[31mbold\x1b[0m") == "bold"
    assert strip_ansi("\x1b[39;49;00mplain\x1b[0m") == "plain"
    assert strip_ansi("\x1b(Btext") == "text"
