"""Monorepo 检测器 — 启发式检测 Monorepo 结构。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zmai.cli.detectors import Detector, _read_json, _read_toml, PackageInfo


class MonorepoDetector(Detector):
    priority = 50  # 最高优先级，先于语言检测
    name = "monorepo"

    def detect(self, root: Path) -> dict[str, Any] | None:
        indicators: list[str] = []

        # pnpm workspace
        if (root / "pnpm-workspace.yaml").exists():
            indicators.append("pnpm-workspace")

        # npm/pnpm/yarn workspaces
        pkg = _read_json(root / "package.json")
        if pkg and "workspaces" in pkg:
            indicators.append("npm-workspace")

        # cargo workspace
        cargo = _read_toml(root / "Cargo.toml")
        if cargo and "workspace" in cargo:
            indicators.append("cargo-workspace")

        # go workspace
        if (root / "go.work").exists():
            indicators.append("go-workspace")

        # uv workspace
        pyproject = _read_toml(root / "pyproject.toml")
        if pyproject:
            uv_cfg = pyproject.get("tool", {}).get("uv", {})
            if uv_cfg and isinstance(uv_cfg, dict) and uv_cfg.get("workspace"):
                indicators.append("uv-workspace")

        # 目录结构启发式
        mono_dirs = [d for d in root.iterdir()
                     if d.is_dir() and d.name in ("packages", "apps", "modules", "services", "crates")]
        if len(mono_dirs) >= 2:
            indicators.append("monorepo-structure")
        elif len(mono_dirs) == 1:
            sub_count = len(list(mono_dirs[0].iterdir()))
            if sub_count >= 2:
                indicators.append("monorepo-structure")

        if len(indicators) < 1:
            # 检查是否多个子项目各有独立标记文件
            sub_projects = 0
            for d in root.iterdir():
                if not d.is_dir() or d.name.startswith("."):
                    continue
                if any((d / m).exists() for m in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod")):
                    sub_projects += 1
            if sub_projects >= 3:
                indicators.append("multi-language")

        if not indicators:
            return None

        packages = self._discover_packages(root)
        pkg_types = [p.type for p in packages if p.type]

        return {
            "is_monorepo": True,
            "indicators": indicators,
            "packages": packages,
            "type": "monorepo" if len(set(pkg_types)) <= 1 else "monorepo-multi",
        }

    def _discover_packages(self, root: Path) -> list[dict[str, Any]]:
        packages: list[dict[str, Any]] = []

        # pnpm / npm workspaces
        pkg = _read_json(root / "package.json")
        if pkg and "workspaces" in pkg:
            workspaces = pkg["workspaces"]
            for pattern in workspaces:
                base = pattern.replace("/*", "")
                pdir = root / base
                if pdir.is_dir():
                    for sub in sorted(pdir.iterdir()):
                        if sub.is_dir():
                            spkg = _read_json(sub / "package.json")
                            if spkg:
                                packages.append({
                                    "name": spkg.get("name", sub.name),
                                    "path": str(sub.relative_to(root)),
                                    "type": "node",
                                })

        # cargo workspace
        cargo = _read_toml(root / "Cargo.toml")
        if cargo:
            ws = cargo.get("workspace", {})
            for member in ws.get("members", []):
                mp = root / member
                if mp.is_dir():
                    mcargo = _read_toml(mp / "Cargo.toml")
                    packages.append({
                        "name": mcargo.get("package", {}).get("name", mp.name) if mcargo else mp.name,
                        "path": member,
                        "type": "rust",
                    })

        # 目录下发现有 pyproject.toml 的子目录
        for base in ("packages", "apps", "modules", "libs"):
            bdir = root / base
            if bdir.is_dir():
                for sub in sorted(bdir.iterdir()):
                    if not sub.is_dir():
                        continue
                    name = sub.name
                    if (sub / "pyproject.toml").exists():
                        packages.append({"name": name, "path": f"{base}/{name}", "type": "python"})
                    elif (sub / "Cargo.toml").exists():
                        packages.append({"name": name, "path": f"{base}/{name}", "type": "rust"})
                    elif (sub / "go.mod").exists():
                        packages.append({"name": name, "path": f"{base}/{name}", "type": "go"})
                    elif (sub / "package.json").exists():
                        packages.append({"name": name, "path": f"{base}/{name}", "type": "node"})

        return packages
