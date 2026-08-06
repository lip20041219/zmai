"""generate_by_zmai.py — ZMAI 通过自身 API 生成示例文件。

此脚本由 ZMAI 的 Workspace API 执行文件创建，
展示 ZMAI 如何用自身工具管理代码生成流程。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

FIB_CONTENT = '''\
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
'''


async def main() -> None:
    from zmai.workspace import Workspace

    ws = Workspace(root="./workspace")
    agent_id = "zmai_generated"

    # 使用 ZMAI Workspace API 创建文件
    ws.prepare(agent_id)
    ws.write_text(agent_id, "output/fibonacci.py", FIB_CONTENT)

    # 读取验证
    content = ws.read_text(agent_id, "output/fibonacci.py")
    print(f"  [ZMAI Workspace] 已生成: workspace/{agent_id}/output/fibonacci.py")
    print(f"  文件大小: {len(content)} 字符, {len(content.splitlines())} 行")

    # 复制到 examples/
    import shutil
    from pathlib import Path

    src = Path("workspace") / agent_id / "output" / "fibonacci.py"
    dst = Path(__file__).resolve().parent / "fibonacci.py"
    shutil.copy2(str(src), str(dst))
    print(f"  复制到: {dst}")

    # 清理 workspace
    ws.cleanup(agent_id, keep_output=False)
    print()

    # 展示内容摘要
    print("  内容摘要:")
    for line in content.splitlines()[:10]:
        print(f"    {line}")
    print("    ...")


if __name__ == "__main__":
    asyncio.run(main())
