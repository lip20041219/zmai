"""Edits that remove/rename public API surface get a warning, never a failure."""

from __future__ import annotations

from pathlib import Path

from zmai.swe.tools import _public_api_changes, _write_checked

_OLD = """\
class TextReporter:
    line_format = \"{path}:{line}\"

    def on_set_current_module(self, module: str) -> None:
        pass

    def _helper(self) -> None:
        pass
"""

_RENAMED = """\
class TextReporter:
    _template = \"{path}:{line}\"

    def on_set_current_module(self, module: str) -> None:
        pass

    def _helper(self) -> None:
        pass
"""


def test_public_class_attribute_rename_is_flagged():
    changes = _public_api_changes(_OLD, _RENAMED)
    assert any("TextReporter.line_format" in change for change in changes)


def test_private_rename_is_not_flagged():
    old = "class A:\n    def _x(self): pass\n"
    new = "class A:\n    def _y(self): pass\n"
    assert _public_api_changes(old, new) == []


def test_public_method_signature_and_bases_are_flagged():
    old = "class A:\n    def f(self, x): pass\n"
    new = "class A:\n    def f(self, x, y): pass\n"
    assert any("签名变化 方法 A.f" in change for change in _public_api_changes(old, new))

    old = "class A(Base):\n    pass\n"
    new = "class A:\n    pass\n"
    assert any("类 A" in change for change in _public_api_changes(old, new))


def test_edit_rename_succeeds_with_public_api_warning(tmp_path: Path):
    target = tmp_path / "mod.py"
    target.write_text(_OLD, encoding="utf-8")
    ok, message = _write_checked(target, _RENAMED, _OLD, "edit")
    assert ok is True
    assert "PUBLIC_API_CHANGE" in message
    assert "line_format" in message
    assert target.read_text(encoding="utf-8") == _RENAMED


def test_edit_private_rename_has_no_warning(tmp_path: Path):
    old = "class A:\n    def _x(self): pass\n"
    new = "class A:\n    def _y(self): pass\n"
    target = tmp_path / "mod.py"
    target.write_text(old, encoding="utf-8")
    ok, message = _write_checked(target, new, old, "edit")
    assert ok is True
    assert message == ""
