"""CLI 输出格式化 — ANSI 着色、主题、结构化输出。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

# ── ANSI 转义码 ──────────────────────────────────────────────

class _ANSICodes:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # 标准前景色 (在暗色背景下表现良好)
    class FG:
        BLACK = "\033[30m"
        RED = "\033[31m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        BLUE = "\033[34m"
        MAGENTA = "\033[35m"
        CYAN = "\033[36m"
        WHITE = "\033[37m"
        BRIGHT_RED = "\033[91m"
        BRIGHT_GREEN = "\033[92m"
        BRIGHT_YELLOW = "\033[93m"
        BRIGHT_BLUE = "\033[94m"
        BRIGHT_CYAN = "\033[96m"


DARK_THEME = {
    "success": _ANSICodes.FG.BRIGHT_GREEN,
    "error": _ANSICodes.FG.BRIGHT_RED,
    "warning": _ANSICodes.FG.BRIGHT_YELLOW,
    "info": _ANSICodes.FG.CYAN,
    "dim": _ANSICodes.DIM,
    "highlight": _ANSICodes.FG.BRIGHT_BLUE,
    "label": _ANSICodes.FG.BRIGHT_CYAN,
    "value": _ANSICodes.FG.WHITE,
}

LIGHT_THEME = {
    "success": _ANSICodes.FG.GREEN,
    "error": _ANSICodes.FG.RED,
    "warning": _ANSICodes.FG.YELLOW,
    "info": _ANSICodes.FG.BLUE,
    "dim": _ANSICodes.DIM,
    "highlight": _ANSICodes.FG.BLUE,
    "label": _ANSICodes.FG.BLUE,
    "value": _ANSICodes.FG.BLACK,
}


@dataclass
class Theme:
    """输出主题。默认暗色模式，支持禁用 ANSI。"""

    colors: dict[str, str] = field(default_factory=lambda: dict(DARK_THEME))
    enabled: bool = True

    @classmethod
    def dark(cls) -> Theme:
        return Theme(colors=dict(DARK_THEME))

    @classmethod
    def light(cls) -> Theme:
        return Theme(colors=dict(LIGHT_THEME))

    @classmethod
    def plain(cls) -> Theme:
        return Theme(enabled=False)

    def colorize(self, text: str, style: str) -> str:
        if not self.enabled:
            return text
        code = self.colors.get(style, "")
        return f"{code}{text}{_ANSICodes.RESET}" if code else text

    # ── 便捷方法 ────────────────────────────────────────────

    def success(self, text: str) -> str:
        return self.colorize(text, "success")

    def error(self, text: str) -> str:
        return self.colorize(text, "error")

    def warning(self, text: str) -> str:
        return self.colorize(text, "warning")

    def info(self, text: str) -> str:
        return self.colorize(text, "info")

    def dim(self, text: str) -> str:
        return self.colorize(text, "dim")

    def highlight(self, text: str) -> str:
        return self.colorize(text, "highlight")


# ── 输出函数 ──────────────────────────────────────────────────

def print_json(data: Any) -> None:
    """JSON 格式输出（始终无着色）。"""
    sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def print_success(message: str, theme: Theme | None = None) -> None:
    t = theme or Theme.dark()
    sys.stdout.write(f"{t.success('+')} {message}\n")


def print_error(message: str, theme: Theme | None = None) -> None:
    t = theme or Theme.dark()
    sys.stderr.write(f"{t.error('x')} {t.error(message)}\n")


def print_warning(message: str, theme: Theme | None = None) -> None:
    t = theme or Theme.dark()
    sys.stdout.write(f"{t.warning('!')} {t.warning(message)}\n")


def print_info(message: str, theme: Theme | None = None) -> None:
    t = theme or Theme.dark()
    sys.stdout.write(f"{t.info('i')} {t.info(message)}\n")


def print_table(headers: list[str], rows: list[list[str]], theme: Theme | None = None) -> None:
    """打印表格（两端对齐）。"""
    t = theme or Theme.dark()
    if not rows:
        return
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(cell))

    # header
    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sys.stdout.write(t.highlight(header_line) + "\n")
    sys.stdout.write(t.dim("-" * len(header_line)) + "\n")

    # rows
    for row in rows:
        line = "  ".join(
            cell.ljust(col_widths[i]) if i < len(col_widths) else cell
            for i, cell in enumerate(row)
        )
        sys.stdout.write(line + "\n")
