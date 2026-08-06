"""pytest 全局配置。

规避 Windows 上 pytest 清理 tmp_path 时的 PermissionError：
真实子进程（pytest/ShellTool）在 tmp 目录下运行后，pytest 会为每次会话
创建 `pytest-of-<user>/pytest-current` symlink；当目标目录已删除时，
pytest 的 cleanup_dead_symlinks 在 Windows 上 resolve() 失效链接会抛
WinError 5（拒绝访问），导致 pytest 以 traceback 结尾。

本钩子 tryfirst（先于 tmpdir 插件清理）主动移除失效 symlink 并提前关闭
tmp 工厂，使后续 cleanup 无死链、且 ExitStack.close() 幂等不再抛错。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _remove_dead_symlinks(base: Path) -> None:
    """删除 base 下指向已删目标的失效 symlink（Windows 安全版）。"""
    try:
        for entry in base.iterdir():
            try:
                if entry.is_symlink() and not entry.exists():
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """在 tmpdir 插件清理前移除失效 symlink 并释放 tmp 工厂。"""
    try:
        tpf = getattr(session.config, "_tmp_path_factory", None)
        if tpf is not None:
            base = Path(tpf.getbasetemp())
            _remove_dead_symlinks(base.parent)  # pytest-of-<user>
            tpf._exit_stack.close()  # 幂等；先关则 tmpdir 插件后关为空操作
    except Exception:
        pass
