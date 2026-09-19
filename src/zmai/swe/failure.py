"""Failure Parser — 把 pytest traceback 解析成语义化的问题描述。

目标：Agent 看到的不再是裸的 ``assert 404 == 200``，而是
"首页路由不存在（404）→ 需要注册 @app.route('/')"。

这是修复能力的地基：只有先理解失败，Fix Planner 才能生成有效计划，
Agent 才能做出正确的代码修改（而不是盲目猜测）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from zmai.swe.verifier import strip_ansi

# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════


@dataclass
class FailureIssue:
    """一个被语义化的测试失败。

    Attributes:
        test_name: 失败的测试名（如 test_home_returns_200）。
        error_type: 异常类型（AssertionError / KeyError / ImportError …）。
        file: 出错文件（如 test_app.py），可为空。
        detail: 原始失败摘要（截断，用于审计）。
        semantic: 语义化的根因描述（人类可读）。
        hints: 可执行的修复提示列表。
    """

    test_name: str
    error_type: str
    detail: str
    semantic: str
    hints: list[str] = field(default_factory=list)
    issue_type: str = ""  # NotFound / MissingField / MissingDependency …
    # ── 根因定位字段 ──
    # file / line / function 三者来自**同一个 traceback frame**（见 _parse_frames），
    # 且优先选被测源码帧而非测试帧 —— 这是"失败 → 改哪里"的落点。
    file: str = ""
    line: int = 0
    function: str = ""
    # 定位点附近源码（±15 行，>> 标记根因行）；文件不可读时为空串。
    source_snippet: str = ""
    # ── 断言值与候选业务文件（供短路径修复）──
    expected: str = ""
    actual: str = ""
    candidate_files: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# 语义规则：traceback 特征 → 语义化问题 + 修复提示
# ═══════════════════════════════════════════════════════════════════

_SEMANTIC_RULES: list[tuple[str, str, str, list[str]]] = [
    # (正则, 语义, 问题类型, 修复提示)
    (
        r"assert\s+(\w+\.status_code)\s*==\s*200|assert\s+404\s*==\s*200"
        r"|status_code\s*==\s*200",
        "请求返回了非 200 状态码（很可能是 404 页面不存在）。"
        "对应的 HTTP 路由可能未注册，或 URL 路径不正确。",
        "NotFound",
        [
            "检查被测文件里是否注册了对应 @app.route('/...') 装饰器",
            "核对路由路径与测试请求的 URL 是否完全一致",
            "确认目标函数名与路由绑定是否正确",
        ],
    ),
    (
        r"KeyError:\s*'([^']+)'",
        "API/数据结构缺少所需字段 '{0}'。后端返回的字段名与测试期望不一致。",
        "MissingField",
        [
            "检查 JSON 响应里实际返回的字段名",
            "把字段名改成测试期望的名称，或补上缺失字段",
        ],
    ),
    (
        r"ModuleNotFoundError:\s*No module named '([^']+)'",
        "缺少依赖模块 '{0}'。运行环境未安装该包。",
        "MissingDependency",
        [
            f"运行 pip install '{0}'（或用项目 venv 安装）",
            "检查 import 语句是否拼写正确",
        ],
    ),
    (
        r"NameError:\s*name '([^']+)' is not defined",
        "代码里使用了未定义的名称 '{0}'。",
        "UndefinedName",
        [
            f"在文件顶部定义或导入 '{0}'",
            "检查变量名是否拼写错误",
        ],
    ),
    (
        r"ImportError:\s*([^\n]+)",
        "导入失败：{0}。",
        "ImportError",
        [
            "检查循环导入或缺失的符号",
            "确认 import 路径正确",
        ],
    ),
    (
        r"assert\s+([^=!<>]+)\s+not in\s+",
        "测试期望某内容『不包含』某值，但实际包含了（可能是编码/非法字符问题）。",
        "UnexpectedContent",
        [
            "检查响应/文件内容里是否有非法字符或控制字符",
            "确认编码（UTF-8）与内容是否一致",
        ],
    ),
    (
        r"TypeError:\s*([^\n]+)",
        "类型错误：{0}。",
        "TypeError",
        [
            "检查函数调用参数的类型是否匹配",
        ],
    ),
    (
        r"UnicodeDecodeError:\s*'([^']+)' codec can't decode",
        "文件读取编码错误：Windows 用 '{0}'（默认 GBK）读取 UTF-8 文件失败。",
        "EncodingError",
        [
            "在 read_text/read() 调用上显式指定 encoding='utf-8'",
            "或把源文件另存为 '{0}' 编码",
            "确认目标文件确实是 UTF-8 编码（而非 '{0}'）",
        ],
    ),
]

# 逐行匹配异常类型：pytest 的异常行是 `E   ValueError: ...`，原生 traceback 是
# `ValueError: ...`。用 `^` 单点锚（无 MULTILINE）在 ShellTool 前置了
# `[test summary]` 之后永远匹配不到位置 0，错误类型会一路回落成 "Error"。
# 只允许 pytest 的 `E ` 前缀，不放行任意前导空白 —— 避免把源码回显
# （`    raise TypeError(...)`）或普通文本里缩进出现的词误判成异常类型。
_ERROR_TYPE_RE = re.compile(
    r"^(?:E\s+)?(?P<error>AssertionError|KeyError|NameError|TypeError|ImportError|"
    r"ModuleNotFoundError|AttributeError|IndexError|ValueError|RuntimeError|"
    r"SyntaxError|UnicodeDecodeError)\b",
    re.MULTILINE,
)


# ═══════════════════════════════════════════════════════════════════
# 解析入口
# ═══════════════════════════════════════════════════════════════════


# 语义解析窗口 —— 头部（[test summary] 计数 / collection）+ 尾部（FAILURES 段、
# traceback、short test summary）。pytest 把失败详情放在输出尾部，长输出（前置噪声、
# 大量通过用例）会把失败挤出头部：只取 [:1000] 时 test_name / error_type / 语义规则
# 会一起退化成 unknown / Error / Generic。整段 ≤ 4000 时原样返回，不改变短输出行为。
# frame 与源码片段解析仍使用完整文本（见 _parse_frames），不受这里影响。
_SEMANTIC_HEAD_CHARS = 1000
_SEMANTIC_TAIL_CHARS = 3000


def _semantic_text(text: str) -> str:
    """语义解析用文本：头 + 尾，中间省略。"""
    text = (text or "").strip()
    if len(text) <= _SEMANTIC_HEAD_CHARS + _SEMANTIC_TAIL_CHARS:
        return text
    return f"{text[:_SEMANTIC_HEAD_CHARS]}\n{text[-_SEMANTIC_TAIL_CHARS:]}"


def _extract_test_name(text: str) -> str:
    """从 traceback 提取失败的测试名（形如 test_home_returns_200）。"""
    # 优先匹配 "::test_xxx"（pytest 失败摘要格式）
    m = re.search(r"::(?P<n>test_[A-Za-z0-9_]+)", text)
    if m:
        return m.group("n")
    # 其次匹配 "in test_xxx"（pytest 帧标记，如 test_app.py:40: in test_xxx）
    m = re.search(r"\bin\s+(test_[A-Za-z0-9_]+)\b", text)
    if m:
        return m.group(1)
    # 兜底：任意 test_ 单词（排除文件名 test_xxx.py）
    for m in re.finditer(r"\b(test_[A-Za-z0-9_]+)\b", text):
        if not text[m.end():m.end() + 3] == ".py":
            return m.group(1)
    return ""


def _extract_error_type(text: str) -> str:
    """提取异常类型。"""
    m = _ERROR_TYPE_RE.search(text)
    return m.group("error") if m else "Error"


def _extract_file(text: str) -> str:
    """兜底：提取首个 File "..." 路径（仅在完全没有可解析 frame 时使用）。"""
    m = re.search(r'File "([^"]+)"', text)
    return m.group(1) if m else ""


def _extract_line(text: str) -> int:
    """兜底：从形如 test_app.py:40 的片段提取行号（仅在无 frame 可用时使用）。"""
    m = re.search(r"(?:test_[A-Za-z0-9_]+\.py|/[^:\n]+\.py):(\d+)", text)
    return int(m.group(1)) if m else 0


# ═══════════════════════════════════════════════════════════════════
# Traceback frame —— file / line / function 必须来自同一个 frame
# ═══════════════════════════════════════════════════════════════════

# pytest 长 traceback 会混排三种形态（实测 pytest 9.x 输出）：
#   test_ledger.py:5:                      ← 测试帧，无函数名
#   ledger.py:28: in __init__              ← 源码帧，带函数名
#   ledger.py:46: ValueError               ← 最深帧（raise 点），只有异常名
# 加上 CPython 原生形态：
#   File "src/app.py", line 42, in parse_config


# pytest / 插件 / stdlib 的目录名（按路径段匹配）
_INTERNAL_DIR_SEGMENTS = frozenset({
    "_pytest", "pytest", "pluggy", "_pluggy",
    "importlib", "_distutils_hack",
})
# 第三方包与 stdlib 的路径特征（子串匹配）
_INTERNAL_PATH_MARKERS = (
    "site-packages", "dist-packages", "/lib/python", "/lib64/python",
)


@dataclass
class TracebackFrame:
    """一个 traceback frame —— 三者同时解析，不拆分。"""

    file: str
    line: int
    function: str = ""

    def is_test(self) -> bool:
        low = self.file.lower().replace("\\", "/")
        name = low.rsplit("/", 1)[-1]
        if "/tests/" in low or "/test/" in low or low.startswith("tests/"):
            return True
        if name == "conftest.py":
            return True
        return name.startswith("test_") or name.endswith("_test.py")

    def is_internal(self) -> bool:
        """pytest / pluggy / stdlib / site-packages 内部帧不是根因所在。"""
        low = self.file.lower().replace("\\", "/")
        if low.startswith("<"):  # <frozen importlib._bootstrap> / <string>
            return True
        # 按目录段判定，相对路径（`_pytest/python.py`）与绝对路径都能命中；
        # 不能用 startswith("_") —— 那会误伤项目里的 `_vendor/x.py`。
        segments = [seg for seg in low.split("/") if seg][:-1]
        if any(seg in _INTERNAL_DIR_SEGMENTS for seg in segments):
            return True
        return any(m in low for m in _INTERNAL_PATH_MARKERS)


# File "src/app.py", line 42, in parse_config   （引号界定路径，兼容空格/盘符）
_FRAME_FILE_RE = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.*?))?\s*$'
)
# tests/test_app.py:40: in test_button_works  （要求顶格，避开源码回显行）
_FRAME_PYTEST_RE = re.compile(
    r"^(?P<file>\S.*?\.py):(?P<line>\d+): in (?P<func>\S+)\s*$"
)
# ledger.py:46: ValueError  （raise 点：有 file:line，无函数名）
_FRAME_RAISE_RE = re.compile(
    r"^(?P<file>\S.*?\.py):(?P<line>\d+): (?P<exc>[A-Za-z_][\w.]*)\s*$"
)


def _parse_frames(text: str) -> list[TracebackFrame]:
    """按出现顺序解析全部 traceback frame。

    用**整段失败文本**而非 detail[:1000]：最深的帧（raise 点）常在切片之外。
    """
    frames: list[TracebackFrame] = []
    for raw in (text or "").splitlines():
        m = _FRAME_FILE_RE.match(raw)
        if m:
            frames.append(TracebackFrame(
                m.group("file"), int(m.group("line")), (m.group("func") or "").strip()))
            continue
        m = _FRAME_PYTEST_RE.match(raw)
        if m:
            frames.append(TracebackFrame(
                m.group("file"), int(m.group("line")), m.group("func")))
            continue
        m = _FRAME_RAISE_RE.match(raw)
        if m:
            frames.append(TracebackFrame(m.group("file"), int(m.group("line")), ""))
    return frames


def _select_frame(frames: list[TracebackFrame]) -> TracebackFrame | None:
    """选出最值得定位的 frame。

    优先级：最后一个「非测试 + 非内部」源码帧（traceback 最深 = raise 点）；
    没有源码帧时回退到测试帧（比没有定位强）；再没有就用最后一帧。
    """
    if not frames:
        return None
    source = [f for f in frames if not f.is_test() and not f.is_internal()]
    if source:
        return source[-1]
    conventional = [f for f in frames if not f.is_internal()]
    if conventional:
        return conventional[-1]
    return frames[-1]


# ═══════════════════════════════════════════════════════════════════
# 源码上下文
# ═══════════════════════════════════════════════════════════════════

SNIPPET_CONTEXT_LINES = 15
_MAX_SNIPPET_FILE_BYTES = 2 * 1024 * 1024


def _resolve_source_path(path: str, project_root: str | Path | None) -> Path | None:
    """把 traceback 里的路径解析成真实文件；解析不到返回 None。"""
    if not path:
        return None
    p = Path(path)
    candidates = [p] if p.is_absolute() else (
        ([Path(project_root) / p] if project_root else []) + [p]
    )
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _read_source_snippet(path: str, line: int,
                         project_root: str | Path | None,
                         context: int = SNIPPET_CONTEXT_LINES) -> str:
    """读取定位点前后 context 行的源码。

    行号越界会被夹紧；文件不存在/不可读/过大时返回空串——本函数只读，不写。
    """
    if not path or line <= 0:
        return ""
    target = _resolve_source_path(path, project_root)
    if target is None:
        return ""
    try:
        if target.stat().st_size > _MAX_SNIPPET_FILE_BYTES:
            return ""
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines:
        return ""
    line = min(line, len(lines))
    start = max(1, line - context)
    end = min(len(lines), line + context)
    return "\n".join(
        f"{'>>' if n == line else '  '}{n:>5} | {lines[n - 1]}"
        for n in range(start, end + 1)
    )


def _extract_expected_actual(text: str) -> tuple[str, str]:
    """提取断言的 expected / actual 数值。

    pytest 在失败摘要里输出 ``assert 6 == 5`` 这种已求值的字面量。
    约定 ``assert <actual> == <expected>``：LHS=actual、RHS=expected。
    """
    for pat in (r"assert\s+(-?\d+)\s*!=\s*(-?\d+)",
                r"assert\s+(-?\d+)\s*==\s*(-?\d+)"):
        m = re.search(pat, text)
        if m:
            return m.group(2), m.group(1)  # (expected, actual)
    return "", ""


# 测试里读取资源的常见调用，用于从测试源码推断被测业务文件
# 只保留明确的文件读取/加载 API，避免 dict.get/client.get 等误报
_PATH_CALL_RE = re.compile(
    r"(?:open|read_text|read_bytes|json\.load|import_module|include|require)"
    r"\s*\(\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_KNOWN_EXT = (".js", ".py", ".json", ".html", ".css", ".txt", ".csv",
              ".toml", ".ini", ".yaml", ".yml", ".xml", ".md")
# 任意带已知扩展名、且不含空格的引号字符串（覆盖 os.path.join(...) 拼接写法，
# 且避免把 "unbalanced braces in main.js" 这类句子误判为路径）
_QUOTED_PATH_RE = re.compile(r"['\"]([^'\"\s]+\.(?:js|json|html|css|py|txt|csv|toml|ini|ya?ml|xml|md))['\"]",  # noqa: E501
                             re.IGNORECASE)


def _candidate_paths_from_source(test_src: str) -> list[str]:
    """从测试源码里抽取它读取/加载的资源路径（即候选被测业务文件）。"""
    out: list[str] = []
    for m in _PATH_CALL_RE.finditer(test_src):
        s = m.group(1).strip().strip("'\"")
        if not s:
            continue
        if s.endswith(_KNOWN_EXT) or "/" in s or "\\" in s or s.startswith("."):
            if s not in out:
                out.append(s)
    # 兜底：捕捉 os.path.join("static","js","main.js") 这类拼接里的文件名
    for m in _QUOTED_PATH_RE.finditer(test_src):
        s = m.group(1)
        if s not in out:
            out.append(s)
    return out


def _collect_candidate_files(text: str, project_root: str | Path | None,
                             test_name: str) -> list[str]:
    """推断最可能的被测业务文件（供 Agent 优先读取/修复）。

    优先级：
      1. traceback 里出现的非测试源码文件（File "..." 帧）
      2. 失败测试源码里显式 open/read 的资源路径
      3. 去重、过滤测试文件与明显非源码文件
    """
    cands: list[str] = []

    # 1) traceback 中的 File "..." 帧（排除测试文件）
    for m in re.finditer(r'File "([^"]+)"', text):
        f = m.group(1)
        low = f.lower().replace("\\", "/")
        if ("/tests/" in low or low.split("/")[-1].startswith("test_")
                or low.split("/")[-1].endswith("_test.py") or "/conftest" in low):
            continue
        cands.append(f)

    # 2) 读取失败测试的源码，抽取资源路径
    if project_root and test_name:
        try:
            root = Path(project_root)
            test_file = None
            for candidate in root.rglob("test_*.py"):
                if "test_" not in candidate.name and "_test.py" not in candidate.name:
                    continue
                body = candidate.read_text(encoding="utf-8", errors="replace")
                # 粗略判断该测试文件是否含此测试函数
                if f"def {test_name}" in body or test_name in body:
                    test_file = candidate
                    break
            if test_file is not None:
                body = test_file.read_text(encoding="utf-8", errors="replace")
                for p in _candidate_paths_from_source(body):
                    low = p.replace("\\", "/")
                    if (low.startswith("tests/") or low.endswith(("test_.py", "_test.py"))):
                        continue
                    cands.append(p)
        except Exception:
            pass

    # 去重保序
    seen: set[str] = set()
    result: list[str] = []
    for c in cands:
        norm = c.replace("\\", "/")
        if norm not in seen:
            seen.add(norm)
            result.append(c)
    return result


def parse_test_failure(traceback_text: str,
                       project_root: str | Path | None = None) -> FailureIssue | None:
    """把 pytest 输出 / traceback 解析成语义化 FailureIssue。

    Args:
        traceback_text: pytest 的失败输出（含 traceback 与断言信息）。
        project_root: 可选的项目根路径。提供时，额外推断候选业务文件
            （如测试读取的 static/js/main.js），供 Agent 走"短路径"修复。

    Returns:
        FailureIssue；无法解析（空/无失败特征）时返回 None。
    """
    if not traceback_text or not traceback_text.strip():
        return None
    # 先剥 ANSI：彩色输出（FORCE_COLOR / PY_COLORS / --color=yes）会把转义插进
    # `app.py:2: KeyError` 这类语义单元中间，使 frame / 异常类型正则全部失配。
    # 入口统一剥离，下游 frame / 语义 / 源码片段解析无需各自处理。
    traceback_text = strip_ansi(traceback_text)

    # 语义解析（测试名/异常类型/规则/期望值）用头+尾窗口：失败详情在输出尾部，
    # 长输出下头部只有噪声。
    detail = _semantic_text(traceback_text)
    error_type = _extract_error_type(detail)
    test_name = _extract_test_name(detail)
    expected, actual = _extract_expected_actual(detail)
    candidate_files = _collect_candidate_files(detail, project_root, test_name)

    # ── 根因定位：frame 三要素 + 源码上下文 ──
    # 解析整段文本（最深的 raise 帧常在头部窗口之外），选中帧后读源码。
    frame = _select_frame(_parse_frames(traceback_text))
    if frame is not None:
        file, line, function = frame.file, frame.line, frame.function
    else:
        # 无任何可识别 frame 时退回旧行为，保证不回归。
        file, line, function = _extract_file(detail), _extract_line(detail), ""
    source_snippet = _read_source_snippet(file, line, project_root)

    def _build(itype: str, semantic: str, hints: list[str]) -> FailureIssue:
        return FailureIssue(
            test_name=test_name,
            error_type=error_type,
            detail=detail,
            semantic=semantic,
            hints=list(hints),
            file=file,
            issue_type=itype,
            line=line,
            function=function,
            source_snippet=source_snippet,
            expected=expected,
            actual=actual,
            candidate_files=candidate_files,
        )

    # 用语义规则匹配；第一条命中优先
    for pattern, semantic, itype, hints in _SEMANTIC_RULES:
        m = re.search(pattern, detail, flags=re.MULTILINE)
        if m:
            groups = m.groups()
            filled = semantic.format(*groups) if groups else semantic
            return _build(itype, filled, hints)

    # 兜底：无法精确归类，仍给一个通用语义
    return _build(
        "Generic",
        f"测试 '{test_name or 'unknown'}' 失败（{error_type}）。"
        f"请结合 failure 详情与相关源码定位根因。",
        [],
    )


def format_failure(issue: FailureIssue) -> str:
    """把 FailureIssue 格式化成可注入上下文的文本。"""
    lines = [f"- 失败测试: {issue.test_name or '(unknown)'}",
             f"- 错误类型: {issue.error_type}",
             f"- 语义化根因: {issue.semantic}"]
    if issue.file:
        loc = f"- 根因位置: {issue.file}:{issue.line}" if issue.line else \
              f"- 根因位置: {issue.file}"
        if issue.function:
            loc += f"  (in {issue.function})"
        lines.append(loc)
    if issue.source_snippet:
        lines.append("- 源码上下文（>> 标记根因行）:")
        lines.append(issue.source_snippet)
    if issue.expected or issue.actual:
        lines.append(f"- 断言值: expected={issue.expected}, actual={issue.actual}")
    if issue.candidate_files:
        lines.append("- 最可能的被测业务文件（优先读取/修改）:")
        for c in issue.candidate_files[:5]:
            lines.append(f"  · {c}")
    if issue.hints:
        lines.append("- 修复提示:")
        for h in issue.hints:
            lines.append(f"  · {h}")
    return "\n".join(lines)
