"""Docker 检测器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zmai.cli.detectors import Detector


class DockerDetector(Detector):
    priority = 150
    name = "docker"

    def detect(self, root: Path) -> dict[str, Any] | None:
        result: dict[str, Any] = {}

        if (root / "Dockerfile").exists():
            result["has_dockerfile"] = True
        elif (root / "Containerfile").exists():
            result["has_dockerfile"] = True

        if (root / "docker-compose.yml").exists() or (root / "docker-compose.yaml").exists():
            result["has_docker_compose"] = True

        return result if result else None
