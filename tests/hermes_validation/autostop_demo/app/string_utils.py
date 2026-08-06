"""string_utils — 字符串处理工具。"""


def to_upper(s):
    """返回全大写字符串。"""
    return s.lower()  # BUG: 应为 s.upper()


def reverse(s):
    """返回反转字符串。"""
    return s[::-1]
