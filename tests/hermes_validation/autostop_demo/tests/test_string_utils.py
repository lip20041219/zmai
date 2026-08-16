"""string_utils 模块验证测试。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from string_utils import reverse, to_upper


def test_to_upper():
    assert to_upper("hello") == "HELLO"
    assert to_upper("ZMAI") == "ZMAI"


def test_reverse():
    assert reverse("abc") == "cba"
    assert reverse("") == ""
