"""ProjectDetector 测试 — 项目自动发现。"""

from __future__ import annotations

from pathlib import Path

from zmai.cli.detector import _find_root, _load_project_config, _resolve_workspace, detect


class TestFindRoot:
    def test_cwd_is_root(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        root = _find_root(tmp_path)
        assert root == tmp_path

    def test_pyproject_toml_is_root(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("")
        root = _find_root(tmp_path)
        assert root == tmp_path

    def test_upward_traversal(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "a" / "b" / "c"
        sub.mkdir(parents=True)
        root = _find_root(sub)
        assert root == tmp_path

    def test_no_project_returns_none(self, tmp_path: Path):
        root = _find_root(tmp_path)
        assert root is None

    def test_zmai_root_marker(self, tmp_path: Path):
        (tmp_path / ".zmai-root").write_text("")
        root = _find_root(tmp_path)
        assert root == tmp_path


class TestDetect:
    def test_detect_project(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        info = detect(tmp_path)
        assert info.mode == "project"
        assert info.root == tmp_path
        assert info.name == tmp_path.name

    def test_detect_chat(self, tmp_path: Path):
        info = detect(tmp_path)
        assert info.mode == "chat"
        assert info.root is None

    def test_detect_python_project(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("")
        info = detect(tmp_path)
        assert info.mode == "project"
        assert info.project_type == "python"

    def test_detect_node_project(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}")
        info = detect(tmp_path)
        assert info.mode == "project"
        assert info.project_type == "node"


class TestLoadConfig:
    def test_load_existing(self, tmp_path: Path):
        cfg = tmp_path / "zmai.json"
        cfg.write_text('{"runtime": {"max_iterations": 200}}')
        result = _load_project_config(tmp_path)
        assert result["runtime"]["max_iterations"] == 200

    def test_load_missing(self, tmp_path: Path):
        result = _load_project_config(tmp_path)
        assert result == {}

    def test_load_invalid_json(self, tmp_path: Path):
        (tmp_path / "zmai.json").write_text("not json")
        result = _load_project_config(tmp_path)
        assert result == {}


class TestResolveWorkspace:
    def test_default_workspace(self, tmp_path: Path):
        ws = _resolve_workspace(tmp_path, {})
        assert ws == (tmp_path / "workspace").resolve()

    def test_custom_workspace(self, tmp_path: Path):
        ws = _resolve_workspace(tmp_path, {"workspace": {"root": "./my_ws"}})
        assert ws == (tmp_path / "my_ws").resolve()

    def test_absolute_workspace(self, tmp_path: Path):
        abs_path = tmp_path / "other" / "ws"
        ws = _resolve_workspace(tmp_path, {"workspace": {"root": str(abs_path)}})
        assert ws == abs_path.resolve()
