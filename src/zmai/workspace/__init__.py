"""ZMAI Workspace — agent-isolated file system sandbox."""

from zmai.workspace.docker import DockerSandbox, is_docker_available
from zmai.workspace.workspace import Workspace

__all__ = ["Workspace", "DockerSandbox", "is_docker_available"]
