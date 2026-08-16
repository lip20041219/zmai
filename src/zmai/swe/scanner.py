"""RepositoryScanner — 项目源码目录发现与过滤。

核心职责：
  1. 区分 user project root 与 agent runtime workspace 和内部状态目录。
  2. 扫描项目根目录，返回源码/测试/配置文件列表。
  3. 禁止扫描 workspace/、.state/、__pycache__/ 等内部目录。

使用方式:
    info = RepositoryScanner.scan(project_root)
    print(info.summary)  # → LLM 友好的项目结构描述
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger("zmai.swe.scanner")

# ── 需要排除的目录名 ─────────────────────────────────────────────

_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({
    # 内部状态目录
    "workspace", ".state", ".claude",
    # 版本控制
    ".git", ".svn", ".hg",
    # Python
    "__pycache__", ".venv", "venv", ".tox", ".eggs", "eggs",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    # Node
    "node_modules", "bower_components",
    # 构建输出
    "build", "dist", ".next", ".nuxt", "out", "target",
    # IDE
    ".idea", ".vscode", ".vs",
    # 操作系统
    ".DS_Store",  # macOS
    # 其他
    ".gitkeep", ".gitattributes", ".gitignore",  # git 文件
    "temp", "output", "input",  # workspace 子目录
    ".pip",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
})

# ── 源码文件扩展名映射 ────────────────────────────────────────────

_SOURCE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp", ".c": "c", ".h": "c-header", ".hpp": "c-header",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".ps1": "powershell", ".psm1": "powershell",
    ".bat": "batch", ".cmd": "batch",
    ".sql": "sql",
    ".r": "r",
    ".m": "matlab",
    ".dart": "dart",
    ".lua": "lua",
    ".html": "html", ".css": "css", ".scss": "scss", ".less": "less",
    ".xml": "xml", ".svg": "svg",
    ".vue": "vue",
    ".svelte": "svelte",
    ".astro": "astro",
}

# ── 配置文件扩展名 ────────────────────────────────────────────────

_CONFIG_EXTENSIONS: frozenset[str] = frozenset({
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".editorconfig",
})

# ── 测试文件名模式 ────────────────────────────────────────────────

_TEST_NAME_PATTERNS: tuple[str, ...] = (
    "test_", "_test", "Test", "spec_", "_spec", "Spec",
    "conftest", "check_",
)

# ── 项目标记文件 ──────────────────────────────────────────────────

_PROJECT_MARKERS: tuple[str, ...] = (
    ".git",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "zmai.json",
    ".zmai-root",
)


# ═══════════════════════════════════════════════════════════════════
# RepositoryInfo — 扫描结果数据结构
# ═══════════════════════════════════════════════════════════════════


@dataclass
class RepositoryInfo:
    """项目目录扫描结果。

    Attributes:
        root: 项目根目录的绝对路径。
        source_files: 源码文件列表（相对于 root）。
        test_files: 测试文件列表（相对于 root）。
        config_files: 配置文件列表（相对于 root）。
        all_files: 所有已发现文件的完整列表（相对于 root）。
        language: 主要编程语言（如 "python", "node"）。
        has_git: 是否包含 .git 目录。
        file_count: 源码文件总数（不含排除目录）。
        summary: 格式化后的项目结构描述（供 LLM 消费）。
    """

    root: Path
    source_files: list[Path] = field(default_factory=list)
    test_files: list[Path] = field(default_factory=list)
    config_files: list[Path] = field(default_factory=list)
    all_files: list[Path] = field(default_factory=list)
    language: str = "unknown"
    has_git: bool = False
    file_count: int = 0
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "language": self.language,
            "has_git": self.has_git,
            "file_count": self.file_count,
            "source_files": [str(p) for p in self.source_files],
            "test_files": [str(p) for p in self.test_files],
            "config_files": [str(p) for p in self.config_files],
        }


# ═══════════════════════════════════════════════════════════════════
# RepositoryScanner — 项目扫描器
# ═══════════════════════════════════════════════════════════════════


class RepositoryScanner:
    """项目源码目录发现器。

    职责：
      - 只扫描 user project root，杜绝 workspace/、.state/ 等内部目录。
      - 返回结构化的项目源码文件列表，供 PlanAgent / SWEAgent 使用。
      - 自动检测项目语言和入口点。

    使用方式:
        info = RepositoryScanner.scan("/path/to/project")
        if info.file_count > 0:
            planner_prompt += info.summary
    """

    EXCLUDED_DIRS: ClassVar[frozenset[str]] = _EXCLUDED_DIR_NAMES
    SOURCE_EXTENSIONS: ClassVar[dict[str, str]] = _SOURCE_EXTENSIONS
    CONFIG_EXTENSIONS: ClassVar[frozenset[str]] = _CONFIG_EXTENSIONS
    TEST_PATTERNS: ClassVar[tuple[str, ...]] = _TEST_NAME_PATTERNS
    PROJECT_MARKERS: ClassVar[tuple[str, ...]] = _PROJECT_MARKERS

    # ── 路径筛选 ──────────────────────────────────────────────

    @staticmethod
    def is_excluded_dir(name: str) -> bool:
        """检查目录名是否需要排除。

        排除规则:
          1. 在 EXCLUDED_DIRS 中
          2. 以 '.' 开头（隐藏目录）
          3. 等于 'temp'/'output'/'input'（workspace 子目录）
        """
        return (
            name in RepositoryScanner.EXCLUDED_DIRS
            or name in {"temp", "output", "input"}
            or (name.startswith(".") and name not in {".github"})
        )

    @staticmethod
    def is_source_file(path: Path) -> bool:
        """判断是否为源码文件（基于扩展名）。"""
        return path.suffix.lower() in RepositoryScanner.SOURCE_EXTENSIONS

    @staticmethod
    def is_test_file(path: Path) -> bool:
        """判断是否为测试文件（基于文件名模式）。"""
        name = path.stem  # 不含扩展名
        for pattern in RepositoryScanner.TEST_PATTERNS:
            if pattern in name:
                return True
        return False

    @staticmethod
    def is_config_file(path: Path) -> bool:
        """判断是否为配置文件（基于扩展名或文件名）。

        特殊处理:
          - .env 文件名（Python 3.11 中 Path('.env').suffix 返回 ''）
          - 以 '.' 开头的无扩展名配置文件
        """
        # 检查完整文件名（处理 .env 等点文件名）
        name = path.name
        if name in RepositoryScanner.CONFIG_EXTENSIONS:
            return True
        # 检查扩展名
        return path.suffix.lower() in RepositoryScanner.CONFIG_EXTENSIONS

    # ── 项目根目录发现 ────────────────────────────────────────

    @staticmethod
    def find_project_root(cwd: str | Path | None = None) -> Path | None:
        """从 cwd 向上遍历，找到项目根目录。

        查找顺序:
          1. 当前目录是否存在项目标记文件
          2. 依次检查父目录
          3. 到 home 目录为止

        Args:
            cwd: 起始目录（默认当前工作目录）。

        Returns:
            项目根目录的绝对路径，未找到返回 None。
        """
        start = Path(cwd).resolve() if cwd else Path.cwd().resolve()
        home = Path.home().resolve()

        for parent in [start] + list(start.parents):
            if parent == home or parent == parent.parent:
                break
            for marker in RepositoryScanner.PROJECT_MARKERS:
                if (parent / marker).exists():
                    return parent
        return None

    # ── 主扫描方法 ────────────────────────────────────────────

    @staticmethod
    def scan(root: str | Path, max_files: int = 500) -> RepositoryInfo:
        """扫描项目根目录，返回结构化的文件信息。

        扫描规则:
          - 只扫描 root 下的目录
          - 跳过 is_excluded_dir() 返回 True 的目录
          - 跳过以 '.' 开头的隐藏目录（.github 等白名单除外）
          - 文件分类：源码 / 测试 / 配置 / 其他
          - 最多扫描 max_files 个文件（防止超大型项目的无限遍历）

        Args:
            root: 项目根目录路径。
            max_files: 最大文件扫描数（默认 500，超过即停止）。

        Returns:
            RepositoryInfo 对象。

        Raises:
            NotADirectoryError: root 不是目录。
            FileNotFoundError: root 不存在。
        """
        root_path = Path(root).resolve()

        if not root_path.exists():
            raise FileNotFoundError(f"项目目录不存在: {root_path}")
        if not root_path.is_dir():
            raise NotADirectoryError(f"项目路径不是目录: {root_path}")

        source_files: list[Path] = []
        test_files: list[Path] = []
        config_files: list[Path] = []
        all_files: list[Path] = []
        language_counts: dict[str, int] = {}
        has_git = (root_path / ".git").exists()

        scanned = 0

        for entry in sorted(root_path.rglob("*")):
            # 跳过根目录自身
            if entry == root_path:
                continue

            # 检查是否需要排除此路径
            rel = entry.relative_to(root_path)
            parts = rel.parts

            # 跳过排除目录内的所有文件
            skip = False
            for part in parts[:-1]:  # 只检查目录部分（不检查文件名本身）
                if RepositoryScanner.is_excluded_dir(part):
                    skip = True
                    break
            if skip:
                continue

            # 跳过隐藏文件（以 '.' 开头的文件，白名单除外）
            if entry.name.startswith(".") and entry.name not in {".github", ".claude", ".gitignore", ".env"}:  # noqa: E501
                continue

            if not entry.is_file():
                continue

            scanned += 1
            if scanned > max_files:
                logger.warning("扫描文件数超过 %d，已截断", max_files)
                break

            # 相对路径
            all_files.append(rel)

            if RepositoryScanner.is_source_file(entry):
                source_files.append(rel)
                ext = entry.suffix.lower()
                lang = RepositoryScanner.SOURCE_EXTENSIONS.get(ext, "other")
                language_counts[lang] = language_counts.get(lang, 0) + 1

                if RepositoryScanner.is_test_file(entry):
                    test_files.append(rel)
            elif RepositoryScanner.is_config_file(entry):
                config_files.append(rel)

        # 确定主要语言
        primary_lang = "unknown"
        if language_counts:
            primary_lang = max(language_counts, key=language_counts.get)

        info = RepositoryInfo(
            root=root_path,
            source_files=source_files,
            test_files=test_files,
            config_files=config_files,
            all_files=all_files,
            language=primary_lang,
            has_git=has_git,
            file_count=len(source_files) + len(config_files),
            summary="",
        )
        info.summary = RepositoryScanner._build_summary(info)
        return info

    # ── 格式化输出 ────────────────────────────────────────────

    @staticmethod
    def _build_summary(info: RepositoryInfo, max_files: int = 30) -> str:
        """构建 LLM 友好的项目结构摘要。"""
        lines = [
            f"## 项目结构 ({info.language})",
            f"根目录: {info.root}",
            f"源码文件: {len(info.source_files)}  |  测试文件: {len(info.test_files)}",
            f"Git: {'是' if info.has_git else '否'}",
            "",
        ]

        # 按目录分组展示源码文件
        if info.source_files:
            lines.append("### 源码文件")
            dirs: dict[str, list[str]] = {}
            for f in info.source_files[:max_files]:
                parent = str(f.parent) if str(f.parent) != "." else "/"
                dirs.setdefault(parent, []).append(f.name)

            for directory in sorted(dirs):
                files = sorted(dirs[directory])
                indent = "  " * (directory.count("/") + 1 if directory != "/" else 1)
                label = directory if directory != "/" else "(根目录)"
                lines.append(f"{indent}{label}/")
                for fname in files:
                    lines.append(f"{indent}  {fname}")

            if len(info.source_files) > max_files:
                lines.append(f"  ... +{len(info.source_files) - max_files} 个文件")

        # 测试文件
        if info.test_files:
            lines.append("")
            lines.append(f"### 测试文件 ({len(info.test_files)})")
            for f in info.test_files[:10]:
                lines.append(f"  - {f}")
            if len(info.test_files) > 10:
                lines.append(f"  ... +{len(info.test_files) - 10} 个测试文件")

        return "\n".join(lines)

    @staticmethod
    def format_compact(info: RepositoryInfo) -> str:
        """紧凑格式 — 用于系统提示中的项目上下文。"""
        parts = [f"[项目] {info.root.name}  ({info.language})"]
        if info.source_files:
            src_list = ", ".join(str(f) for f in info.source_files[:15])
            if len(info.source_files) > 15:
                src_list += f" ... (+{len(info.source_files) - 15})"
            parts.append(f"  源码: {src_list}")
        if info.test_files:
            test_list = ", ".join(str(f) for f in info.test_files[:8])
            if len(info.test_files) > 8:
                test_list += f" ... (+{len(info.test_files) - 8})"
            parts.append(f"  测试: {test_list}")
        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════


def scan_repository(root: str | Path) -> RepositoryInfo:
    """快捷方式：扫描项目目录并返回 RepositoryInfo。"""
    return RepositoryScanner.scan(root)
