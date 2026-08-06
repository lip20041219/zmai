"""EditTool 换行规范化回归测试 — 防止行拼接损坏。

回归场景（发现于 ZMAI 自动化验证）:
  LLM 调用 edit replace_lines 时 new_text 不带结尾换行
  (如 new_text="return a * b")，旧实现直接 splitlines，
  导致下一行被拼接成 `return a * bdef divide(a, b):`，
  文件产生 SyntaxError，Agent 修复失败并超时。
"""

from __future__ import annotations

import pytest

from zmai.swe.tools import EditTool, _normalize_new_lines
from zmai.tool import ToolContext


def _write(tmp_path, content: str):
    p = tmp_path / "f.py"
    p.write_text(content, encoding="utf-8")
    return p


def _ctx(tmp_path, project: str | None = None) -> ToolContext:
    return ToolContext(
        agent_id="t",
        workspace_path=tmp_path,
        project_path=project or str(tmp_path),
        config={},
        timeout=30,
    )


def test_normalize_adds_trailing_newline():
    assert _normalize_new_lines("return a * b") == ["return a * b\n"]
    assert _normalize_new_lines("a\nb") == ["a\n", "b\n"]
    assert _normalize_new_lines("a\nb\n") == ["a\n", "b\n"]
    assert _normalize_new_lines("") == []


def test_replace_lines_no_join_bug(tmp_path):
    """replace_lines 替换单行时 new_text 无结尾换行，不得拼接下一行。"""
    p = _write(tmp_path, "def add(a, b):\n    return a - b\n\n\ndef divide(a, b):\n    return a / b\n")
    tool = EditTool()
    r = tool.execute(_ctx(tmp_path), {
        "path": "f.py", "mode": "replace_lines",
        "start_line": 2, "end_line": 2,
        "new_text": "    return a + b",
    })
    assert r.success, r.error
    content = p.read_text(encoding="utf-8")
    assert "return a + b\n\n\ndef divide" in content
    assert "bdef" not in content
    # 语法必须合法
    compile(content, "f.py", "exec")


def test_insert_no_join_bug(tmp_path):
    """insert 在行间插入时 new_text 无结尾换行，不得与下一行拼接。"""
    p = _write(tmp_path, "a = 1\nb = 2\n")
    tool = EditTool()
    r = tool.execute(_ctx(tmp_path), {
        "path": "f.py", "mode": "insert",
        "start_line": 2, "new_text": "mid = 3",
    })
    assert r.success, r.error
    content = p.read_text(encoding="utf-8")
    assert "mid = 3\nb = 2" in content
    assert "3b" not in content


def test_replace_lines_empty_new_text_deletes(tmp_path):
    """new_text 为空 → 删除指定行。"""
    p = _write(tmp_path, "keep\n\ndrop\nkeep2\n")
    tool = EditTool()
    r = tool.execute(_ctx(tmp_path), {
        "path": "f.py", "mode": "replace_lines",
        "start_line": 3, "end_line": 3, "new_text": "",
    })
    assert r.success, r.error
    content = p.read_text(encoding="utf-8")
    assert "drop" not in content
    assert "keep\n\nkeep2" in content
