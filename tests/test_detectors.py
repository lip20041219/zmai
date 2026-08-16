"""检测器集成测试 — Python / Node / Rust / Go / Git / Docker / Monorepo。"""

from __future__ import annotations

from pathlib import Path

from zmai.cli.context import build_context
from zmai.cli.detectors.docker_detector import DockerDetector
from zmai.cli.detectors.git_detector import GitDetector
from zmai.cli.detectors.lang import GoDetector, NodeDetector, PythonDetector, RustDetector
from zmai.cli.detectors.monorepo import MonorepoDetector


class TestPythonDetector:
    def test_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\nrequires-python = '>=3.11'\n")  # noqa: E501
        r = PythonDetector().detect(tmp_path)
        assert r and r["type"] == "python"
        assert r["language_version"] == "3.11"

    def test_python_version_file(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        (tmp_path / ".python-version").write_text("3.12")
        r = PythonDetector().detect(tmp_path)
        assert r and r["language_version"] == "3.12"

    def test_setup_py(self, tmp_path: Path):
        (tmp_path / "setup.py").write_text("")
        r = PythonDetector().detect(tmp_path)
        assert r and r["type"] == "python"

    def test_pytest_detected(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n")
        r = PythonDetector().detect(tmp_path)
        assert r and r["test_framework"] == "pytest"

    def test_not_python(self, tmp_path: Path):
        r = PythonDetector().detect(tmp_path)
        assert r is None


class TestNodeDetector:
    def test_basic(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"x"}')
        r = NodeDetector().detect(tmp_path)
        assert r and r["type"] == "node"

    def test_pnpm(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"x"}')
        (tmp_path / "pnpm-lock.yaml").write_text("")
        r = NodeDetector().detect(tmp_path)
        assert r and r["package_manager"] == "pnpm"

    def test_test_framework(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"x","devDependencies":{"vitest":"1.0"}}')
        r = NodeDetector().detect(tmp_path)
        assert r and r["test_framework"] == "vitest"

    def test_not_node(self, tmp_path: Path):
        r = NodeDetector().detect(tmp_path)
        assert r is None


class TestRustDetector:
    def test_basic(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\nedition = "2021"\n')
        r = RustDetector().detect(tmp_path)
        assert r and r["type"] == "rust"
        assert r["language_version"] == "2021"

    def test_not_rust(self, tmp_path: Path):
        r = RustDetector().detect(tmp_path)
        assert r is None


class TestGoDetector:
    def test_basic(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example\n\ngo 1.22\n")
        r = GoDetector().detect(tmp_path)
        assert r and r["type"] == "go"
        assert r["language_version"] == "1.22"

    def test_not_go(self, tmp_path: Path):
        r = GoDetector().detect(tmp_path)
        assert r is None


class TestGitDetector:
    def test_no_git(self, tmp_path: Path):
        r = GitDetector().detect(tmp_path)
        assert r is None

    def test_with_git(self, tmp_path: Path):
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)  # noqa: E501
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)  # noqa: E501
        (tmp_path / "f.py").write_text("x = 1")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
        r = GitDetector().detect(tmp_path)
        assert r is not None
        assert r["git_branch"] == "master" or r["git_branch"] == "main"


class TestDockerDetector:
    def test_dockerfile(self, tmp_path: Path):
        (tmp_path / "Dockerfile").write_text("FROM python")
        r = DockerDetector().detect(tmp_path)
        assert r and r["has_dockerfile"]

    def test_docker_compose(self, tmp_path: Path):
        (tmp_path / "docker-compose.yml").write_text("")
        r = DockerDetector().detect(tmp_path)
        assert r and r["has_docker_compose"]

    def test_none(self, tmp_path: Path):
        r = DockerDetector().detect(tmp_path)
        assert r is None


class TestMonorepoDetector:
    def test_pnpm_workspace(self, tmp_path: Path):
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - packages/*\n")
        # also need at least 2 dirs in mono_dirs
        (tmp_path / "packages").mkdir()
        (tmp_path / "apps").mkdir()
        r = MonorepoDetector().detect(tmp_path)
        assert r and r["is_monorepo"]

    def test_npm_workspaces(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"workspaces":["packages/*"]}')
        (tmp_path / "packages").mkdir()
        (tmp_path / "apps").mkdir()
        r = MonorepoDetector().detect(tmp_path)
        assert r and r["is_monorepo"]

    def test_not_monorepo(self, tmp_path: Path):
        r = MonorepoDetector().detect(tmp_path)
        assert r is None


class TestBuildContext:
    def test_python_project(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\nrequires-python = '>=3.11'\n")  # noqa: E501
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        ctx = build_context(tmp_path)
        assert ctx.type == "python"
        assert ctx.name == tmp_path.name
        assert "src" in ctx.src_dirs
        assert ctx.package_manager == "pip"

    def test_node_project(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"x","devDependencies":{"jest":"1.0"}}')
        (tmp_path / "src").mkdir()
        ctx = build_context(tmp_path)
        assert ctx.type == "node"
        assert ctx.test_framework == "jest"

    def test_unknown_project(self, tmp_path: Path):
        ctx = build_context(tmp_path)
        assert ctx.type == "unknown"

    def test_git_integration(self, tmp_path: Path):
        import subprocess
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), capture_output=True)  # noqa: E501
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "x.py").write_text("")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
        ctx = build_context(tmp_path)
        assert ctx.type == "python"
        assert ctx.git_branch in ("master", "main")
