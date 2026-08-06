"""Git 状态检测器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zmai.cli.detectors import Detector, _run_git


class GitDetector(Detector):
    priority = 200
    name = "git"

    def detect(self, root: Path) -> dict[str, Any] | None:
        if not (root / ".git").exists():
            return None

        return {
            "git_branch": _run_git(root, "rev-parse --abbrev-ref HEAD"),
            "git_remote": _run_git(root, "remote get-url origin 2>/dev/null"),
        }
