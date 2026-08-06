"""string_utils 模块验证测试。"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from string_utils import to_upper, reverse


def test_to_upper():
    assert to_upper("hello") == "HELLO"
    assert to_upper("ZMAI") == "ZMAI"


def test_reverse():
    assert reverse("abc") == "cba"
    assert reverse("") == ""
