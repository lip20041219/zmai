"""ProjectDetector — 自动检测项目根目录、配置、Workspace。

从任何目录启动，自动找到项目根：
  1. 检查 CWD 是否有项目标记文件（.git / zmai.json / pyproject.toml 等）
  2. 没有则向上遍历父目录
  3. 到 home 目录为止
  4. 仍未找到 → chat 模式
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProjectInfo:
    mode: str  # "project" | "chat"
    root: Path | None = None
    workspace_root: Path | None = None
    config: dict[str, Any] = field(default_factory=dict)
    project_type: str = ""
    name: str = ""


_PROJECT_MARKERS = [
    ".git",
    "zmai.json",
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "setup.cfg",
    ".zmai-root",
]

_PROJECT_TYPES: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "package.json": "node",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "Gemfile": "ruby",
}


def _detect_type(root: Path) -> str:
    for filename, ptype in _PROJECT_TYPES.items():
        if (root / filename).exists():
            return ptype
    return "unknown"


def _find_root(cwd: Path | None = None) -> Path | None:
    """从 cwd 向上遍历，找到项目根目录。"""
    start = cwd or Path.cwd()
    home = Path.home()
    for parent in [start] + list(start.parents):
        if parent == home:
            break
        for marker in _PROJECT_MARKERS:
            if (parent / marker).exists():
                return parent
    return None


def _load_project_config(root: Path) -> dict[str, Any]:
    """加载项目根目录的 zmai.json。"""
    cfg_path = root / "zmai.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _resolve_workspace(root: Path, config: dict[str, Any]) -> Path:
    """解析 workspace 路径（相对路径相对于项目根）。"""
    ws = config.get("workspace", {}).get("root", "./workspace")
    if ws.startswith("./"):
        return (root / ws[2:]).resolve()
    return Path(ws).resolve()


def detect(cwd: Path | None = None) -> ProjectInfo:
    """自动检测当前环境。

    Returns:
        ProjectInfo — project 模式或 chat 模式。
    """
    root = _find_root(cwd)

    if not root:
        return ProjectInfo(mode="chat")

    config = _load_project_config(root)
    ws_root = _resolve_workspace(root, config)
    ptype = _detect_type(root)
    name = root.name

    return ProjectInfo(
        mode="project",
        root=root,
        workspace_root=ws_root,
        config=config,
        project_type=ptype,
        name=name,
    )
