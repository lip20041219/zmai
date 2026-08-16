"""Python 项目检测器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zmai.cli.detectors import Detector, _read_json, _read_toml


class PythonDetector(Detector):
    priority = 100
    name = "python"

    def detect(self, root: Path) -> dict[str, Any] | None:
        pyproject = _read_toml(root / "pyproject.toml")
        setup_py = (root / "setup.py").exists()
        setup_cfg = (root / "setup.cfg").exists()
        req_txt = (root / "requirements.txt").exists()

        if not pyproject and not setup_py and not setup_cfg and not req_txt:
            return None

        result: dict[str, Any] = {
            "type": "python",
            "language_version": "",
            "package_manager": "pip",
            "test_framework": "",
            "linter": "",
            "build_tool": "setuptools",
            "src_dirs": [],
            "test_dirs": ["tests"],
        }

        # 语言版本
        if pyproject:
            rp = pyproject.get("project", {})
            rpv = rp.get("requires-python", "")
            if rpv:
                import re
                m = re.search(r"(\d+\.\d+)", rpv)
                if m:
                    result["language_version"] = m.group(1)
        pv_file = root / ".python-version"
        if pv_file.exists():
            result["language_version"] = pv_file.read_text().strip()

        # 包管理器
        if pyproject:
            build_backend = pyproject.get("build-system", {}).get("build-backend", "")
            if "poetry" in build_backend:
                result["package_manager"] = "poetry"
            elif "pdm" in build_backend:
                result["package_manager"] = "pdm"
            uv = _read_toml(root / "uv.lock")
            if uv or (root / "uv.lock").exists():
                result["package_manager"] = "uv"

        # 测试框架
        if pyproject:
            tool = pyproject.get("tool", {})
            if "pytest" in tool:
                result["test_framework"] = "pytest"
            elif "unittest" in tool:
                result["test_framework"] = "unittest"

        # Linter
        if pyproject:
            tool = pyproject.get("tool", {})
            if "ruff" in tool:
                result["linter"] = "ruff"
            elif "pylint" in tool:
                result["linter"] = "pylint"
        if (root / ".flake8").exists():
            result["linter"] = "flake8"
        if (root / "ruff.toml").exists() or (root / ".ruff.toml").exists():
            result["linter"] = "ruff"

        # 源目录
        if pyproject:
            where = pyproject.get("tool", {}).get("setuptools", {}).get("packages", {}).get("where", [])  # noqa: E501
            if where:
                result["src_dirs"] = where
        if not result["src_dirs"]:
            srcd = root / "src"
            if srcd.is_dir():
                result["src_dirs"] = ["src"]
            else:
                result["src_dirs"] = ["."]
        if not (root / "tests").exists() and (root / "test").exists():
            result["test_dirs"] = ["test"]

        return result


class NodeDetector(Detector):
    priority = 100
    name = "node"

    def detect(self, root: Path) -> dict[str, Any] | None:
        pkg = _read_json(root / "package.json")
        if not pkg:
            return None

        result: dict[str, Any] = {
            "type": "node",
            "language_version": "",
            "package_manager": "npm",
            "test_framework": "",
            "linter": "",
            "build_tool": "",
            "src_dirs": ["src"],
            "test_dirs": ["__tests__"],
        }

        # 语言版本
        engines = pkg.get("engines", {})
        result["language_version"] = engines.get("node", "")
        for f in (root / ".nvmrc", root / ".node-version"):
            if f.exists():
                result["language_version"] = f.read_text().strip()
                break

        # 包管理器
        if (root / "pnpm-lock.yaml").exists():
            result["package_manager"] = "pnpm"
        elif (root / "yarn.lock").exists():
            result["package_manager"] = "yarn"

        # 测试框架
        dev = pkg.get("devDependencies", {})
        for fw in ("vitest", "jest", "mocha", "ava", "tape"):
            if fw in dev:
                result["test_framework"] = fw
                break

        # Linter
        for linter in ("eslint", "biome", "prettier"):
            if linter in dev:
                result["linter"] = linter
                break

        # 源目录
        if not (root / "src").exists() and (root / "lib").exists():
            result["src_dirs"] = ["lib"]
        elif (root / "app").exists():
            result["src_dirs"] = ["app"]

        return result


class RustDetector(Detector):
    priority = 100
    name = "rust"

    def detect(self, root: Path) -> dict[str, Any] | None:
        cargo = _read_toml(root / "Cargo.toml")
        if not cargo:
            return None

        result: dict[str, Any] = {
            "type": "rust",
            "language_version": "",
            "package_manager": "cargo",
            "test_framework": "cargo-test",
            "linter": "clippy",
            "build_tool": "cargo",
            "src_dirs": ["src"],
            "test_dirs": ["tests"],
        }

        # Edition 作为版本参考
        pkg = cargo.get("package", {})
        result["language_version"] = pkg.get("edition", "")

        # toolchain file
        for f in ("rust-toolchain.toml", "rust-toolchain"):
            tf = root / f
            if tf.exists():
                content = tf.read_text()
                import re
                m = re.search(r'channel\s*=\s*"([^"]+)"', content)
                if m:
                    result["language_version"] = m.group(1)
                break

        return result


class GoDetector(Detector):
    priority = 100
    name = "go"

    def detect(self, root: Path) -> dict[str, Any] | None:
        if not (root / "go.mod").exists():
            return None

        result: dict[str, Any] = {
            "type": "go",
            "language_version": "",
            "package_manager": "go",
            "test_framework": "go-test",
            "linter": "",
            "build_tool": "go",
            "src_dirs": ["."],
            "test_dirs": ["."],
        }

        # 版本
        for line in (root / "go.mod").read_text().splitlines():
            if line.startswith("go "):
                result["language_version"] = line[3:].strip()
                break

        # Linter
        for f in (".golangci.yml", ".golangci.yaml", ".golangci.toml"):
            if (root / f).exists():
                result["linter"] = "golangci-lint"
                break

        return result
