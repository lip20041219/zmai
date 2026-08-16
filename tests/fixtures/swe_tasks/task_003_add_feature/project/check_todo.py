"""Tests for TodoList — includes a test for the missing clear_done feature."""

from pathlib import Path

from todo import TodoList


def test_add_and_list(tmp_path: Path):
    storage = str(tmp_path / "todos.json")
    t = TodoList(storage)
    t.add("Buy milk")
    t.add("Write code")
    items = t.list_all()
    assert len(items) == 2
    assert items[0]["title"] == "Buy milk"
    assert items[0]["done"] is False


def test_mark_done(tmp_path: Path):
    storage = str(tmp_path / "todos.json")
    t = TodoList(storage)
    t.add("Task 1")
    assert t.mark_done(1) is True
    items = t.list_all()
    assert items[0]["done"] is True


def test_remove(tmp_path: Path):
    storage = str(tmp_path / "todos.json")
    t = TodoList(storage)
    t.add("Task 1")
    t.add("Task 2")
    assert t.remove(1) is True
    items = t.list_all()
    assert len(items) == 1
    assert items[0]["title"] == "Task 2"


def test_clear_done(tmp_path: Path):
    """clear_done() should remove all completed todos."""
    storage = str(tmp_path / "todos.json")
    t = TodoList(storage)
    t.add("Task 1")
    t.add("Task 2")
    t.mark_done(1)
    t.clear_done()
    items = t.list_all()
    assert len(items) == 1
    assert items[0]["title"] == "Task 2"


def test_clear_done_no_done(tmp_path: Path):
    """clear_done() with no done items should not remove anything."""
    storage = str(tmp_path / "todos.json")
    t = TodoList(storage)
    t.add("Task 1")
    t.add("Task 2")
    t.clear_done()
    items = t.list_all()
    assert len(items) == 2
