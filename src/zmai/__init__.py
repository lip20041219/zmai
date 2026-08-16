"""ZMAI — Model-Agnostic Agent Runtime."""

__version__ = "0.1.0"

from zmai.agent import Agent, AgentAction, AgentContext, AgentResult, AgentState
from zmai.config import Config
from zmai.errors import (
    AgentError,
    BackendError,
    ConfigError,
    MemoryError,
    PluginError,
    RuntimeError,
    ToolError,
    WorkspaceError,
    ZMAIError,
)
from zmai.gateway import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRegistry,
    BackendRequest,
    BackendResponse,
    MCPClient,
    TokenUsage,
    ToolRouter,
)
from zmai.memory import Memory, MemoryManager
from zmai.prompt import PromptEngine, PromptRole, PromptType
from zmai.runtime import Runtime
from zmai.tool import Tool, ToolCall, ToolContext, ToolDefinition, ToolRegistry, ToolResult
from zmai.workflow import Workflow, WorkflowEngine, WorkflowStep

__all__ = [
    "Agent",
    "AgentAction",
    "AgentContext",
    "AgentError",
    "AgentResult",
    "AgentState",
    "Backend",
    "BackendCapability",
    "BackendError",
    "BackendEvent",
    "BackendRegistry",
    "BackendRequest",
    "BackendResponse",
    "Config",
    "ConfigError",
    "MCPClient",
    "Memory",
    "MemoryError",
    "MemoryManager",
    "PluginError",
    "PromptEngine",
    "PromptRole",
    "PromptType",
    "Runtime",
    "RuntimeError",
    "TokenUsage",
    "Tool",
    "ToolCall",
    "ToolContext",
    "ToolDefinition",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolRouter",
    "WorkspaceError",
    "Workflow",
    "WorkflowEngine",
    "WorkflowStep",
    "ZMAIError",
]

