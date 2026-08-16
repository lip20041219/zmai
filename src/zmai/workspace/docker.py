"""Docker sandbox — isolated execution environment for agents.

Provides:
  - Docker availability detection
  - Container lifecycle management (create, start, stop, remove)
  - Command execution inside containers
  - File copy between host and container
  - Integration with the Workspace system

All operations use subprocess (docker CLI) — no Docker SDK dependency.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("zmai.workspace.docker")

# ── Defaults ─────────────────────────────────────────────────────

DEFAULT_IMAGE = "python:3.11-slim"
DEFAULT_TIMEOUT = 300  # 5 min
MAX_CONTAINER_NAME_LEN = 64

# Standard images per language
LANGUAGE_IMAGES: dict[str, str] = {
    "python": "python:3.11-slim",
    "python3.10": "python:3.10-slim",
    "python3.11": "python:3.11-slim",
    "python3.12": "python:3.12-slim",
    "node": "node:20-slim",
    "go": "golang:1.22",
    "rust": "rust:1.77-slim",
    "java": "eclipse-temurin:21",
}


# ── Exceptions ────────────────────────────────────────────────────


class DockerError(Exception):
    """Base error for Docker operations."""


class DockerNotAvailable(DockerError):
    """Docker is not installed or not running."""


class ContainerError(DockerError):
    """Container operation failed."""


# ── Availability Check ────────────────────────────────────────────


def is_docker_available() -> bool:
    """Check if Docker is installed and the daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def require_docker() -> None:
    """Raise DockerNotAvailable if Docker is not available."""
    if not is_docker_available():
        raise DockerNotAvailable(
            "Docker is not available. Install Docker Desktop or the Docker CLI."
        )


# ── Container Lifecycle ───────────────────────────────────────────


def _sanitize_name(name: str) -> str:
    """Sanitize a container name to be Docker-compatible."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return safe[:MAX_CONTAINER_NAME_LEN].lower()


@dataclass
class ContainerSpec:
    """Specification for creating a Docker container."""

    image: str = DEFAULT_IMAGE
    name: str = ""
    workdir: str = "/workspace"
    volumes: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    command: str = "sleep 3600"  # Keep alive
    memory_limit: str = "2g"
    network_disabled: bool = False
    remove_on_exit: bool = True

    def to_docker_args(self) -> list[str]:
        """Convert spec to docker run CLI arguments."""
        args = []

        if self.name:
            args.extend(["--name", _sanitize_name(self.name)])

        if self.workdir:
            args.extend(["-w", self.workdir])

        for host_path, container_path in self.volumes.items():
            args.extend(["-v", f"{host_path}:{container_path}"])

        for key, val in self.env.items():
            args.extend(["-e", f"{key}={val}"])

        if self.memory_limit:
            args.extend(["-m", self.memory_limit])

        if self.network_disabled:
            args.append("--network=none")

        if self.remove_on_exit:
            args.append("--rm")

        # Interactive + daemon
        args.extend(["-i", "-d"])

        args.append(self.image)

        if self.command:
            args.extend(["sh", "-c", self.command])

        return args


def create_container(spec: ContainerSpec) -> str:
    """Create and start a Docker container.

    Args:
        spec: Container specification.

    Returns:
        Container ID.

    Raises:
        DockerNotAvailable: Docker is not available.
        ContainerError: Container creation failed.
    """
    require_docker()

    # Remove existing container with the same name
    if spec.name:
        try:
            subprocess.run(
                ["docker", "rm", "-f", _sanitize_name(spec.name)],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:
            pass

    args = ["docker", "run"] + spec.to_docker_args()
    logger.debug("Creating container: %s", " ".join(str(a) for a in args))

    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise ContainerError(
            f"Container creation failed: {result.stderr[:500]}"
        )

    container_id = result.stdout.strip()
    logger.info("Container created: %s", container_id[:12])
    return container_id


def stop_container(container_id: str, timeout: int = 10) -> None:
    """Stop and remove a container."""
    try:
        subprocess.run(
            ["docker", "stop", "-t", str(timeout), container_id],
            capture_output=True, text=True, timeout=timeout + 10,
        )
    except Exception as e:
        logger.warning("Failed to stop container %s: %s", container_id[:12], e)


def exec_in_container(
    container_id: str,
    command: str,
    workdir: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str, str]:
    """Execute a command inside a running container.

    Args:
        container_id: Container ID or name.
        command: Shell command to execute.
        workdir: Working directory inside container (None = default).
        timeout: Command timeout in seconds.

    Returns:
        (exit_code, stdout, stderr)
    """
    args = ["docker", "exec"]
    if workdir:
        args.extend(["-w", workdir])
    args.extend([container_id, "sh", "-c", command])

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"Timeout ({timeout}s)"
    except Exception as e:
        return -1, "", str(e)


def copy_to_container(container_id: str, src: str | Path, dest: str) -> None:
    """Copy a file or directory into the container.

    Args:
        container_id: Container ID or name.
        src: Host source path.
        dest: Container destination path.
    """
    result = subprocess.run(
        ["docker", "cp", str(src), f"{container_id}:{dest}"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise ContainerError(f"Copy to container failed: {result.stderr[:500]}")


def copy_from_container(container_id: str, src: str, dest: str | Path) -> None:
    """Copy a file or directory from the container.

    Args:
        container_id: Container ID or name.
        src: Container source path.
        dest: Host destination path.
    """
    result = subprocess.run(
        ["docker", "cp", f"{container_id}:{src}", str(dest)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise ContainerError(
            f"Copy from container failed: {result.stderr[:500]}"
        )


# ── Sandbox Manager ───────────────────────────────────────────────


class DockerSandbox:
    """Managed Docker sandbox for isolated agent execution.

    Usage:
        with DockerSandbox(image="python:3.11-slim") as sandbox:
            sandbox.copy_workspace("/path/to/project")
            code, out, err = sandbox.run("python test.py")
            sandbox.copy_results("/path/to/output")
    """

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        name: str = "",
        workdir: str = "/workspace",
        memory_limit: str = "2g",
        env: dict[str, str] | None = None,
    ) -> None:
        self.image = image
        self.name = name or f"zmai_{os.getpid()}_{int(time.time())}"
        self.workdir = workdir
        self.memory_limit = memory_limit
        self.env = env or {}
        self._container_id: str = ""
        self._temp_dir: Path | None = None

    @property
    def container_id(self) -> str:
        if not self._container_id:
            raise DockerError("Container not created. Use __enter__ or create().")
        return self._container_id

    @property
    def is_running(self) -> bool:
        if not self._container_id:
            return False
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}",
                 self._container_id],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() == "true"
        except Exception:
            return False

    def create(self) -> str:
        """Create and start the sandbox container."""
        spec = ContainerSpec(
            image=self.image,
            name=self.name,
            workdir=self.workdir,
            memory_limit=self.memory_limit,
            env=self.env,
        )
        self._container_id = create_container(spec)
        return self._container_id

    def run(
        self,
        command: str,
        workdir: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> tuple[int, str, str]:
        """Execute a command inside the sandbox.

        Returns:
            (exit_code, stdout, stderr)
        """
        return exec_in_container(
            self.container_id, command,
            workdir=workdir or self.workdir,
            timeout=timeout,
        )

    def copy_to(self, src: str | Path, dest: str) -> None:
        """Copy files from host to sandbox."""
        copy_to_container(self.container_id, src, dest)

    def copy_from(self, src: str, dest: str | Path) -> None:
        """Copy files from sandbox to host."""
        copy_from_container(self.container_id, src, dest)

    def copy_workspace(self, host_path: str | Path, container_path: str = "") -> None:
        """Copy a workspace directory into the sandbox.

        Uses docker cp (not volume mount) for simplicity.
        """
        if not container_path:
            container_path = self.workdir
        # Create workdir
        self.run(f"mkdir -p {container_path}", timeout=30)
        # Copy files
        self.copy_to(host_path, container_path)

    def ensure_python(self, packages: list[str] | None = None) -> None:
        """Ensure Python and optionally install packages."""
        # Python should already be there with slim images
        if packages:
            pkgs = " ".join(packages)
            code, out, err = self.run(f"pip install {pkgs} --quiet", timeout=120)
            if code != 0:
                logger.warning("pip install failed: %s", err[:200])

    def close(self) -> None:
        """Stop and remove the sandbox container."""
        if self._container_id:
            try:
                stop_container(self._container_id)
            except Exception as e:
                logger.warning("Error stopping container: %s", e)
            self._container_id = ""

    def __enter__(self) -> DockerSandbox:
        self.create()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
