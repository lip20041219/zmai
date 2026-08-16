"""detectors — 项目类型检测器注册与接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PackageInfo:
    name: str
    path: str
    type: str = ""


@dataclass
class ProjectContext:
    root: Path
    name: str
    type: str = "unknown"          # python | node | rust | go | java | csharp | docker | monorepo | unknown
    language_version: str = ""
    package_manager: str = ""
    test_framework: str = ""
    build_tool: str = ""
    linter: str = ""
    test_dirs: list[str] = field(default_factory=list)
    workspace_dirs: list[str] = field(default_factory=list)
    is_monorepo: bool = False
    packages: list[PackageInfo] = field(default_factory=list)
    git_branch: str = ""
    git_has_uncommitted: bool = False
    git_remote: str = ""
    has_dockerfile: bool = False
    has_docker_compose: bool = False
    ci_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "version": self.language_version,
            "root": str(self.root),
            "tools": {
                "package_manager": self.package_manager,
                "test_framework": self.test_framework,
                "linter": self.linter,
                "build_tool": self.build_tool,
            },
            "structure": {
                "src": self.src_dirs,
                "tests": self.test_dirs,
                "is_monorepo": self.is_monorepo,
                "packages": [{"name": p.name, "path": p.path, "type": p.type} for p in self.packages],
            },
            "git": {
                "branch": self.git_branch,
                "has_uncommitted": self.git_has_uncommitted,
                "remote": self.git_remote,
            },
            "docker": {
                "has_dockerfile": self.has_dockerfile,
                "has_docker_compose": self.has_docker_compose,
            },
        }

    def summary(self) -> str:
        parts = [self.name]
        if self.type != "unknown":
            parts.append(f"({self.type}")
            if self.language_version:
                parts[-1] += f" {self.language_version}"
            parts[-1] += ")"
        if self.is_monorepo:
            parts.append(f"monorepo/{len(self.packages)}pkgs")
        if self.test_framework:
            parts.append(f"test:{self.test_framework}")
        if self.git_branch:
            parts.append(f"git:{self.git_branch}")
        return " ".join(parts)


class Detector(ABC):
    """检测器基类。子类实现 detect() 返回类型特定信息。"""

    priority: int = 100
    name: str = ""

    @abstractmethod
    def detect(self, root: Path) -> dict[str, Any] | None:
        ...


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.exists() and path.stat().st_size > 0:
            import json
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _read_toml(path: Path) -> dict[str, Any] | None:
    try:
        if path.exists() and path.stat().st_size > 0:
            try:
                import tomllib
            except ModuleNotFoundError:  # Python 3.10 及以下
                import tomli as tomllib  # type: ignore[no-redef]

            return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None

def _run_git(root: Path, cmd: str) -> str:
    import subprocess
    try:
        r = subprocess.run(
            ["git"] + cmd.split(),
            cwd=str(root), capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()
    except Exception:
        return ""
