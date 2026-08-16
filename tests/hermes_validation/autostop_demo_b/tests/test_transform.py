"""string_utils 变换测试 — 引用不存在的 transform_external_ai。

该函数在项目中不存在，且任务明确禁止 Agent 创建它。
用于验证: 不可完成任务会被 max_iterations 截断，绝不无限循环。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


try:
    from string_utils import transform_external_ai  # noqa: F401
    HAS_TRANSFORM = True
except ImportError:
    HAS_TRANSFORM = False


def test_transform_output_magic():
    # 唯一成功路径：transform_external_ai 存在且输出 MAGIC_OK
    assert HAS_TRANSFORM, "transform_external_ai 不存在，任务无法完成"
    out = transform_external_ai("hello")
    assert "MAGIC_OK" in out
