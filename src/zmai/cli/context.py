"""ContextBuilder — 运行所有检测器，构建项目上下文。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zmai.cli.detectors import Detector, ProjectContext
from zmai.cli.detectors.docker_detector import DockerDetector
from zmai.cli.detectors.git_detector import GitDetector
from zmai.cli.detectors.lang import GoDetector, NodeDetector, PythonDetector, RustDetector
from zmai.cli.detectors.monorepo import MonorepoDetector
from zmai.cli.detectors import PackageInfo


_DETECTORS: list[Detector] = [
    MonorepoDetector(),
    PythonDetector(),
    NodeDetector(),
    RustDetector(),
    GoDetector(),
    DockerDetector(),
    GitDetector(),
]


def build_context(root: Path) -> ProjectContext:
    """运行全部检测器，合并结果。"""
    ctx = ProjectContext(
        root=root,
        name=root.name,
    )

    for detector in sorted(_DETECTORS, key=lambda d: d.priority):
        try:
            result = detector.detect(root)
            if result:
                _merge(ctx, result)
        except Exception:
            continue

    return ctx


def _merge(ctx: ProjectContext, data: dict[str, Any]) -> None:
    """将检测结果合并到 ProjectContext。"""
    # 类型 — Monorepo 覆盖其他类型检测
    if data.get("is_monorepo"):
        ctx.is_monorepo = True
        ctx.type = data.get("type", "monorepo")
        ctx.packages = [
            PackageInfo(**p) if isinstance(p, dict) else p
            for p in data.get("packages", [])
        ]
        return

    # 语言信息
    if t := data.get("type"):
        ctx.type = t
    if v := data.get("language_version"):
        ctx.language_version = v
    if pm := data.get("package_manager"):
        ctx.package_manager = pm
    if tf := data.get("test_framework"):
        ctx.test_framework = tf
    if bt := data.get("build_tool"):
        ctx.build_tool = bt
    if linter := data.get("linter"):
        ctx.linter = linter

    # 目录
    if sd := data.get("src_dirs"):
        ctx.src_dirs = sd
    if td := data.get("test_dirs"):
        ctx.test_dirs = td

    # Git
    if gb := data.get("git_branch"):
        ctx.git_branch = gb
    if data.get("git_has_uncommitted"):
        ctx.git_has_uncommitted = True
    if gr := data.get("git_remote"):
        ctx.git_remote = gr

    # Docker
    if data.get("has_dockerfile"):
        ctx.has_dockerfile = True
    if data.get("has_docker_compose"):
        ctx.has_docker_compose = True
