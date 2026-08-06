"""SWE Workspace Write Permission 测试。

验证 Agent 在自身 workspace 内的写权限规则：
  - 允许：编辑 workspace 内已有文件
  - 允许：在 workspace 内创建新文件
  - 禁止：路径逃逸（绝对路径逃逸 / `../` 穿越）到 workspace 外

同时验证保留的安全限制：文件大小限制、path traversal 防护。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zmai.tool import ToolContext
from zmai.swe.tools import EditTool, WriteFileTool


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def ctx(ws: Path) -> ToolContext:
    return ToolContext(agent_id="test", workspace_path=ws, timeout=10)


def test_agent_can_edit_workspace_file(ctx, ws: Path):
    """Agent 可以编辑 workspace 内的源代码文件。"""
    target = ws / "app" / "calculator.py"
    target.parent.mkdir(parents=True)
    target.write_text("def add(a, b):\n    return a - b\n")

    tool = EditTool()
    result = tool.execute(ctx, {
        "path": "app/calculator.py",
        "mode": "regex_replace",
        "old_text": "return a - b",
        "new_text": "return a + b",
    })

    assert result.success, result.error
    assert "return a + b" in target.read_text()
    assert "return a - b" not in target.read_text()


def test_agent_can_write_new_file(ctx, ws: Path):
    """Agent 可以在 workspace 内创建新文件。"""
    tool = WriteFileTool()
    result = tool.execute(ctx, {
        "path": "app/new_module.py",
        "content": "def hello():\n    return 'hi'\n",
    })

    assert result.success, result.error
    assert (ws / "app" / "new_module.py").exists()
    assert "hello" in (ws / "app" / "new_module.py").read_text()


def test_agent_cannot_escape_workspace(ctx, ws: Path):
    """Agent 不能通过绝对路径或 `../` 逃逸出 workspace。"""
    outside_dir = ws.parent / "outside"
    outside_dir.mkdir(exist_ok=True)

    tool = WriteFileTool()

    # 绝对路径逃逸
    r_abs = tool.execute(ctx, {
        "path": str(outside_dir / "secret.txt"),
        "content": "secret",
    })
    assert not r_abs.success, "绝对路径逃逸应被拒绝"
    assert not (outside_dir / "secret.txt").exists()

    # `../` 相对路径穿越
    r_traversal = tool.execute(ctx, {
        "path": "../outside/secret2.txt",
        "content": "secret",
    })
    assert not r_traversal.success, "`../` 路径穿越应被拒绝"
    assert not (outside_dir / "secret2.txt").exists()


def test_edit_blocks_escape(ctx, ws: Path):
    """Edit 工具同样阻止 workspace 逃逸。"""
    (ws / "in.txt").write_text("ok")

    outside_dir = ws.parent / "outside"
    outside_dir.mkdir(exist_ok=True)

    tool = EditTool()
    result = tool.execute(ctx, {
        "path": "../outside/victim.txt",
        "mode": "append",
        "new_text": "pwned\n",
    })
    assert not result.success, "Edit 逃逸应被拒绝"
    assert not (outside_dir / "victim.txt").exists()


def test_oversize_content_is_rejected(ctx):
    """文件大小限制仍保留：超限写入被拒绝。"""
    tool = WriteFileTool()
    tool._MAX_WRITE_SIZE = 10  # 缩小限制以便测试
    result = tool.execute(ctx, {
        "path": "big.txt",
        "content": "x" * 100,
    })
    assert not result.success
    assert "过大" in result.error
