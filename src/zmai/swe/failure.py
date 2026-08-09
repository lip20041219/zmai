"""Failure Parser — 把 pytest traceback 解析成语义化的问题描述。

目标：Agent 看到的不再是裸的 ``assert 404 == 200``，而是
"首页路由不存在（404）→ 需要注册 @app.route('/')"。

这是修复能力的地基：只有先理解失败，Fix Planner 才能生成有效计划，
Agent 才能做出正确的代码修改（而不是盲目猜测）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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
    file: str = ""
    issue_type: str = ""  # NotFound / MissingField / MissingDependency …


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
]

_ERROR_TYPE_RE = re.compile(
    r"^(?P<error>AssertionError|KeyError|NameError|TypeError|ImportError|"
    r"ModuleNotFoundError|AttributeError|IndexError|ValueError|RuntimeError|"
    r"SyntaxError)\b"
)


# ═══════════════════════════════════════════════════════════════════
# 解析入口
# ═══════════════════════════════════════════════════════════════════


def _extract_test_name(text: str) -> str:
    """从 traceback 提取失败的测试名（形如 test_home_returns_200）。"""
    # 优先匹配 "::test_xxx"（pytest 失败摘要格式）
    m = re.search(r"::(?P<n>test_[A-Za-z0-9_]+)", text)
    if m:
        return m.group("n")
    m = re.search(r"\b(test_[A-Za-z0-9_]+)\.py\b", text)
    if m:
        return m.group(1)
    # 兜底：任意 test_ 单词
    m = re.search(r"\b(test_[A-Za-z0-9_]+)\b", text)
    return m.group(1) if m else ""


def _extract_error_type(text: str) -> str:
    """提取异常类型。"""
    m = _ERROR_TYPE_RE.search(text)
    return m.group("error") if m else "Error"


def _extract_file(text: str) -> str:
    """提取首个非测试文件路径（被测试的源文件，供定位用）。"""
    m = re.search(r'File "([^"]+)"', text)
    return m.group(1) if m else ""


def parse_test_failure(traceback_text: str) -> FailureIssue | None:
    """把 pytest 输出 / traceback 解析成语义化 FailureIssue。

    Args:
        traceback_text: pytest 的失败输出（含 traceback 与断言信息）。

    Returns:
        FailureIssue；无法解析（空/无失败特征）时返回 None。
    """
    if not traceback_text or not traceback_text.strip():
        return None

    detail = traceback_text.strip()[:1000]
    error_type = _extract_error_type(detail)
    test_name = _extract_test_name(detail)
    file = _extract_file(detail)

    # 用语义规则匹配；第一条命中优先
    for pattern, semantic, itype, hints in _SEMANTIC_RULES:
        m = re.search(pattern, detail, flags=re.MULTILINE)
        if m:
            groups = m.groups()
            filled = semantic.format(*groups) if groups else semantic
            return FailureIssue(
                test_name=test_name,
                error_type=error_type,
                detail=detail,
                semantic=filled,
                hints=list(hints),
                file=file,
                issue_type=itype,
            )

    # 兜底：无法精确归类，仍给一个通用语义
    return FailureIssue(
        test_name=test_name,
        error_type=error_type,
        detail=detail,
        semantic=f"测试 '{test_name or 'unknown'}' 失败（{error_type}）。"
                f"请结合 failure 详情与相关源码定位根因。",
        file=file,
        issue_type="Generic",
    )


def format_failure(issue: FailureIssue) -> str:
    """把 FailureIssue 格式化成可注入上下文的文本。"""
    lines = [f"- 失败测试: {issue.test_name or '(unknown)'}",
             f"- 错误类型: {issue.error_type}",
             f"- 语义化根因: {issue.semantic}"]
    if issue.file:
        lines.append(f"- 出错文件: {issue.file}")
    if issue.hints:
        lines.append("- 修复提示:")
        for h in issue.hints:
            lines.append(f"  · {h}")
    return "\n".join(lines)
