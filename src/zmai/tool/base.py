"""Tool 抽象基类与数据类型。

所有工具必须继承 Tool(ABC) 并实现 execute() 方法。
提供 ToolContext、ToolResult、ToolCall、ToolDefinition 数据类。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolContext:
    """工具执行上下文。

    Attributes:
        agent_id: 调用方 Agent ID。
        workspace_path: Agent 工作目录。
        config: 工具自定义配置字典。
        timeout: 执行超时（秒），默认 120。
        env: 环境变量字典。
        logger: 日志记录器。
    """

    agent_id: str
    workspace_path: Path
    project_path: Path | None = None
    config: dict[str, Any] = field(default_factory=dict)
    timeout: int = 120
    env: dict[str, str] = field(default_factory=dict)
    logger: logging.Logger | None = None


@dataclass
class ToolResult:
    """工具执行结果。

    Attributes:
        success: 执行是否成功。
        output: 执行输出文本。
        error: 错误信息（success=False 时必须提供）。
        metadata: 额外元数据（执行时间、文件路径等）。
        duration_ms: 执行耗时（毫秒）。
    """

    success: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def ok(cls, output: str = "", metadata: dict[str, Any] | None = None) -> ToolResult:
        """快速创建成功结果。"""
        return cls(success=True, output=output, metadata=metadata or {})

    @classmethod
    def err(cls, error: str, metadata: dict[str, Any] | None = None) -> ToolResult:
        """快速创建失败结果。"""
        return cls(success=False, output="", error=error, metadata=metadata or {})


@dataclass(frozen=True)
class ToolCall:
    """LLM 发起的工具调用请求。

    Attributes:
        id: 工具调用唯一 ID（由 LLM 生成）。
        name: 工具名称。
        params: 工具参数字典。
    """

    id: str
    name: str
    params: dict[str, Any]


@dataclass(frozen=True)
class ToolDefinition:
    """工具定义，供 LLM 理解工具的用途和参数格式。

    Attributes:
        name: 工具名称，唯一标识。
        description: 工具描述。
        input_schema: JSON Schema 格式的参数定义。
    """

    name: str
    description: str
    input_schema: dict[str, Any]


class Tool(ABC):
    """工具抽象基类。

    所有工具实现必须：
    1. 设置 name、description、parameters 类属性
    2. 实现 execute() 方法

    使用方式:
        class MyTool(Tool):
            name = "my_tool"
            description = "Do something"
            parameters = {"type": "object", "properties": {}}

            def execute(self, context: ToolContext, params: dict) -> ToolResult:
                return ToolResult.ok("done")
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name:
            raise TypeError(f"Tool subclass {cls.__name__} must define 'name'")
        if not cls.description:
            raise TypeError(f"Tool subclass {cls.__name__} must define 'description'")

    @abstractmethod
    def execute(self, context: ToolContext, params: dict[str, Any]) -> ToolResult:
        """执行工具。

        Args:
            context: 执行上下文。
            params: 执行参数（须符合 self.parameters JSON Schema）。

        Returns:
            工具执行结果。
        """
        ...

    def validate(self, params: dict[str, Any]) -> bool:
        """校验参数是否符合 JSON Schema。

        基类提供基础类型检查、enum 约束检查，子类可覆盖实现严格校验。

        Args:
            params: 待校验的参数。

        Returns:
            True 通过，False 不通过。
        """
        schema = self.parameters
        if not schema:
            return True
        props = schema.get("properties", {})
        required = schema.get("required", [])

        for key in required:
            if key not in params:
                return False

        for key, val in params.items():
            if key in props:
                prop = props[key]
                expected = prop.get("type")
                if expected and val is not None:
                    if expected == "string" and not isinstance(val, str):
                        return False
                    if expected in ("integer", "number") and not isinstance(val, int | float):
                        return False
                    if expected == "boolean" and not isinstance(val, bool):
                        return False
                        return False
                    if expected == "object" and not isinstance(val, dict):
                        return False
                # 校验 enum 约束
                if "enum" in prop and val not in prop["enum"]:
                    return False
        return True

    def to_definition(self) -> ToolDefinition:
        """生成 LLM 可消费的工具定义。"""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.parameters,
        )
