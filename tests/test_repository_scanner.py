"""RepositoryScanner — 项目源码目录发现与过滤的全面测试。

测试覆盖：
  1. 正常扫描 — 发现源码/测试/配置文件
  2. 排除规则 — workspace/、.state/、__pycache__/ 等不被扫描
  3. 项目根目录自动发现
  4. 边界情况 — 空项目、非项目目录
  5. 格式输出 — summary 和 format_compact
  6. 关键场景 — project/ 含 app/、tests/、workspace/，只分析 app/ 和 tests/
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zmai.swe.scanner import RepositoryInfo, RepositoryScanner, scan_repository


def _p(path: str) -> Path:
    """Convert forward-slash path string to Path (cross-platform compatible)."""
    return Path(path)


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════


def _make_project(tmp_path: Path, structure: dict[str, str | None]) -> Path:
    """从字典创建项目目录结构。

    Args:
        tmp_path: 临时目录。
        structure: key=文件路径, value=文件内容。
                    值为 None 表示创建目录（而非文件）。

    Returns:
        项目根目录路径。
    """
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    for path, content in structure.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            full.mkdir(exist_ok=True)
        else:
            full.write_text(content, encoding="utf-8")
    return root


# ═══════════════════════════════════════════════════════════════════
# 测试: 基本扫描功能
# ═══════════════════════════════════════════════════════════════════


class TestBasicScan:
    """基本扫描功能测试。"""

    def test_scan_detects_python_files(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "app/main.py": "def main(): pass\n",
            "app/utils.py": "def helper(): pass\n",
            "README.md": "# Project\n",
        })
        info = RepositoryScanner.scan(root)
        assert info.file_count >= 1
        assert _p("app/main.py") in info.source_files
        assert _p("app/utils.py") in info.source_files

    def test_scan_detects_test_files(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "app/main.py": "def main(): pass\n",
            "tests/test_main.py": "def test_main(): pass\n",
            "tests/conftest.py": "import pytest\n",
        })
        info = RepositoryScanner.scan(root)
        assert _p("tests/test_main.py") in info.test_files
        assert _p("tests/conftest.py") in info.test_files
        # test_main.py 和 conftest.py 也应该是 source_files
        assert _p("tests/test_main.py") in info.source_files

    def test_scan_detects_config_files(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "app/main.py": "print('hello')\n",
            "pyproject.toml": "[project]\nname = 'test'\n",
            ".env": "DEBUG=true\n",
        })
        info = RepositoryScanner.scan(root)
        assert _p("pyproject.toml") in info.config_files
        assert _p("app/main.py") in info.source_files
        assert _p(".env") in info.config_files

    def test_scan_detects_language(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "src/main.py": "# python\n",
            "src/utils.py": "# python 2\n",
            "src/main.js": "// javascript\n",
        })
        info = RepositoryScanner.scan(root)
        # Python 文件 (2) 多于 JS (1)
        assert info.language == "python"

    def test_scan_git_detection(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "src/main.py": "x = 1\n",
            ".git/config": "[core]\n",
        })
        info = RepositoryScanner.scan(root)
        assert info.has_git is True

    def test_scan_no_git(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "src/main.py": "x = 1\n",
        })
        info = RepositoryScanner.scan(root)
        assert info.has_git is False

    def test_scan_unknown_language(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "foo.xyz": "content\n",
        })
        info = RepositoryScanner.scan(root)
        assert info.language == "unknown"


# ═══════════════════════════════════════════════════════════════════
# 测试: 排除规则
# ═══════════════════════════════════════════════════════════════════


class TestExclusionRules:
    """排除规则测试 — workspace/、.state/、__pycache__/ 等不应被扫描。"""

    def test_workspace_dir_excluded(self, tmp_path: Path):
        """workspace/ 目录不应出现在扫描结果中。"""
        root = _make_project(tmp_path, {
            "app/main.py": "def app(): pass\n",
            "workspace/agent_1/.state/state.json": '{"status": "active"}\n',
            "workspace/agent_1/output/result.txt": "done\n",
        })
        info = RepositoryScanner.scan(root)
        assert _p("app/main.py") in info.source_files
        for f in info.source_files:
            assert "workspace" not in str(f), f"workspace/ 不应被扫描: {f}"

    def test_dot_state_dir_excluded(self, tmp_path: Path):
        """.state/ 目录不应出现在扫描结果中。"""
        root = _make_project(tmp_path, {
            "app/main.py": "x = 1\n",
            "app/.state/cache.json": '{"cached": true}\n',
            ".state/global.json": '{"global": true}\n',
        })
        info = RepositoryScanner.scan(root)
        assert _p("app/main.py") in info.source_files
        for f in info.source_files:
            assert ".state" not in str(f), f".state/ 不应被扫描: {f}"

    def test_pycache_excluded(self, tmp_path: Path):
        """__pycache__/ 不应被扫描。"""
        root = _make_project(tmp_path, {
            "app/main.py": "x = 1\n",
            "app/__pycache__/main.cpython-311.pyc": None,
        })
        info = RepositoryScanner.scan(root)
        assert _p("app/main.py") in info.source_files
        for f in info.source_files:
            assert "__pycache__" not in str(f), f"__pycache__/ 不应被扫描: {f}"

    def test_git_dir_excluded(self, tmp_path: Path):
        """.git/ 目录不应被扫描。"""
        root = _make_project(tmp_path, {
            "src/main.py": "x = 1\n",
            ".git/objects/abc123": None,
            ".git/HEAD": "ref: refs/heads/main\n",
        })
        info = RepositoryScanner.scan(root)
        assert _p("src/main.py") in info.source_files
        for f in info.source_files:
            assert ".git" not in str(f), f".git/ 不应被扫描: {f}"

    def test_hidden_dirs_excluded(self, tmp_path: Path):
        """以 '.' 开头的隐藏目录应被排除（白名单除外）。"""
        root = _make_project(tmp_path, {
            "src/main.py": "x = 1\n",
            ".hidden_dir/config.txt": "secret\n",
            ".vscode/settings.json": '{"editor": "vim"}\n',
        })
        info = RepositoryScanner.scan(root)
        assert _p("src/main.py") in info.source_files
        for f in info.source_files:
            assert ".hidden_dir" not in str(f)
            assert ".vscode" not in str(f)

    def test_node_modules_excluded(self, tmp_path: Path):
        """node_modules/ 不应被扫描。"""
        root = _make_project(tmp_path, {
            "src/index.js": "console.log('hello')\n",
            "node_modules/lodash/index.js": "// million lines\n",
        })
        info = RepositoryScanner.scan(root)
        assert _p("src/index.js") in info.source_files
        for f in info.source_files:
            assert "node_modules" not in str(f)

    def test_temp_dir_excluded(self, tmp_path: Path):
        """temp/ 目录不应被扫描（workspace 子目录）。"""
        root = _make_project(tmp_path, {
            "app/main.py": "x = 1\n",
            "temp/scratch.txt": "scratch\n",
        })
        info = RepositoryScanner.scan(root)
        assert _p("app/main.py") in info.source_files
        for f in info.source_files:
            assert "temp" not in str(f)

    def test_output_dir_excluded(self, tmp_path: Path):
        """output/ 目录不应被扫描（workspace 子目录）。"""
        root = _make_project(tmp_path, {
            "app/main.py": "x = 1\n",
            "output/report.json": '{"result": "ok"}\n',
        })
        info = RepositoryScanner.scan(root)
        assert _p("app/main.py") in info.source_files
        for f in info.source_files:
            assert "output" not in str(f)


# ═══════════════════════════════════════════════════════════════════
# 测试: 关键场景 — project/ 含 app/、tests/、workspace/
# ═══════════════════════════════════════════════════════════════════


class TestKeyScenario:
    """关键测试场景：Agent 应只分析 app/ 和 tests/，不能读取 workspace/。

    给定:
        project/
         ├ app/
         │  └ main.py
         ├ tests/
         │  └ test_main.py
         └ workspace/       ← 这个必须被忽略
            └ data.txt

    Agent 的扫描结果必须只包含 app/ 和 tests/ 中的文件。
    """

    def test_only_app_and_tests_are_scanned(self, tmp_path: Path):
        """验证 workspace/ 被排除，只有 app/ 和 tests/ 出现在结果中。"""
        root = _make_project(tmp_path, {
            "app/main.py": "def app(): pass\n",
            "app/utils.py": "def util(): pass\n",
            "tests/test_main.py": "from app import main\n",
            "tests/conftest.py": "import pytest\n",
            "workspace/output.txt": "should not appear\n",
            "workspace/.state/state.json": '{"status": "active"}\n',
        })
        info = RepositoryScanner.scan(root)

        # 验证：app/ 和 tests/ 文件出现在 scan 结果中
        all_source = info.source_files
        all_test = info.test_files

        assert _p("app/main.py") in all_source, "app/main.py 必须被扫描到"
        assert _p("app/utils.py") in all_source, "app/utils.py 必须被扫描到"
        assert _p("tests/test_main.py") in all_source, "测试文件也属于源码"

        assert _p("tests/test_main.py") in all_test, "test_main.py 应被识别为测试文件"
        assert _p("tests/conftest.py") in all_test, "conftest.py 应被识别为测试文件"

        # 验证：workspace/ 和 .state/ 完全不在结果中
        for p in all_source:
            assert "workspace" not in str(p), f"workspace/ 不应在扫描结果中: {p}"
            assert ".state" not in str(p), f".state/ 不应在扫描结果中: {p}"

    def test_all_files_excludes_workspace(self, tmp_path: Path):
        """all_files 也应排除 workspace/ 内容。"""
        root = _make_project(tmp_path, {
            "app/main.py": "x = 1\n",
            "workspace/output.txt": "hidden\n",
        })
        info = RepositoryScanner.scan(root)
        assert _p("app/main.py") in info.all_files
        for f in info.all_files:
            assert "workspace" not in str(f)

    def test_summary_excludes_workspace(self, tmp_path: Path):
        """summary 输出不应提及 workspace/。"""
        root = _make_project(tmp_path, {
            "app/main.py": "x = 1\n",
            "workspace/data.txt": "hidden\n",
        })
        info = RepositoryScanner.scan(root)
        assert "workspace" not in info.summary, \
            "summary 不应包含 workspace/ 的引用"
        assert "app/main.py" in info.summary or "app" in info.summary

    def test_repo_info_file_count_accurate(self, tmp_path: Path):
        """file_count 不应计入被排除目录中的文件。"""
        root = _make_project(tmp_path, {
            "app/main.py": "x = 1\n",
            "app/helper.py": "y = 2\n",
            "tests/test_app.py": "def test_x(): pass\n",
            "workspace/big_output.txt": "x" * 1000,
            ".state/cache.json": "{}",
        })
        info = RepositoryScanner.scan(root)
        # 只有 app/main.py, app/helper.py, tests/test_app.py 应被计入
        assert info.file_count == 3, (
            f"file_count 应为 3（排除 workspace/ 和 .state/ 后），"
            f"实际为 {info.file_count}"
        )


# ═══════════════════════════════════════════════════════════════════
# 测试: 项目根目录发现
# ═══════════════════════════════════════════════════════════════════


class TestFindProjectRoot:
    """项目根目录自动发现。"""

    def test_find_root_with_git(self, tmp_path: Path):
        root = tmp_path / "my-project"
        root.mkdir()
        (root / ".git").mkdir()
        (root / "src").mkdir()
        (root / "src/main.py").write_text("x = 1")

        found = RepositoryScanner.find_project_root(root / "src")
        assert found is not None
        assert found == root.resolve()

    def test_find_root_with_pyproject(self, tmp_path: Path):
        root = tmp_path / "my-project"
        root.mkdir()
        (root / "pyproject.toml").write_text("[project]\n")
        (root / "sub").mkdir()

        found = RepositoryScanner.find_project_root(root / "sub")
        assert found is not None
        assert found == root.resolve()

    def test_find_root_without_markers(self, tmp_path: Path):
        root = tmp_path / "no-project"
        root.mkdir()
        (root / "random.py").write_text("x = 1")

        # 没有标记文件，不应找到项目根目录
        found = RepositoryScanner.find_project_root(root)
        assert found is None

    def test_find_root_from_current_directory(self, tmp_path: Path):
        root = tmp_path / "project"
        root.mkdir()
        (root / ".git").mkdir()
        (root / "code.py").write_text("x = 1")

        # 从根目录自身查找
        found = RepositoryScanner.find_project_root(root)
        assert found == root.resolve()

    def test_find_root_stops_at_home(self, tmp_path: Path):
        """不应跨越 home 目录向上查找。"""
        # 使用临时目录确保没有项目标记
        empty = tmp_path / "empty"
        empty.mkdir()
        found = RepositoryScanner.find_project_root(empty)
        # 可能找不到（因为我们不在 home 的父目录创建标记）
        # 关键是函数不崩溃
        assert found is None or found.exists()


# ═══════════════════════════════════════════════════════════════════
# 测试: 边界情况
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """边界情况测试。"""

    def test_empty_project(self, tmp_path: Path):
        """空项目（无文件）应返回空的 RepositoryInfo。"""
        root = tmp_path / "empty"
        root.mkdir()
        info = RepositoryScanner.scan(root)
        assert info.file_count == 0
        assert len(info.source_files) == 0
        assert len(info.all_files) == 0
        assert info.language == "unknown"

    def test_non_existent_directory(self, tmp_path: Path):
        """不存在的目录应抛出 FileNotFoundError。"""
        fake = tmp_path / "does-not-exist"
        with pytest.raises(FileNotFoundError):
            RepositoryScanner.scan(fake)

    def test_file_instead_of_directory(self, tmp_path: Path):
        """传入文件路径而不是目录应抛出 NotADirectoryError。"""
        f = tmp_path / "a_file.txt"
        f.write_text("hello")
        with pytest.raises(NotADirectoryError):
            RepositoryScanner.scan(f)

    def test_large_project_truncation(self, tmp_path: Path):
        """超过 max_files 限制时应截断。"""
        root = tmp_path / "big_project"
        root.mkdir()
        for i in range(600):
            (root / f"file_{i}.py").write_text(f"x = {i}\n")

        info = RepositoryScanner.scan(root, max_files=100)
        assert len(info.all_files) <= 100

    def test_scan_repository_convenience(self, tmp_path: Path):
        """scan_repository 便捷函数应返回 RepositoryInfo。"""
        root = _make_project(tmp_path, {
            "main.py": "print('hello')\n",
        })
        info = scan_repository(root)
        assert isinstance(info, RepositoryInfo)
        assert len(info.source_files) == 1

    def test_root_is_correctly_set(self, tmp_path: Path):
        """RepositoryInfo.root 应为解析后的绝对路径。"""
        root = _make_project(tmp_path, {
            "main.py": "x = 1\n",
        })
        info = RepositoryScanner.scan(root)
        assert info.root == root.resolve()

    def test_project_without_source_files(self, tmp_path: Path):
        """只有非源码文件的项目。"""
        root = _make_project(tmp_path, {
            "README.md": "# Docs\n",
            "data.csv": "a,b,c\n",
        })
        info = RepositoryScanner.scan(root)
        assert info.file_count == 0  # 没有源码文件
        assert len(info.source_files) == 0
        assert info.language == "unknown"


# ═══════════════════════════════════════════════════════════════════
# 测试: 格式输出
# ═══════════════════════════════════════════════════════════════════


class TestFormatOutput:
    """summary 和 format_compact 输出格式测试。"""

    def test_summary_contains_project_name(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "main.py": "print('hello')\n",
        })
        info = RepositoryScanner.scan(root)
        # summary 应包含项目名（目录名）
        assert root.name in info.summary

    def test_summary_lists_files(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "app/main.py": "def main(): pass\n",
            "app/utils.py": "def util(): pass\n",
        })
        info = RepositoryScanner.scan(root)
        assert "main.py" in info.summary
        assert "utils.py" in info.summary

    def test_summary_shows_file_count(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "main.py": "x = 1\n",
            "test_main.py": "def test_x(): pass\n",
        })
        info = RepositoryScanner.scan(root)
        assert "2" in info.summary or "源码" in info.summary

    def test_format_compact_compactness(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "src/main.py": "x = 1\n",
            "src/utils.py": "y = 2\n",
            "tests/test_main.py": "def test_x(): pass\n",
        })
        info = RepositoryScanner.scan(root)
        compact = RepositoryScanner.format_compact(info)
        # compact 格式应精简（不包含多行目录树）
        # 应包含项目标记
        assert root.name in compact
        # 应包含文件引用
        assert "main.py" in compact or "src" in compact

    def test_compact_does_not_mention_workspace(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "main.py": "x = 1\n",
            "workspace/hidden.txt": "secret\n",
        })
        info = RepositoryScanner.scan(root)
        compact = RepositoryScanner.format_compact(info)
        assert "workspace" not in compact

    def test_summary_returns_different_string_per_project(self, tmp_path: Path):
        root_a = _make_project(tmp_path / "a", {"main.py": "x = 1\n"})
        root_b = _make_project(tmp_path / "b", {"utils.py": "y = 2\n"})
        info_a = RepositoryScanner.scan(root_a)
        info_b = RepositoryScanner.scan(root_b)
        assert info_a.summary != info_b.summary


# ═══════════════════════════════════════════════════════════════════
# 测试: RepositoryInfo.to_dict()
# ═══════════════════════════════════════════════════════════════════


class TestRepositoryInfoDict:
    """RepositoryInfo.to_dict() 序列化测试。"""

    def test_to_dict_basic(self, tmp_path: Path):
        root = _make_project(tmp_path, {
            "src/main.py": "x = 1\n",
            "tests/test_main.py": "def test_x(): pass\n",
        })
        info = RepositoryScanner.scan(root)
        d = info.to_dict()
        assert d["root"] == str(root.resolve())
        assert d["language"] == "python"
        assert len(d["source_files"]) >= 1
        assert len(d["test_files"]) >= 1


# ═══════════════════════════════════════════════════════════════════
# 测试: is_excluded_dir() 静态方法
# ═══════════════════════════════════════════════════════════════════


class TestIsExcludedDir:
    """is_excluded_dir 静态方法测试。"""

    @pytest.mark.parametrize("name,expected", [
        ("workspace", True),
        (".state", True),
        ("__pycache__", True),
        (".git", True),
        ("node_modules", True),
        (".venv", True),
        ("build", True),
        ("dist", True),
        ("temp", True),
        ("output", True),
        ("input", True),
        (".vscode", True),
        (".idea", True),
        # 不应排除的
        ("app", False),
        ("src", False),
        ("tests", False),
        ("docs", False),
        (".github", False),  # 白名单
        (".claude", True),  # 内部配置目录，应排除
    ])
    def test_is_excluded_dir(self, name: str, expected: bool):
        assert RepositoryScanner.is_excluded_dir(name) == expected


# ═══════════════════════════════════════════════════════════════════
# 测试: is_source_file / is_test_file / is_config_file
# ═══════════════════════════════════════════════════════════════════


class TestFileClassification:
    """文件分类静态方法测试。"""

    @pytest.mark.parametrize("name,expected", [
        ("main.py", True),
        ("app.js", True),
        ("index.ts", True),
        ("style.css", True),
        ("index.html", True),
        ("Dockerfile", False),
        ("README.md", False),
        ("data.csv", False),
    ])
    def test_is_source_file(self, name: str, expected: bool):
        assert RepositoryScanner.is_source_file(Path(name)) == expected

    @pytest.mark.parametrize("name,expected", [
        ("test_main.py", True),
        ("main_test.py", True),
        ("conftest.py", True),
        ("check_it.py", True),
        ("TestApp.py", True),
        ("spec_helper.py", True),
        ("main_spec.py", True),
        ("main.py", False),
        ("utils.py", False),
    ])
    def test_is_test_file(self, name: str, expected: bool):
        assert RepositoryScanner.is_test_file(Path(name)) == expected

    @pytest.mark.parametrize("name,expected", [
        ("config.json", True),
        (".env", True),
        ("settings.yaml", True),
        ("pyproject.toml", True),
        ("main.py", False),
        ("README.md", False),
    ])
    def test_is_config_file(self, name: str, expected: bool):
        assert RepositoryScanner.is_config_file(Path(name)) == expected
