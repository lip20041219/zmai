"""写盘后必须作废**该文件自己**的 __pycache__ 字节码缓存。

回归场景（全量测试中真实复现，`tests/test_swe_regression_detect.py` 的两条用例）：
Agent 在同一整秒内把 `VALUE = 1` 改成 `VALUE = 0`（**等长**），随后重跑 pytest。
CPython 的时间戳式 .pyc 校验只比较 `(int(source_mtime), source_size)` —— 两份
源码落在同一整秒、大小又相同，旧 .pyc 仍被判为有效，于是第二轮 pytest 报出与
第一轮**完全相同**的计数：regression 被读成 no_progress，`[Regression]` 不再注入，
`regression_detected` 不再累计。观测到的测试结果直接说谎。

本用例把"同一整秒 + 等长"钉死成确定性前提（源码 mtime 由测试显式设定，第一轮
pytest 写下的 .pyc 记下该整秒，改写后再把 mtime 拨回同一整秒），因此不依赖运气：

  * 未修复 → 子进程复用旧字节码，观察到**修改前**的行为 → 失败；
  * 已修复 → 写盘时旧 .pyc 已被删除 → 子进程必须观察到新源码。

EditTool 与 WriteFileTool 两条写入路径分别覆盖。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from zmai.swe.tools import EditTool, WriteFileTool
from zmai.tool import ToolContext

_BEFORE = "VALUE = 1\n"      # 10 字节
_AFTER = "VALUE = 0\n"       # 10 字节 —— 等长，正是复现条件之一
_TEST = (
    "import bug\n"
    "import helper\n"          # 未被改动的第二个模块：其 .pyc 必须存活
    "def test_a():\n    assert bug.VALUE == 1\n"
    "def test_b():\n    assert bug.VALUE == 2\n"
)

# 固定的"旧"整秒（模块导入时取一次，保证在编辑发生之前）。
# 第一轮 pytest 编译 bug.py 时 .pyc 记录的正是这一秒；改写后把 mtime 拨回
# _SECOND + 0.7 —— 同一整秒、同一大小，未修复的实现必然命中旧 .pyc。
_SECOND = float(int(time.time()) - 10)


def _ctx(root: Path) -> ToolContext:
    return ToolContext(agent_id="pyc", workspace_path=root, project_path=str(root),
                       config={"_quiet": True}, timeout=60)


def _pin_mtime(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def _pyc_files(root: Path, stem: str) -> list[Path]:
    cache = root / "__pycache__"
    if not cache.is_dir():
        return []
    return sorted(
        p for p in cache.iterdir()
        if p.name.startswith(f"{stem}.") and p.name.endswith(".pyc")
    )


def _project(root: Path) -> None:
    assert len(_AFTER) == len(_BEFORE), "复现条件：改写前后必须等长"
    (root / "bug.py").write_text(_BEFORE, encoding="utf-8")
    (root / "helper.py").write_text("LIMIT = 10\n", encoding="utf-8")
    (root / "test_all.py").write_text(_TEST, encoding="utf-8")
    _pin_mtime(root / "bug.py", _SECOND)


def _pytest(root: Path) -> str:
    """独立 Python 子进程执行真实 pytest（与 Agent 的 shell_exec 同一路径）。"""
    r = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=str(root),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return (r.stdout or "") + (r.stderr or "")


def _assert_stale_would_be_used(tmp_path: Path) -> None:
    """前置条件：旧 .pyc 已生成，且改写后会被拨回同一整秒。"""
    assert _pyc_files(tmp_path, "bug"), "前置条件：bug.py 的字节码缓存必须已存在"
    assert _pyc_files(tmp_path, "helper"), "前置条件：helper.py 也应有缓存（用于验证未被误删）"


def _assert_fresh_source_observed(tmp_path: Path, out: str) -> None:
    assert "2 failed" in out, f"子进程必须观察到修改后的源码: …{out[-400:]}"
    assert _pyc_files(tmp_path, "helper"), "不得删除其它模块的字节码缓存"


def test_edit_tool_invalidates_stale_bytecode(tmp_path):
    """edit（regex_replace，等长改写）后，子进程不得复用旧 .pyc。"""
    _project(tmp_path)
    assert "1 failed, 1 passed" in _pytest(tmp_path), "第一轮：1 passed / 1 failed"
    _assert_stale_would_be_used(tmp_path)

    r = EditTool().execute(_ctx(tmp_path), {
        "path": "bug.py", "mode": "regex_replace",
        "old_text": "VALUE = 1", "new_text": "VALUE = 0",
    })
    assert r.success, r.error
    assert (tmp_path / "bug.py").read_text(encoding="utf-8") == _AFTER
    _pin_mtime(tmp_path / "bug.py", _SECOND + 0.7)   # 拨回旧 .pyc 记录的整秒

    _assert_fresh_source_observed(tmp_path, _pytest(tmp_path))


def test_write_file_tool_invalidates_stale_bytecode(tmp_path):
    """write_file（等长覆盖）后，子进程不得复用旧 .pyc。"""
    _project(tmp_path)
    assert "1 failed, 1 passed" in _pytest(tmp_path), "第一轮：1 passed / 1 failed"
    _assert_stale_would_be_used(tmp_path)

    r = WriteFileTool().execute(_ctx(tmp_path), {"path": "bug.py", "content": _AFTER})
    assert r.success, r.error
    assert (tmp_path / "bug.py").read_text(encoding="utf-8") == _AFTER
    _pin_mtime(tmp_path / "bug.py", _SECOND + 0.7)

    _assert_fresh_source_observed(tmp_path, _pytest(tmp_path))
