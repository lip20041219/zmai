"""calculator — 简单的四则运算工具库。

注意: 本文件包含两个故意引入的 bug（add / multiply），
用于验证 ZMAI SWE Agent 的自动修复能力。
"""


def add(a, b):
    """返回 a + b。"""
    return a + b


def subtract(a, b):
    """返回 a - b。"""
    return a - b


def multiply(a, b):
    """返回 a * b。"""
    return a * b


def divide(a, b):
    """返回 a / b。"""
    if b == 0:
        raise ValueError("division by zero")
    return a / b
