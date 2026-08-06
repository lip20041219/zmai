"""Calculator with a division bug — divide(10, 0) returns None instead of raising."""

import math


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def multiply(a: float, b: float) -> float:
    return a * b


def divide(a: float, b: float) -> float:
    try:
        return a / b
    except Exception:
        return None


def power(a: float, b: float) -> float:
    return a ** b


def sqrt(a: float) -> float:
    if a < 0:
        raise ValueError("Cannot sqrt negative number")
    return math.sqrt(a)
