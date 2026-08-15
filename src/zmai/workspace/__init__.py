"""ZMAI Workspace — agent-isolated file system sandbox."""

from zmai.workspace.workspace import Workspace
from zmai.workspace.docker import DockerSandbox, is_docker_available

__all__ = ["Workspace", "DockerSandbox", "is_docker_available"]
