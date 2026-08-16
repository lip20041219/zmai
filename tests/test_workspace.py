"""Tests for zmai.workspace module."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from zmai.errors import WorkspaceError
from zmai.workspace import Workspace
from zmai.workspace.workspace import (
    AgentWorkspaceState,
    FileEntry,
    GlobalWorkspaceState,
    WorkspaceManifest,
    _classify_file,
    _guess_mime,
)

# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def tmp_root() -> Path:
    """创建临时工作区根目录。"""
    path = Path(tempfile.mkdtemp(prefix="zmai_workspace_test_"))
    yield path
    if path.exists():
        shutil.rmtree(path)


@pytest.fixture
def workspace(tmp_root: Path) -> Workspace:
    """创建 Workspace 实例。"""
    return Workspace(root=tmp_root)


@pytest.fixture
def prepared_workspace(workspace: Workspace) -> tuple[Workspace, str, Path]:
    """创建已准备好 Agent 工作区的 Workspace。"""
    agent_id = "test_agent_001"
    agent_path = workspace.prepare(agent_id)
    return workspace, agent_id, agent_path


# ── 测试: 初始化 ──────────────────────────────────────────────


class TestWorkspaceInit:
    def test_create_with_default_root(self, tmp_root: Path) -> None:
        ws = Workspace(root=tmp_root)
        assert ws._root == tmp_root
        assert ws._root.exists()

    def test_create_with_string_path(self, tmp_root: Path) -> None:
        ws = Workspace(root=str(tmp_root))
        assert ws._root == tmp_root

    def test_create_with_nonexistent_dir(self, tmp_root: Path) -> None:
        path = tmp_root / "nested" / "workspace"
        ws = Workspace(root=path)
        assert ws._root.exists()

    def test_init_creates_state_and_manifest(self, tmp_root: Path) -> None:
        Workspace(root=tmp_root)
        assert (tmp_root / "state.json").exists()
        assert (tmp_root / "manifest.json").exists()

    def test_init_with_custom_config(self, tmp_root: Path) -> None:
        ws = Workspace(root=tmp_root, config={"max_file_size": 100})
        assert ws._config["max_file_size"] == 100
        assert ws._config["max_files"] == 1000  # 默认值保留

    def test_init_with_unwritable_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试根目录不可写时抛出异常（跨平台：mock 目录创建失败）。"""
        # 不依赖操作系统只读路径（Windows 管理员下 C:\\Windows\\System32 可能可写），
        # 而是模拟 mkdir 抛 PermissionError，确保各平台行为一致。
        def _raise_permission(*args: object, **kwargs: object) -> None:
            raise PermissionError("模拟的目录创建权限被拒绝")

        monkeypatch.setattr(Path, "mkdir", _raise_permission)
        with pytest.raises(WorkspaceError):
            Workspace(root=Path(tempfile.mkdtemp(prefix="zmai_ro_")) / "sub")


# ── 测试: Agent 生命周期 ──────────────────────────────────────


class TestAgentLifecycle:
    def test_prepare_creates_directories(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        assert agent_path.exists()
        assert (agent_path / "input").exists()
        assert (agent_path / "output").exists()
        assert (agent_path / "temp").exists()
        assert (agent_path / ".state").exists()

    def test_prepare_returns_absolute_path(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        _, _, agent_path = prepared_workspace
        assert agent_path.is_absolute()

    def test_prepare_idempotent(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        path2 = ws.prepare(agent_id)
        assert path2 == agent_path
        assert agent_path.exists()

    def test_prepare_updates_global_state(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        state = ws.get_global_state()
        assert agent_id in state.workspaces
        assert state.workspaces[agent_id].status == "active"

    def test_cleanup_removes_temp(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        (agent_path / "temp" / "tmp.txt").write_text("temporary")
        assert (agent_path / "temp" / "tmp.txt").exists()

        ws.cleanup(agent_id)

        assert agent_path.exists()
        assert (agent_path / "output").exists()
        assert not (agent_path / "temp" / "tmp.txt").exists()

    def test_cleanup_without_keep_output(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        (agent_path / "output" / "result.md").write_text("# Result")
        ws.cleanup(agent_id, keep_output=False)
        assert not (agent_path / "output" / "result.md").exists()

    def test_cleanup_nonexistent_agent(self, workspace: Workspace) -> None:
        # 不存在的 Agent 不应抛出异常
        workspace.cleanup("nonexistent_agent")

    def test_remove_deletes_all(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        ws.remove(agent_id)
        assert not agent_path.exists()
        assert agent_id not in ws.get_global_state().workspaces

    def test_remove_nonexistent_agent(self, workspace: Workspace) -> None:
        workspace.remove("nonexistent")
        # 不应抛出异常

    def test_list_agents(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        agents = ws.list_agents()
        assert agent_id in agents


# ── 测试: 文件操作 ────────────────────────────────────────────


class TestFileOperations:
    def test_write_and_read_bytes(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        data = b"hello world"
        path = ws.write(agent_id, "output/test.bin", data)
        assert path.exists()
        assert ws.read(agent_id, "output/test.bin") == data

    def test_write_and_read_text(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        text = "Hello, 世界!"
        ws.write_text(agent_id, "output/hello.txt", text)
        assert ws.read_text(agent_id, "output/hello.txt") == text

    def test_write_creates_parent_dirs(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        path = ws.write(agent_id, "a/b/c/file.txt", b"deep")
        assert path.exists()

    def test_write_to_input_dir(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        ws.write(agent_id, "input/task.json", json.dumps({"task": "test"}).encode())
        assert ws.read_text(agent_id, "input/task.json") == '{"task": "test"}'

    def test_read_nonexistent_file(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        with pytest.raises(WorkspaceError, match="文件不存在"):
            ws.read(agent_id, "nonexistent.txt")

    def test_list_files(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        ws.write_text(agent_id, "a.txt", "a")
        ws.write_text(agent_id, "b.txt", "b")
        ws.write_text(agent_id, "sub/c.txt", "c")

        files = ws.list(agent_id)
        paths = [str(p) for p in files]
        assert "a.txt" in paths
        assert "b.txt" in paths
        # 跨平台：使用 PurePath 比较
        assert any("sub/c.txt" in str(p) or "sub\\c.txt" in str(p) for p in files)

    def test_list_excludes_state(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        files = ws.list(agent_id)
        paths = [str(p) for p in files]
        assert not any(".state" in p for p in paths)

    def test_list_with_pattern(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        ws.write_text(agent_id, "a.py", "code")
        ws.write_text(agent_id, "b.md", "# doc")
        ws.write_text(agent_id, "c.py", "more code")

        py_files = ws.list(agent_id, "*.py")
        assert len(py_files) == 2

    def test_exists(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        ws.write_text(agent_id, "exists.txt", "yes")
        assert ws.exists(agent_id, "exists.txt")
        assert not ws.exists(agent_id, "not_exists.txt")
        assert ws.exists(agent_id, "input")  # 目录

    def test_delete_file(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        ws.write_text(agent_id, "delete_me.txt", "bye")
        assert ws.exists(agent_id, "delete_me.txt")
        ws.delete(agent_id, "delete_me.txt")
        assert not ws.exists(agent_id, "delete_me.txt")

    def test_delete_nonexistent(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        with pytest.raises(WorkspaceError):
            ws.delete(agent_id, "not_here.txt")


# ── 测试: 路径安全 ────────────────────────────────────────────


class TestPathSecurity:
    def test_path_traversal_dotdot(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        with pytest.raises(WorkspaceError, match="路径穿越"):
            ws.read(agent_id, "../../etc/passwd")

    def test_path_traversal_agent_id(
        self, workspace: Workspace
    ) -> None:
        with pytest.raises(WorkspaceError, match="非法的 Agent ID"):
            workspace._agent_path("../evil")

    def test_path_traversal_agent_id_with_slash(
        self, workspace: Workspace
    ) -> None:
        with pytest.raises(WorkspaceError, match="非法的 Agent ID"):
            workspace._agent_path("agent/../evil")

    def test_write_path_traversal(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        with pytest.raises(WorkspaceError, match="路径穿越"):
            ws.write(agent_id, "../../escape.txt", b"hack")

    def test_agent_isolation(
        self, tmp_root: Path
    ) -> None:
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_a")
        ws.prepare("agent_b")
        ws.write_text("agent_a", "output/file.txt", "agent_a_data")

        # agent_b 不能读 agent_a 的文件
        assert not ws.exists("agent_b", "output/file.txt")
        # 尝试穿越
        with pytest.raises(WorkspaceError):
            ws.read("agent_b", "../agent_a/output/file.txt")

    # ── P0-3 回归测试：前缀相似路径不得绕过 ────────────────

    def test_prefix_similar_path_rejected(self, tmp_root: Path) -> None:
        """/ws/agent_1-secret 不得被当作 /ws/agent_1 的子路径。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_1")
        # agent_1-secret 不是 agent_1 的子目录
        # 尝试写入一个看似合法但前缀不同 agent 的路径
        with pytest.raises(WorkspaceError, match="路径穿越"):
            ws.write("agent_1", "../agent_1-secret/file.txt", b"data")

    def test_short_prefix_not_subpath(self, tmp_root: Path) -> None:
        """/ws/a 不得允许访问 /ws/a-extra。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("a")
        # 先创建 a-extra 的数据
        ws2 = Workspace(root=tmp_root)
        ws2.prepare("a-extra")
        ws2.write_text("a-extra", "output/data.txt", "secret")
        # a 不能通过路径穿越读 a-extra
        with pytest.raises(WorkspaceError, match="路径穿越"):
            ws.read("a", "../a-extra/output/data.txt")

    def test_absolute_path_outside_rejected(self, tmp_root: Path) -> None:
        """绝对路径指向 workspace 外部应拒绝。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_1")
        outside = tmp_root.parent / "secret.txt"
        outside.write_text("outside")
        # 尝试用绝对路径读取外部文件
        target = str(outside)
        # 内部调用 _validate_path 应拒绝
        with pytest.raises(WorkspaceError, match="路径穿越"):
            ws._validate_path("agent_1", target)

    def test_dot_path_resolves_correctly(self, tmp_root: Path) -> None:
        """. 和 ./ 应正确解析为当前目录。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_1")
        ws.write_text("agent_1", "output/test.txt", "data")
        # 通过 ./ 读取应成功
        content = ws.read_text("agent_1", "./output/test.txt")
        assert content == "data"
        # 通过 . 读取应成功
        content = ws.read_text("agent_1", "output/./test.txt")
        assert content == "data"

    def test_symlink_inside_allowed(self, tmp_root: Path) -> None:
        """workspace 内的符号链接应允许。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_1")
        ws.write_text("agent_1", "output/real.txt", "real data")
        # 创建符号链接
        link = ws.agent_path("agent_1") / "output/link.txt"
        target = ws.agent_path("agent_1") / "output/real.txt"
        try:
            link.symlink_to(target)
            content = ws.read_text("agent_1", "output/link.txt")
            assert content == "real data"
        except OSError:
            pytest.skip("当前系统不支持创建符号链接")

    def test_symlink_outside_rejected(self, tmp_root: Path) -> None:
        """指向 workspace 外部的符号链接应被拒绝。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_1")
        outside = tmp_root.parent / "external.txt"
        outside.write_text("external")
        link = ws.agent_path("agent_1") / "output/evil_link.txt"
        try:
            link.symlink_to(outside)
            with pytest.raises(WorkspaceError, match="路径穿越"):
                ws.read_text("agent_1", "output/evil_link.txt")
        except OSError:
            pytest.skip("当前系统不支持创建符号链接")

    # ── 回归测试：Windows 大小写不敏感 ────────────────────

    def test_case_difference_on_windows(self, tmp_root: Path) -> None:
        """Windows 上大小写不同但实际相同的路径应允许。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_1")
        ws.write_text("agent_1", "output/Test.txt", "case test")
        # 大小写不同应能读取
        data = ws.read_text("agent_1", "output/test.txt")
        assert data == "case test"

    # ── 回归测试：根目录自身路径 ──────────────────────────

    def test_root_path_itself_allowed(self, tmp_root: Path) -> None:
        """agent_path 自身（不拼接路径）应通过验证。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_root")
        result = ws._validate_path("agent_root", ".")
        assert result == ws.agent_path("agent_root")

    def test_empty_path_allowed(self, tmp_root: Path) -> None:
        """空路径（等同于当前目录）应通过验证。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_empty")
        result = ws._validate_path("agent_empty", "")
        assert result == ws.agent_path("agent_empty")

    # ── 回归测试：多层嵌套路径 ────────────────────────────

    def test_deeply_nested_path_allowed(self, tmp_root: Path) -> None:
        """深层嵌套路径应允许。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_deep")
        ws.write_text("agent_deep", "a/b/c/d/e/f/file.txt", "deep")
        data = ws.read_text("agent_deep", "a/b/c/d/e/f/file.txt")
        assert data == "deep"

    # ── 回归测试：仅空格路径 ──────────────────────────────

    def test_path_with_utf8_chars(self, tmp_root: Path) -> None:
        """UTF-8 路径应正确解析。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_utf")
        ws.write_text("agent_utf", "文件/报告.txt", "utf8 content")
        data = ws.read_text("agent_utf", "文件/报告.txt")
        assert data == "utf8 content"

    # ── 回归测试：多个 . 和 .. 的组合 ──────────────────────

    def test_mixed_dot_dotdot_resolved(self, tmp_root: Path) -> None:
        """./././a/././b 应等价于 a/b。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_mix")
        ws.write_text("agent_mix", "a/b/file.txt", "mix")
        data = ws.read_text("agent_mix", "./././a/././b/file.txt")
        assert data == "mix"

    def test_dotdot_then_back_inside(self, tmp_root: Path) -> None:
        """先向上再向下的路径（../agent_1/back）如果仍在范围内应允许。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_back")
        ws.write_text("agent_back", "output/file.txt", "back")
        # agent_back 的父目录的 agent_back 子目录 → 自身
        # 等价于 agent_back/output/file.txt
        data = ws.read_text("agent_back", "output/../output/file.txt")
        assert data == "back"

    # ── 回归测试：路径末尾的点 ────────────────────────────

    def test_trailing_dot_allowed(self, tmp_root: Path) -> None:
        """路径末尾的 . 应正确解析为当前目录。"""
        ws = Workspace(root=tmp_root)
        ws.prepare("agent_trail")
        ws.write_text("agent_trail", "output/file.txt", "trailing")
        data = ws.read_text("agent_trail", "output/./file.txt")
        assert data == "trailing"


# ── 测试: 文件大小限制 ────────────────────────────────────────


class TestFileSizeLimit:
    def test_exceeds_max_file_size(self, tmp_root: Path) -> None:
        ws = Workspace(root=tmp_root, config={"max_file_size": 10})
        ws.prepare("agent_tiny")
        with pytest.raises(WorkspaceError, match="文件大小超过限制"):
            ws.write("agent_tiny", "big.txt", b"x" * 11)

    def test_allows_within_limit(self, tmp_root: Path) -> None:
        ws = Workspace(root=tmp_root, config={"max_file_size": 100})
        ws.prepare("agent_tiny")
        # 应该成功
        ws.write("agent_tiny", "small.txt", b"x" * 50)


# ── 测试: Manifest 管理 ───────────────────────────────────────


class TestManifest:
    def test_manifest_created_on_prepare(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        manifest = ws.get_manifest(agent_id)
        assert manifest is not None
        assert manifest.agent_id == agent_id

    def test_manifest_updates_on_write(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        ws.write_text(agent_id, "output/test.md", "# Hello")
        manifest = ws.get_manifest(agent_id)
        assert manifest is not None
        assert manifest.file_count == 1
        assert manifest.files[0].path == "output/test.md"
        assert manifest.files[0].category == "markdown"

    def test_manifest_tracks_multiple_files(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        ws.write_text(agent_id, "a.py", "code")
        ws.write_text(agent_id, "b.md", "doc")
        ws.write_text(agent_id, "c.json", "{}")
        manifest = ws.get_manifest(agent_id)
        assert manifest is not None
        assert manifest.file_count == 3
        assert manifest.total_size > 0

    def test_manifest_removes_on_delete(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        ws.write_text(agent_id, "delete_me.py", "code")
        assert ws.get_manifest(agent_id).file_count == 1  # type: ignore
        ws.delete(agent_id, "delete_me.py")
        assert ws.get_manifest(agent_id).file_count == 0  # type: ignore

    def test_global_manifest(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        ws.write_text(agent_id, "test.py", "code")
        global_manifest = ws.get_global_manifest()
        assert global_manifest["total_agents"] == 1
        assert global_manifest["total_files"] >= 1

    def test_workspace_manifest_dataclass(self) -> None:
        manifest = WorkspaceManifest(agent_id="test")
        entry = FileEntry(
            path="test.py",
            size=100,
            category="code",
            mime_type="text/x-python",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        manifest.add_file(entry)
        assert manifest.file_count == 1
        assert manifest.total_size == 100

        d = manifest.to_dict()
        restored = WorkspaceManifest.from_dict(d)
        assert restored.agent_id == "test"
        assert restored.file_count == 1


# ── 测试: State 管理 ──────────────────────────────────────────


class TestState:
    def test_global_state(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        state = ws.get_global_state()
        assert state.total_workspaces == 1
        assert state.active_workspaces == 1

    def test_agent_state(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        state = ws.get_state(agent_id)
        assert state is not None
        assert state.status == "active"

    def test_state_persists_on_disk(
        self, tmp_root: Path
    ) -> None:
        ws1 = Workspace(root=tmp_root)
        ws1.prepare("persist_test")
        ws1.write_text("persist_test", "test.txt", "data")

        # 用新的 Workspace 实例读取
        ws2 = Workspace(root=tmp_root)
        state = ws2.get_global_state()
        assert "persist_test" in state.workspaces
        assert ws2.read_text("persist_test", "test.txt") == "data"

    def test_agent_state_dataclass(self) -> None:
        state = AgentWorkspaceState(
            agent_id="test",
            status="active",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        d = state.to_dict()
        restored = AgentWorkspaceState.from_dict(d)
        assert restored.agent_id == "test"
        assert restored.status == "active"

    def test_global_state_dataclass(self) -> None:
        state = GlobalWorkspaceState(updated_at="2026-01-01T00:00:00Z")
        agent_state = AgentWorkspaceState(
            agent_id="a1",
            status="active",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        state.update_agent(agent_state)
        assert state.total_workspaces == 1
        assert state.active_workspaces == 1

        d = state.to_dict()
        restored = GlobalWorkspaceState.from_dict(d)
        assert restored.total_workspaces == 1
        assert "a1" in restored.workspaces


# ── 测试: 目录路径 ────────────────────────────────────────────


class TestDirectoryPaths:
    def test_agent_path(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        assert ws.agent_path(agent_id) == agent_path

    def test_input_dir(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        expected = agent_path / "input"
        assert ws.input_dir(agent_id) == expected

    def test_output_dir(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        expected = agent_path / "output"
        assert ws.output_dir(agent_id) == expected

    def test_temp_dir(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        expected = agent_path / "temp"
        assert ws.temp_dir(agent_id) == expected

    def test_state_dir(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, agent_path = prepared_workspace
        expected = agent_path / ".state"
        assert ws.state_dir(agent_id) == expected


# ── 测试: 文件类型分类 ────────────────────────────────────────


class TestFileClassification:
    def test_classify_code(self) -> None:
        assert _classify_file("main.py") == "code"
        assert _classify_file("app.js") == "code"
        assert _classify_file("component.tsx") == "code"

    def test_classify_markdown(self) -> None:
        assert _classify_file("readme.md") == "markdown"
        assert _classify_file("doc.markdown") == "markdown"

    def test_classify_image(self) -> None:
        assert _classify_file("photo.png") == "image"
        assert _classify_file("photo.jpg") == "image"
        assert _classify_file("icon.svg") == "image"

    def test_classify_config(self) -> None:
        assert _classify_file("config.yaml") == "config"
        assert _classify_file(".env") == "config"
        assert _classify_file("pyproject.toml") == "config"

    def test_classify_database(self) -> None:
        assert _classify_file("data.db") == "database"
        assert _classify_file("data.sqlite") == "database"
        assert _classify_file("data.json") == "database"

    def test_classify_task(self) -> None:
        assert _classify_file("todo.txt") == "task"
        assert _classify_file("tasks.todo") == "task"

    def test_classify_pdf(self) -> None:
        assert _classify_file("doc.pdf") == "pdf"

    def test_classify_prompt(self) -> None:
        assert _classify_file("template.jinja") == "prompt"
        assert _classify_file("prompt.prompt") == "prompt"

    def test_classify_unknown(self) -> None:
        assert _classify_file("unknown.xyz") == "other"

    def test_guess_mime(self) -> None:
        assert _guess_mime("test.py") == "text/x-python"
        assert _guess_mime("test.md") == "text/markdown"
        assert _guess_mime("test.png") == "image/png"
        assert _guess_mime("test.pdf") == "application/pdf"
        assert _guess_mime("test.json") == "application/json"
        assert _guess_mime("test.unknown") == "application/octet-stream"


# ── 测试: 各种文件类型支持 ────────────────────────────────────


class TestFileTypeSupport:
    """测试 workspace 支持的各种文件类型。"""

    def test_markdown_file(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        content = "# Title\n\nHello **world**."
        ws.write_text(agent_id, "output/doc.md", content)
        assert ws.read_text(agent_id, "output/doc.md") == content
        manifest = ws.get_manifest(agent_id)
        assert manifest is not None
        md_file = [f for f in manifest.files if f.path == "output/doc.md"][0]
        assert md_file.category == "markdown"

    def test_python_code_file(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        code = "def hello():\n    print('hello')\n"
        ws.write_text(agent_id, "output/hello.py", code)
        assert ws.read_text(agent_id, "output/hello.py") == code

    def test_json_config_file(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        config = {"key": "value", "count": 42}
        ws.write_text(agent_id, "output/config.json", json.dumps(config, indent=2))
        written = json.loads(ws.read_text(agent_id, "output/config.json"))
        assert written == config

    def test_image_file_binary(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        # 模拟一个小的 PNG 文件（实际是二进制数据）
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        ws.write(agent_id, "output/image.png", png_header)
        assert ws.read(agent_id, "output/image.png") == png_header

    def test_yaml_config_file(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        content = "version: 1\nname: test\n"
        ws.write_text(agent_id, "output/config.yaml", content)
        assert ws.read_text(agent_id, "output/config.yaml") == content

    def test_sqlite_database_file(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        # 只是测试二进制写入，不实际创建 SQLite DB
        data = b"SQLite format 3\x00" + b"\x00" * 100
        ws.write(agent_id, "output/data.db", data)
        assert ws.read(agent_id, "output/data.db")[:16] == b"SQLite format 3\x00"

    def test_prompt_template_file(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        ws, agent_id, _ = prepared_workspace
        content = "You are an expert in {{ domain }}."
        ws.write_text(agent_id, "output/prompt.jinja", content)
        assert ws.read_text(agent_id, "output/prompt.jinja") == content

    def test_mixed_file_types(
        self, prepared_workspace: tuple[Workspace, str, Path]
    ) -> None:
        """测试多种文件类型混合使用。"""
        ws, agent_id, _ = prepared_workspace

        files = {
            "input/task.txt": b"Fix the bug",
            "input/task.json": b'{"priority": "high"}',
            "output/report.md": b"# Report",
            "output/main.py": b"print('hello')",
            "output/config.yaml": b"key: value",
            "temp/tmp.dat": b"temporary",
        }

        for path, content in files.items():
            ws.write(agent_id, path, content)

        for path, content in files.items():
            assert ws.read(agent_id, path) == content

        manifest = ws.get_manifest(agent_id)
        assert manifest is not None
        assert manifest.file_count >= len(files) - 1  # temp 中的不计数，但 manifest 中排除 .state 但不排除 temp  # noqa: E501


# ── 测试: 并发安全 ────────────────────────────────────────────


class TestConcurrency:
    def test_multiple_agents(
        self, tmp_root: Path
    ) -> None:
        ws = Workspace(root=tmp_root)
        agents = [f"agent_{i:03d}" for i in range(10)]

        for aid in agents:
            ws.prepare(aid)
            ws.write_text(aid, f"output/{aid}.txt", f"data_{aid}")

        for aid in agents:
            assert ws.exists(aid, f"output/{aid}.txt")
            assert ws.read_text(aid, f"output/{aid}.txt") == f"data_{aid}"

        state = ws.get_global_state()
        assert state.total_workspaces == 10

    def test_same_agent_concurrent_operations(
        self, tmp_root: Path
    ) -> None:
        """验证同一 Agent 的并发文件操作不会导致 manifest 损坏。"""
        import concurrent.futures

        ws = Workspace(root=tmp_root)
        agent_id = "concurrent_agent"
        ws.prepare(agent_id)

        def write_file(i: int) -> None:
            ws.write_text(agent_id, f"output/file_{i:03d}.txt", f"content_{i}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write_file, range(50)))

        manifest = ws.get_manifest(agent_id)
        assert manifest is not None
        assert manifest.file_count == 50
