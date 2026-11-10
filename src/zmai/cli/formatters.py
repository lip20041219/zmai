"""CLI 输出格式化 — ANSI 着色、主题、结构化输出。"""

from __future__ import annotations

import json
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
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


def _fg256(n: int) -> str:
    """256 色前景近似（Windows Terminal / VS Code / 现代 conhost 均支持）。"""
    return f"\033[38;5;{n}m"


# 蓝色 CRT 主题：黑底 + 灰白正文 + 蓝色强调，错误用蓝白反色而非红色。
CRT_THEME = {
    "success": _fg256(153),           # 冰蓝 #A8D8FF
    "error": "\033[97;48;5;24m",      # 蓝白反色 [FAIL] 标签
    "warning": _fg256(75),            # 主蓝 #5C9DFF
    "info": _fg256(75),
    "dim": _ANSICodes.DIM,
    "highlight": _fg256(153),
    "label": _fg256(153),
    "value": _fg256(252),
    "border": _fg256(24),             # 暗蓝边框
    "text": _fg256(252),              # 正文灰白
    "muted": _fg256(243),             # 弱化文字
}


@dataclass
class Theme:
    """输出主题。默认蓝色 CRT，支持禁用 ANSI。"""

    colors: dict[str, str] = field(default_factory=lambda: dict(CRT_THEME))
    enabled: bool = True

    @classmethod
    def crt(cls) -> Theme:
        return Theme(colors=dict(CRT_THEME))

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


# ── 蓝色 CRT 终端渲染 ────────────────────────────────────────
# 流式工作站：启动面板 + 实时事件行（[TRACE]/[ OK ]/[FAIL]）+ 结束摘要。
# 不做全屏重绘，避免阻塞输入、破坏日志复制或依赖大型 TUI 库。

_LOGO = [
    "███████╗ ███╗   ███╗  █████╗ ██████╗",
    "╚══███╔╝ ████╗ ████║ ██╔══██╗  ██╔╝",
    "  ███╔╝  ██╔████╔██║ ███████║  ██║",
    " ███╔╝   ██║╚██╔╝██║ ██╔══██║  ██║",
    "███████╗ ██║ ╚═╝ ██║ ██║  ██║  ██║",
    "╚══════╝ ╚═╝     ╚═╝ ╚═╝  ╚═╝██████╝",
]
_LOGO_WIDTH = max(len(line) for line in _LOGO)

_EVENT_TAGS = {"tool": "TRACE", "ok": " OK  ", "fail": "FAIL", "info": " SYS "}
_EVENT_STYLE = {"tool": "info", "ok": "highlight", "fail": "error", "info": "muted"}


def _disp_width(text: str) -> int:
    """终端显示宽度（CJK 宽字符按 2 列计）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _clip(text: str, width: int) -> str:
    """单行化并按显示宽度截断，避免换行破坏面板/日志布局。"""
    text = str(text).replace("\r", " ").replace("\n", " ")
    if width <= 0:
        return ""
    if _disp_width(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + cw > width - 1:
            break
        out.append(ch)
        used += cw
    return "".join(out) + "…"


def _glyphs() -> dict[str, str]:
    enc = getattr(sys.stdout, "encoding", "") or "utf-8"
    try:
        "┌─┐│└┘├┤█╗".encode(enc)
        return {"h": "─", "v": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘"}
    except (UnicodeEncodeError, LookupError):
        return {"h": "-", "v": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+"}


def term_width(default: int = 100) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:
        return default


def render_panel(
    title: str,
    rows: list[tuple[str, str] | str],
    theme: Theme | None = None,
    width: int | None = None,
    default_style: str = "text",
) -> str:
    """渲染带边框面板。rows 为 (纯文本, 样式) 或纯文本，内容不得含 ANSI。"""
    t = theme or Theme.crt()
    g = _glyphs()
    width = max(24, min(width or term_width(), 78))
    border = lambda s: t.colorize(s, "border")  # noqa: E731

    title = _clip(title, max(1, width - 6))
    fill = "─" if g["h"] == "─" else "-"
    top = (
        border(g["tl"] + fill)
        + t.colorize(f" {title} ", "highlight")
        + border(fill * max(0, width - 5 - _disp_width(title)) + g["tr"])
    )
    lines = [top]
    for row in rows:
        content, style = (row, default_style) if isinstance(row, str) else row
        content = _clip(content, width - 4)
        pad = " " * max(0, width - 4 - _disp_width(content))
        lines.append(
            border(g["v"]) + t.colorize(f" {content}{pad} ", style) + border(g["v"])
        )
    lines.append(border(g["bl"] + fill * (width - 2) + g["br"]))
    return "\n".join(lines)


def render_banner(
    theme: Theme | None = None,
    *,
    version: str = "",
    mode: str = "AUTONOMOUS",
    status: str = "READY",
    session: str = "",
    task: str = "",
    backend: str = "",
    budget: int | None = None,
    project: str = "",
    width: int | None = None,
) -> str:
    """启动面板：ZMAI Logo + 运行模式 + 真实配置（无数据处显示 N/A）。"""
    width = max(32, min(width or term_width(), 78))
    rows: list[tuple[str, str]] = []
    if width >= _LOGO_WIDTH + 6:
        rows.extend((line, "highlight") for line in _LOGO)
        rows.append(("", "text"))
    rows.append((f"v{version}    {mode} MODE" if version else f"{mode} MODE", "info"))
    rows.append((f"STATUS: {status}    SESSION: {session or 'N/A'}", "text"))
    rows.append((f"MODEL: {backend or 'N/A'}    BUDGET: {budget if budget is not None else 'N/A'}", "text"))  # noqa: E501
    if project:
        rows.append((f"PROJECT: {project}", "muted"))
    if task:
        rows.append((f"TASK: {task}", "text"))
    return render_panel("ZMAI // SOFTWARE ENGINEERING AGENT", rows, theme, width)


def format_event(kind: str, msg: str, theme: Theme | None = None,
                 width: int | None = None, ts: str | None = None) -> str:
    """单行实时日志：[TRACE]/[ OK ]/[FAIL]/[ SYS ] + 时间戳 + 截断正文。"""
    t = theme or Theme.crt()
    width = width or term_width()
    stamp = ts or datetime.now().strftime("%H:%M:%S")
    tag = _EVENT_TAGS.get(kind, "INFO ")
    style = _EVENT_STYLE.get(kind, "text")
    body = str(msg)
    for prefix in ("OK: ", "FAIL: "):
        if body.startswith(prefix):
            body = body[len(prefix):]
            break
    avail = max(1, width - 18)
    return (
        t.colorize(f"  {stamp} ", "muted")
        + t.colorize(f"[{tag}]", style)
        + " "
        + t.colorize(_clip(body, avail), "text")
    )


def render_summary(
    result: dict[str, Any],
    theme: Theme | None = None,
    *,
    elapsed: float | None = None,
    budget: int | None = None,
    width: int | None = None,
) -> str:
    """结束摘要：只展示结果里真实存在的步数/改动/测试/Token。"""
    t = theme or Theme.crt()
    status = str(result.get("status") or "?")
    meta = result.get("metadata") or {}
    stats = meta.get("swe_stats") or {}
    tokens = meta.get("token_usage") or {}
    steps = result.get("steps", "N/A")
    rows: list[tuple[str, str]] = [
        (f"STATUS: {status.upper()}", "highlight" if status == "completed" else "error"),
        (f"STEPS: {steps}" + (f" / {budget}" if budget is not None else ""), "text"),
        (f"EDITS: {stats.get('edit_calls', 'N/A')}    PYTEST RUNS: {stats.get('pytest_calls', 'N/A')}", "text"),  # noqa: E501
        (f"TESTS PASSED: {meta.get('test_success_count', 'N/A')}", "text"),
        (f"TOKENS: in {tokens.get('input_tokens', 'N/A')} / out {tokens.get('output_tokens', 'N/A')}", "text"),  # noqa: E501
    ]
    if elapsed is not None:
        rows.append((f"ELAPSED: {elapsed:.1f}s", "muted"))
    return render_panel("SESSION SUMMARY", rows, t, width)


def render_keys(theme: Theme | None = None, width: int | None = None) -> str:
    """底部快捷键提示 —— 仅列出真实存在的交互。"""
    t = theme or Theme.crt()
    width = width or term_width()
    if width < 58:
        return t.colorize("  /help  /status  exit  ^C", "muted")
    key = lambda k: t.colorize(f"[ {k} ]", "info")  # noqa: E731
    lab = lambda s: t.colorize(s, "muted")  # noqa: E731
    return (
        f"  {key('/help')}{lab(' HELP')}   {key('/status')}{lab(' STATUS')}   "
        f"{key('exit/quit')}{lab(' QUIT')}   {key('^C')}{lab(' CANCEL')}"
    )


# ── 输出函数 ──────────────────────────────────────────────────

def print_json(data: Any) -> None:
    """JSON 格式输出（始终无着色）。"""
    sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def print_success(message: str, theme: Theme | None = None) -> None:
    t = theme or Theme.crt()
    sys.stdout.write(f"{t.success('+')} {message}\n")


def print_error(message: str, theme: Theme | None = None) -> None:
    t = theme or Theme.crt()
    # 只反色 [FAIL] 标签，正文保持可读（蓝色 CRT 主题下避免大面积色块）。
    sys.stderr.write(f"{t.colorize('[FAIL]', 'error')} {message}\n")


def print_warning(message: str, theme: Theme | None = None) -> None:
    t = theme or Theme.crt()
    sys.stdout.write(f"{t.warning('!')} {t.warning(message)}\n")


def print_info(message: str, theme: Theme | None = None) -> None:
    t = theme or Theme.crt()
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
