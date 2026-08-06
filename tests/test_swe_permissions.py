"""SWE Agent 文件权限测试。

覆盖两层：
1. 项目工具层（tools.py）：Edit/Write 在 workspace 内成功、逃逸被拒。
2. run_agent.py 无头调用层：build_claude_command 生成的权限白名单把
   Edit/Write 限定在 workspace 内，禁止外部路径 / ../ 逃逸。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from zmai.tool import ToolContext
from zmai.swe.tools import EditTool, WriteFileTool

_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "zmai_run_agent", str(_ROOT / "run_agent.py")
)
_run_agent = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_run_agent)


# ── 工具层：workspace 内读写 ────────────────────────────────────────

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def ctx(ws: Path) -> ToolContext:
    return ToolContext(agent_id="test", workspace_path=ws, timeout=10)


def test_agent_can_modify_workspace_file(ctx, ws: Path):
    """Agent 可以修改 workspace 内已有的源文件（app/test.py）。"""
    target = ws / "app" / "test.py"
    target.parent.mkdir(parents=True)
    target.write_text("def value():\n    return 1\n")

    tool = EditTool()
    result = tool.execute(ctx, {
        "path": "app/test.py",
        "mode": "regex_replace",
        "old_text": "return 1",
        "new_text": "return 2",
    })
    assert result.success, result.error
    assert "return 2" in target.read_text()


def test_agent_can_create_file(ctx, ws: Path):
    """Agent 可以在 workspace 内创建新文件。"""
    tool = WriteFileTool()
    result = tool.execute(ctx, {
        "path": "app/new_file.py",
        "content": "def hello():\n    return 'hi'\n",
    })
    assert result.success, result.error
    assert (ws / "app" / "new_file.py").exists()


def test_agent_cannot_escape_workspace(ctx, ws: Path):
    """Agent 不能通过 `../outside.py` 逃逸出 workspace。"""
    outside_dir = ws.parent / "outside"
    outside_dir.mkdir(exist_ok=True)

    tool = WriteFileTool()
    result = tool.execute(ctx, {
        "path": "../outside.py",
        "content": "malicious\n",
    })
    assert not result.success, "`../` 逃逸应被拒绝"
    assert not (outside_dir / "outside.py").exists()


# ── run_agent.py 无头调用层：权限白名单边界 ────────────────────────

def test_build_command_allows_workspace_edit_write(tmp_path: Path):
    """默认策略：Edit/Write 白名单路径限定在 workspace 内。"""
    cmd = _run_agent.build_claude_command("fix", str(tmp_path), False)
    allowed = cmd[cmd.index("--allowedTools") + 1]
    abs_ws = str(tmp_path.resolve()).replace("\\", "/")
    assert f"Edit({abs_ws}/**)" in allowed
    assert f"Write({abs_ws}/**)" in allowed
    assert "Read" in allowed
    assert "Bash(*)" in allowed
    # 不包含逃逸到外部目录的规则
    assert ".." not in allowed.split(",")[1]  # Edit 规则路径不含 `..`


def test_build_command_defaults_to_allowed_tools(tmp_path: Path):
    """默认不使用 --dangerously-skip-permissions，保留边界。"""
    cmd = _run_agent.build_claude_command("fix", str(tmp_path), False)
    assert "--dangerously-skip-permissions" not in cmd
    assert "--allowedTools" in cmd


def test_build_command_skip_permissions_flag(tmp_path: Path):
    """--skip-permissions=True 时改用 --dangerously-skip-permissions。"""
    cmd = _run_agent.build_claude_command("fix", str(tmp_path), True)
    assert "--dangerously-skip-permissions" in cmd
    assert "--allowedTools" not in cmd
