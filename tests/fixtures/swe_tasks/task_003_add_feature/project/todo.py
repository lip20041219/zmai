"""Todo list manager — needs a 'clear_done' feature added."""

import json
from pathlib import Path


class TodoList:
    def __init__(self, storage_path: str = "todos.json"):
        self.path = Path(storage_path)
        self.todos: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            self.todos = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.todos, indent=2), encoding="utf-8")

    def add(self, title: str) -> dict:
        todo = {"id": len(self.todos) + 1, "title": title, "done": False}
        self.todos.append(todo)
        self._save()
        return todo

    def list_all(self) -> list[dict]:
        return self.todos

    def mark_done(self, todo_id: int) -> bool:
        for t in self.todos:
            if t["id"] == todo_id:
                t["done"] = True
                self._save()
                return True
        return False

    def remove(self, todo_id: int) -> bool:
        for i, t in enumerate(self.todos):
            if t["id"] == todo_id:
                self.todos.pop(i)
                self._save()
                return True
        return False
