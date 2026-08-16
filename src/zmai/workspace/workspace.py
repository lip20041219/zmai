"""Workspace Manager — Agent 隔离文件系统沙箱。

每个 Agent 拥有独立的工作目录，包含 input/、output/、temp/、.state/ 子目录。
提供路径穿越防护、文件大小限制、磁盘空间检查等安全机制。
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zmai.errors import WorkspaceError

logger = logging.getLogger("zmai.workspace")

# ── 文件类型分类 ──────────────────────────────────────────────

FILE_CATEGORIES: dict[str, str] = {
    # 任务文件
    ".txt": "task",
    ".todo": "task",
    ".task": "task",
    # 数据库
    ".db": "database",
    ".sqlite": "database",
    ".sqlite3": "database",
    ".json": "database",
    # Markdown
    ".md": "markdown",
    ".markdown": "markdown",
    # PDF
    ".pdf": "pdf",
    # Prompt
    ".prompt": "prompt",
    ".jinja": "prompt",
    ".jinja2": "prompt",
    # 配置
    ".yaml": "config",
    ".yml": "config",
    ".toml": "config",
    ".ini": "config",
    ".cfg": "config",
    ".env": "config",
    # 代码
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".jsx": "code",
    ".tsx": "code",
    ".java": "code",
    ".go": "code",
    ".rs": "code",
    ".cpp": "code",
    ".c": "code",
    ".h": "code",
    ".hpp": "code",
    ".cs": "code",
    ".rb": "code",
    ".php": "code",
    ".swift": "code",
    ".kt": "code",
    ".scala": "code",
    ".sh": "code",
    ".bash": "code",
    ".zsh": "code",
    ".ps1": "code",
    ".bat": "code",
    ".cmd": "code",
    ".sql": "code",
    ".r": "code",
    ".m": "code",
    ".dart": "code",
    ".lua": "code",
    # 图片
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".svg": "image",
    ".webp": "image",
    ".ico": "image",
    ".bmp": "image",
    ".tiff": "image",
    ".avif": "image",
}

# ── 数据类型 ──────────────────────────────────────────────────


@dataclass
class FileEntry:
    """工作区中的单个文件记录。"""

    path: str  # 相对于 agent 工作目录的路径
    size: int  # 文件大小（字节）
    category: str  # 文件分类
    mime_type: str  # MIME 类型
    created_at: str  # ISO8601
    updated_at: str  # ISO8601
    checksum: str | None = None  # SHA256（可选）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkspaceManifest:
    """Agent 工作区的文件清单。"""

    agent_id: str
    files: list[FileEntry] = field(default_factory=list)
    total_size: int = 0
    file_count: int = 0
    updated_at: str = ""

    def add_file(self, entry: FileEntry) -> None:
        self.files.append(entry)
        self.file_count = len(self.files)
        self.total_size = sum(f.size for f in self.files)
        self.updated_at = _now_iso()

    def remove_file(self, path: str) -> None:
        self.files = [f for f in self.files if f.path != path]
        self.file_count = len(self.files)
        self.total_size = sum(f.size for f in self.files)
        self.updated_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "files": [f.to_dict() for f in self.files],
            "total_size": self.total_size,
            "file_count": self.file_count,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceManifest:
        manifest = cls(agent_id=data["agent_id"])
        manifest.files = [FileEntry(**f) for f in data.get("files", [])]
        manifest.total_size = data.get("total_size", 0)
        manifest.file_count = data.get("file_count", 0)
        manifest.updated_at = data.get("updated_at", "")
        return manifest


@dataclass
class AgentWorkspaceState:
    """单个 Agent 的工作区状态。"""

    agent_id: str
    status: str  # "active" | "inactive" | "completed" | "failed"
    created_at: str
    updated_at: str
    file_count: int = 0
    total_size: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentWorkspaceState:
        return cls(**data)


@dataclass
class GlobalWorkspaceState:
    """全局工作区状态。"""

    version: str = "1.0"
    workspaces: dict[str, AgentWorkspaceState] = field(default_factory=dict)
    total_workspaces: int = 0
    active_workspaces: int = 0
    updated_at: str = ""

    def update_agent(self, state: AgentWorkspaceState) -> None:
        self.workspaces[state.agent_id] = state
        self.total_workspaces = len(self.workspaces)
        self.active_workspaces = sum(
            1 for w in self.workspaces.values() if w.status == "active"
        )
        self.updated_at = _now_iso()

    def remove_agent(self, agent_id: str) -> None:
        self.workspaces.pop(agent_id, None)
        self.total_workspaces = len(self.workspaces)
        self.active_workspaces = sum(
            1 for w in self.workspaces.values() if w.status == "active"
        )
        self.updated_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "workspaces": {k: v.to_dict() for k, v in self.workspaces.items()},
            "total_workspaces": self.total_workspaces,
            "active_workspaces": self.active_workspaces,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalWorkspaceState:
        state = cls(version=data.get("version", "1.0"))
        state.workspaces = {
            k: AgentWorkspaceState.from_dict(v)
            for k, v in data.get("workspaces", {}).items()
        }
        state.total_workspaces = data.get("total_workspaces", 0)
        state.active_workspaces = data.get("active_workspaces", 0)
        state.updated_at = data.get("updated_at", "")
        return state


# ── 帮助函数 ──────────────────────────────────────────────────


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _classify_file(path: str) -> str:
    """根据文件扩展名返回文件分类。"""
    p = Path(path)
    ext = p.suffix.lower()
    # 处理 .env 这类文件名就是扩展名的情况（Python 3.13+ 行为差异）
    if not ext and p.name.startswith("."):
        ext = p.name.lower()
    return FILE_CATEGORIES.get(ext, "other")


def _guess_mime(path: str) -> str:
    """根据扩展名猜测 MIME 类型。"""
    ext = Path(path).suffix.lower()
    mime_map: dict[str, str] = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".json": "application/json",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".toml": "text/toml",
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".ts": "text/typescript",
        ".jsx": "text/jsx",
        ".tsx": "text/tsx",
        ".java": "text/x-java",
        ".go": "text/x-go",
        ".rs": "text/x-rust",
        ".cpp": "text/x-c++",
        ".c": "text/x-c",
        ".h": "text/x-c-header",
        ".html": "text/html",
        ".css": "text/css",
        ".sh": "text/x-shellscript",
        ".bash": "text/x-shellscript",
        ".ps1": "text/x-powershell",
        ".bat": "text/x-batch",
        ".csv": "text/csv",
        ".xml": "text/xml",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".avif": "image/avif",
        ".db": "application/octet-stream",
        ".sqlite": "application/octet-stream",
        ".sqlite3": "application/octet-stream",
        ".log": "text/plain",
        ".env": "text/plain",
        ".cfg": "text/plain",
        ".ini": "text/plain",
    }
    return mime_map.get(ext, "application/octet-stream")


# ── 默认配置 ──────────────────────────────────────────────────

DEFAULT_WORKSPACE_CONFIG: dict[str, Any] = {
    "root": "./workspace",
    "max_file_size": 10 * 1024 * 1024,  # 10 MB
    "max_files": 1000,
    "min_disk_space": 100 * 1024 * 1024,  # 100 MB
    "cleanup_temp": True,
    "cleanup_output": False,
    "cleanup_input": False,
}


# ── Workspace 主类 ────────────────────────────────────────────


class Workspace:
    """Workspace 管理器。

    为 Agent 提供隔离的文件系统沙箱。每个 Agent 拥有独立的工作目录，
    包含 input/、output/、temp/、.state/ 四个子目录。

    使用方式:
        workspace = Workspace(root="./workspace")
        agent_path = workspace.prepare("agent_123")
        workspace.write("agent_123", "output/result.md", b"# Result")
        data = workspace.read("agent_123", "output/result.md")
        workspace.cleanup("agent_123")
    """

    def __init__(
        self,
        root: str | Path = "./workspace",
        config: dict[str, Any] | None = None,
    ) -> None:
        """初始化 Workspace 管理器。

        Args:
            root: 工作区根目录路径。
            config: 配置字典，合并到默认配置之上。

        Raises:
            WorkspaceError: 根目录不可写时抛出。
        """
        self._root = Path(root).resolve()
        self._config: dict[str, Any] = {**DEFAULT_WORKSPACE_CONFIG, **(config or {})}
        self._locks: dict[str, threading.Lock] = {}
        self._lock_lock = threading.Lock()

        # 确保根目录存在
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise WorkspaceError(
                f"无法创建工作区根目录: {e}",
                path=str(self._root),
            ) from e

        # 检查根目录可写：实际写入探测。
        # 仅用 os.access 并不可靠 —— Windows/管理员下 ACL 可能被绕过，
        # mkdir 与 os.access 都返回可写，但真正写文件才抛 PermissionError。
        probe = self._root / ".zmai_write_probe"
        try:
            probe.write_bytes(b"")
        except OSError as e:
            raise WorkspaceError(
                f"工作区根目录不可写: {e}",
                path=str(self._root),
            ) from e
        finally:
            try:
                probe.unlink()
            except OSError:
                pass

        # 全局状态文件
        self._global_state_path = self._root / "state.json"
        self._global_manifest_path = self._root / "manifest.json"

        # 加载或初始化全局状态
        self._global_state = self._load_global_state()

        # 写入初始状态文件（如果尚未存在）
        if not self._global_state_path.exists():
            self._save_global_state()
        if not self._global_manifest_path.exists():
            self._write_json(self._global_manifest_path, {
                "version": "1.0",
                "agents": [],
                "total_agents": 0,
                "total_files": 0,
                "total_size": 0,
                "updated_at": _now_iso(),
            })

        logger.info("Workspace 初始化完成, root=%s", self._root)

    # ── Agent 工作区生命周期 ──────────────────────────────

    def prepare(self, agent_id: str) -> Path:
        """准备 Agent 的工作区。

        创建 Agent 的工作目录和子目录结构。

        Args:
            agent_id: Agent 唯一标识。

        Returns:
            Agent 工作目录的绝对路径。

        Raises:
            WorkspaceError: 目录创建失败时抛出。
        """
        agent_path = self._agent_path(agent_id)

        if agent_path.exists():
            logger.warning("Agent 工作区已存在: %s", agent_path)
            self._update_agent_state(agent_id, "active")
            return agent_path

        try:
            agent_path.mkdir(parents=True, exist_ok=True)
            (agent_path / "input").mkdir(exist_ok=True)
            (agent_path / "output").mkdir(exist_ok=True)
            (agent_path / "temp").mkdir(exist_ok=True)
            (agent_path / ".state").mkdir(exist_ok=True)
        except OSError as e:
            raise WorkspaceError(
                f"创建 Agent 工作区失败: {e}",
                path=str(agent_path),
            ) from e

        # 初始化 Agent 状态
        state = AgentWorkspaceState(
            agent_id=agent_id,
            status="active",
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        self._write_json(agent_path / ".state" / "state.json", state.to_dict())

        # 初始化 Agent manifest
        manifest = WorkspaceManifest(agent_id=agent_id, updated_at=_now_iso())
        self._write_json(agent_path / ".state" / "manifest.json", manifest.to_dict())

        # 更新全局状态
        self._update_agent_state(agent_id, "active")

        logger.info("Agent 工作区已创建: %s", agent_id)
        return agent_path

    def cleanup(
        self,
        agent_id: str,
        *,
        keep_output: bool = True,
        keep_input: bool = False,
    ) -> None:
        """清理 Agent 的工作区。

        Args:
            agent_id: Agent 唯一标识。
            keep_output: 是否保留 output/ 目录。
            keep_input: 是否保留 input/ 目录。

        Raises:
            WorkspaceError: 清理失败时抛出。
        """
        agent_path = self._agent_path(agent_id)
        if not agent_path.exists():
            logger.warning("Agent 工作区不存在，跳过清理: %s", agent_id)
            return

        try:
            def _clear_contents(dir_path: Path) -> None:
                """删除目录下所有内容但保留目录本身。"""
                for item in dir_path.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()

            # 清理 temp/
            temp_dir = agent_path / "temp"
            if temp_dir.exists():
                _clear_contents(temp_dir)

            # 清理 input/
            if not keep_input:
                input_dir = agent_path / "input"
                if input_dir.exists():
                    _clear_contents(input_dir)

            # 清理 output/
            if not keep_output:
                output_dir = agent_path / "output"
                if output_dir.exists():
                    _clear_contents(output_dir)

            # 更新状态
            status = "completed" if keep_output else "inactive"
            self._update_agent_state(agent_id, status)

            # 写本地状态
            state = AgentWorkspaceState(
                agent_id=agent_id,
                status=status,
                created_at="",
                updated_at=_now_iso(),
            )
            self._write_json(agent_path / ".state" / "state.json", state.to_dict())

            logger.info("Agent 工作区已清理: %s", agent_id)

        except OSError as e:
            raise WorkspaceError(
                f"清理 Agent 工作区失败: {e}",
                path=str(agent_path),
            ) from e

    def remove(self, agent_id: str) -> None:
        """完全删除 Agent 的工作区。

        Args:
            agent_id: Agent 唯一标识。

        Raises:
            WorkspaceError: 删除失败时抛出。
        """
        agent_path = self._agent_path(agent_id)
        if not agent_path.exists():
            self._global_state.remove_agent(agent_id)
            self._save_global_state()
            return

        try:
            shutil.rmtree(agent_path)
            self._global_state.remove_agent(agent_id)
            self._save_global_state()
            logger.info("Agent 工作区已删除: %s", agent_id)
        except OSError as e:
            raise WorkspaceError(
                f"删除 Agent 工作区失败: {e}",
                path=str(agent_path),
            ) from e

    # ── 文件操作 ──────────────────────────────────────────

    def read(self, agent_id: str, path: str) -> bytes:
        """读取 Agent 工作区中的文件。"""
        full_path = self._validate_path(agent_id, path)
        if not full_path.exists():
            raise WorkspaceError(
                f"文件不存在: {path}",
                path=str(full_path),
            )
        if not full_path.is_file():
            raise WorkspaceError(
                f"路径不是文件: {path}",
                path=str(full_path),
            )

        lock = self._get_lock(agent_id)
        with lock:
            try:
                return full_path.read_bytes()
            except OSError as e:
                raise WorkspaceError(
                    f"读取文件失败: {e}",
                    path=str(full_path),
                ) from e

    def read_text(
        self, agent_id: str, path: str, encoding: str = "utf-8"
    ) -> str:
        """以文本形式读取文件。

        Args:
            agent_id: Agent 唯一标识。
            path: 相对于 Agent 工作目录的文件路径。
            encoding: 文件编码，默认 utf-8。

        Returns:
            文件内容（文本）。
        """
        data = self.read(agent_id, path)
        return data.decode(encoding)

    def write(
        self,
        agent_id: str,
        path: str,
        data: bytes,
        *,
        update_manifest: bool = True,
    ) -> Path:
        """向 Agent 工作区写入文件。

        自动创建父目录。记录文件到 manifest。

        Args:
            agent_id: Agent 唯一标识。
            path: 相对于 Agent 工作目录的文件路径。
            data: 文件内容（二进制）。
            update_manifest: 是否更新文件清单。

        Returns:
            写入文件的绝对路径。

        Raises:
            WorkspaceError: 文件大小超限、磁盘空间不足、路径非法时抛出。
        """
        full_path = self._validate_path(agent_id, path)

        # 检查文件大小
        if len(data) > self._config["max_file_size"]:
            raise WorkspaceError(
                f"文件大小超过限制: {len(data)} > {self._config['max_file_size']} 字节",
                path=path,
            )

        # 检查磁盘空间
        self._check_disk_space()

        # 获取 Agent 锁
        lock = self._get_lock(agent_id)

        with lock:
            try:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_bytes(data)

                # 检查文件总数
                if update_manifest:
                    agent_path = self._agent_path(agent_id)
                    file_count = len(list(agent_path.rglob("*")))
                    # 排除目录
                    file_count = len(
                        [p for p in agent_path.rglob("*") if p.is_file()]
                    )
                    # 减去 .state/ 中的文件
                    state_file_count = len(
                        list((agent_path / ".state").rglob("*"))
                    ) if (agent_path / ".state").exists() else 0
                    actual_file_count = file_count - state_file_count

                    if actual_file_count > self._config["max_files"]:
                        full_path.unlink()
                        raise WorkspaceError(
                            f"文件数量超过限制: {actual_file_count} > {self._config['max_files']}",
                            path=path,
                        )

                    # 更新 manifest
                    self._update_manifest(agent_id, full_path, data)

            except OSError as e:
                raise WorkspaceError(
                    f"写入文件失败: {e}",
                    path=str(full_path),
                ) from e

        logger.debug("文件已写入: %s (%d 字节)", path, len(data))
        return full_path

    def write_text(
        self,
        agent_id: str,
        path: str,
        text: str,
        encoding: str = "utf-8",
        *,
        update_manifest: bool = True,
    ) -> Path:
        """以文本形式写入文件。

        Args:
            agent_id: Agent 唯一标识。
            path: 相对于 Agent 工作目录的文件路径。
            text: 文本内容。
            encoding: 文件编码，默认 utf-8。
            update_manifest: 是否更新文件清单。

        Returns:
            写入文件的绝对路径。
        """
        return self.write(agent_id, path, text.encode(encoding), update_manifest=update_manifest)

    def list(
        self,
        agent_id: str,
        pattern: str = "**/*",
        *,
        include_state: bool = False,
    ) -> list[Path]:
        """列出 Agent 工作区中的文件。"""
        agent_path = self._agent_path(agent_id)
        if not agent_path.exists():
            return []

        lock = self._get_lock(agent_id)
        with lock:
            paths: list[Path] = []
            for p in agent_path.glob(pattern):
                if not p.is_file():
                    continue
                if not include_state and ".state" in p.parts:
                    continue
                rel = p.relative_to(agent_path)
                paths.append(rel)

        return sorted(paths)

    def exists(self, agent_id: str, path: str) -> bool:
        """检查 Agent 工作区中的文件或目录是否存在。"""
        try:
            full_path = self._validate_path(agent_id, path)
            lock = self._get_lock(agent_id)
            with lock:
                return full_path.exists()
        except WorkspaceError:
            return False

    def delete(self, agent_id: str, path: str) -> None:
        """删除 Agent 工作区中的文件。

        Args:
            agent_id: Agent 唯一标识。
            path: 相对于 Agent 工作目录的文件路径。

        Raises:
            WorkspaceError: 文件不存在或删除失败时抛出。
        """
        full_path = self._validate_path(agent_id, path)
        if not full_path.exists():
            raise WorkspaceError(
                f"文件不存在: {path}",
                path=str(full_path),
            )

        lock = self._get_lock(agent_id)
        with lock:
            try:
                if full_path.is_file():
                    full_path.unlink()
                else:
                    shutil.rmtree(full_path)

                # 更新 manifest
                self._remove_from_manifest(agent_id, path)
            except OSError as e:
                raise WorkspaceError(
                    f"删除文件失败: {e}",
                    path=str(full_path),
                ) from e

    # ── 目录路径获取 ──────────────────────────────────────

    def agent_path(self, agent_id: str) -> Path:
        """获取 Agent 工作目录的绝对路径。

        Args:
            agent_id: Agent 唯一标识。
        """
        return self._agent_path(agent_id)

    def input_dir(self, agent_id: str) -> Path:
        """获取 Agent 的 input/ 目录路径。"""
        return self._agent_path(agent_id) / "input"

    def output_dir(self, agent_id: str) -> Path:
        """获取 Agent 的 output/ 目录路径。"""
        return self._agent_path(agent_id) / "output"

    def temp_dir(self, agent_id: str) -> Path:
        """获取 Agent 的 temp/ 目录路径。"""
        return self._agent_path(agent_id) / "temp"

    def state_dir(self, agent_id: str) -> Path:
        """获取 Agent 的 .state/ 目录路径。"""
        return self._agent_path(agent_id) / ".state"

    # ── Manifest 和 State 查询 ────────────────────────────

    def get_manifest(self, agent_id: str) -> WorkspaceManifest | None:
        """获取 Agent 的文件清单。

        Args:
            agent_id: Agent 唯一标识。

        Returns:
            文件清单，不存在时返回 None。
        """
        manifest_path = self._agent_path(agent_id) / ".state" / "manifest.json"
        if not manifest_path.exists():
            return None
        data = self._read_json(manifest_path)
        return WorkspaceManifest.from_dict(data) if data else None

    def get_state(self, agent_id: str) -> AgentWorkspaceState | None:
        """获取 Agent 的工作区状态。

        Args:
            agent_id: Agent 唯一标识。

        Returns:
            Agent 工作区状态，不存在时返回 None。
        """
        agent_state = self._global_state.workspaces.get(agent_id)
        if agent_state:
            return agent_state

        state_path = self._agent_path(agent_id) / ".state" / "state.json"
        if not state_path.exists():
            return None
        data = self._read_json(state_path)
        return AgentWorkspaceState.from_dict(data) if data else None

    def get_global_state(self) -> GlobalWorkspaceState:
        """获取全局工作区状态。"""
        return self._global_state

    def get_global_manifest(self) -> dict[str, Any]:
        """获取全局清单（所有 Agent 的文件汇总）。"""
        manifest_path = self._global_manifest_path
        if not manifest_path.exists():
            return {
                "version": "1.0",
                "agents": [],
                "total_agents": 0,
                "total_files": 0,
                "total_size": 0,
                "updated_at": _now_iso(),
            }
        return self._read_json(manifest_path) or {}

    def list_agents(self) -> list[str]:
        """列出所有有工作区的 Agent ID。"""
        if not self._root.exists():
            return []
        return sorted(
            d.name
            for d in self._root.iterdir()
            if d.is_dir()
            and d.name != ".state"
            and not d.name.startswith(".")
            and (d / ".state" / "state.json").exists()
        )

    # ── 内部方法 ──────────────────────────────────────────

    def _agent_path(self, agent_id: str) -> Path:
        """获取 Agent 工作目录路径。"""
        # 校验 agent_id 防止路径穿越
        if "/" in agent_id or "\\" in agent_id or ".." in agent_id:
            raise WorkspaceError(
                "非法的 Agent ID",
                path=agent_id,
            )
        return self._root / agent_id

    def _validate_path(self, agent_id: str, path: str) -> Path:
        """校验路径合法性，防止路径穿越。

        使用 pathlib 的路径语义而非字符串前缀匹配，
        避免 /ws/agent_1-secret/ 被误认为 /ws/agent_1/ 的子路径。

        Args:
            agent_id: Agent 唯一标识。
            path: 相对于 Agent 工作目录的路径。

        Returns:
            解析后的绝对路径。

        Raises:
            WorkspaceError: 路径试图逃逸到工作区之外时抛出。
        """
        agent_path = self._agent_path(agent_id)
        target = (agent_path / path).resolve()
        agent_path_resolved = agent_path.resolve()

        # 路径穿越检测：target 必须在 agent_path 下（pathlib 路径语义）
        # 这比 str.startswith 更严格：/ws/a/ 和 /ws/a-extra/ 是不同目录
        try:
            target.relative_to(agent_path_resolved)
        except ValueError:
            raise WorkspaceError(
                f"路径穿越被拒绝: {path}",
                path=str(target),
            )

        # agent_path 必须在 workspace root 下
        try:
            agent_path_resolved.relative_to(self._root)
        except ValueError:
            raise WorkspaceError(
                "Agent 工作区必须在 workspace root 下",
                path=str(agent_path),
            )

        return target

    def _check_disk_space(self) -> None:
        """检查磁盘剩余空间。"""
        try:
            usage = shutil.disk_usage(str(self._root))
            if usage.free < self._config["min_disk_space"]:
                raise WorkspaceError(
                    f"磁盘空间不足: {usage.free} < {self._config['min_disk_space']} 字节",
                )
        except WorkspaceError:
            raise
        except OSError as e:
            raise WorkspaceError(f"检查磁盘空间失败: {e}") from e

    def _get_lock(self, agent_id: str) -> threading.Lock:
        """获取 Agent 的线程锁。"""
        with self._lock_lock:
            if agent_id not in self._locks:
                self._locks[agent_id] = threading.Lock()
            return self._locks[agent_id]

    def _update_manifest(
        self,
        agent_id: str,
        full_path: Path,
        data: bytes,
    ) -> None:
        """更新 Agent 的文件清单。"""
        agent_path = self._agent_path(agent_id)
        rel_path = str(full_path.relative_to(agent_path).as_posix())

        # 不记录 .state/ 中的文件
        if rel_path.startswith(".state/"):
            return

        manifest = self.get_manifest(agent_id)
        if manifest is None:
            manifest = WorkspaceManifest(agent_id=agent_id, updated_at=_now_iso())

        # 移除旧记录
        manifest.files = [f for f in manifest.files if f.path != rel_path]

        entry = FileEntry(
            path=rel_path,
            size=len(data),
            category=_classify_file(rel_path),
            mime_type=_guess_mime(rel_path),
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        manifest.add_file(entry)

        manifest_path = agent_path / ".state" / "manifest.json"
        self._write_json(manifest_path, manifest.to_dict())

        # 增量更新全局 manifest
        self._update_global_manifest(agent_id)

    def _remove_from_manifest(self, agent_id: str, path: str) -> None:
        """从文件清单中移除文件记录。"""
        manifest = self.get_manifest(agent_id)
        if manifest is None:
            return

        manifest.remove_file(path)

        manifest_path = self._agent_path(agent_id) / ".state" / "manifest.json"
        self._write_json(manifest_path, manifest.to_dict())

        # 更新全局 manifest
        self._update_global_manifest()

    def _update_agent_state(self, agent_id: str, status: str) -> None:
        """更新 Agent 状态到全局状态。"""
        now = _now_iso()
        existing = self._global_state.workspaces.get(agent_id)
        if existing:
            existing.status = status
            existing.updated_at = now
        else:
            state = AgentWorkspaceState(
                agent_id=agent_id,
                status=status,
                created_at=now,
                updated_at=now,
            )
            self._global_state.update_agent(state)

        self._save_global_state()

    def _update_global_manifest(self, agent_id: str | None = None) -> None:
        """增量更新全局 manifest.json。指定 agent_id 时只更新该 Agent 的记录。"""
        if self._global_manifest_path.exists():
            existing = self._read_json(self._global_manifest_path) or {}
            agents_raw = existing.get("agents", [])
            total_files = existing.get("total_files", 0)
            total_size = existing.get("total_size", 0)

            # 减去旧值
            if agent_id:
                agents_raw = [a for a in agents_raw if a.get("agent_id") != agent_id]
                total_files = sum(a.get("file_count", 0) for a in agents_raw)
                total_size = sum(a.get("total_size", 0) for a in agents_raw)
        else:
            agents_raw = []
            total_files = 0
            total_size = 0

        # 添加新值
        if agent_id:
            manifest = self.get_manifest(agent_id)
            if manifest:
                agents_raw.append(manifest.to_dict())
                total_files += manifest.file_count
                total_size += manifest.total_size

        global_manifest = {
            "version": "1.0",
            "agents": agents_raw,
            "total_agents": len(agents_raw),
            "total_files": total_files,
            "total_size": total_size,
            "updated_at": _now_iso(),
        }

        self._write_json(self._global_manifest_path, global_manifest)

    def _load_global_state(self) -> GlobalWorkspaceState:
        """从文件加载全局状态。"""
        if self._global_state_path.exists():
            data = self._read_json(self._global_state_path)
            if data:
                return GlobalWorkspaceState.from_dict(data)
        return GlobalWorkspaceState(updated_at=_now_iso())

    def _save_global_state(self) -> None:
        """持久化全局状态到文件。"""
        self._write_json(
            self._global_state_path, self._global_state.to_dict()
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        """读取 JSON 文件。返回 None 表示文件不存在或空。"""
        if not path.exists():
            return None
        if path.stat().st_size == 0:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.error("JSON 文件损坏 %s: %s", path, e)
            return None
        except OSError as e:
            logger.warning("读取 JSON 文件失败 %s: %s", path, e)
            return None

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        """写入 JSON 文件。"""
        try:
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("写入 JSON 文件失败 %s: %s", path, e)
            raise WorkspaceError(f"写入 JSON 文件失败: {e}", path=str(path))
