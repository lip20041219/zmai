"""fibonacci.py — 斐波那契数列示例，由 ZMAI 生成。

用法:
    python examples/fibonacci.py 10

输出:
    fibonacci(10) = 55
"""

import sys


def fibonacci(n: int) -> int:
    """返回斐波那契数列的第 n 项（0-indexed）。

    Args:
        n: 非负整数。

    Returns:
        第 n 个斐波那契数。

    Examples:
        >>> fibonacci(0)
        0
        >>> fibonacci(1)
        1
        >>> fibonacci(10)
        55
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <n>")
        print(f"Example: python {sys.argv[0]} 10")
        sys.exit(1)
    try:
        n = int(sys.argv[1])
    except ValueError:
        print(f"Error: n must be an integer, got {sys.argv[1]}")
        sys.exit(1)
    result = fibonacci(n)
    print(f"fibonacci({n}) = {result}")


if __name__ == "__main__":
    main()
