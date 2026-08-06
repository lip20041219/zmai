"""ToolRouter — 工具调用路由。

将 LLM 发起的工具调用请求路由到正确的 Tool 实现并执行。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from zmai.errors import BackendError
from zmai.tool import ToolCall, ToolContext, ToolDefinition, ToolResult
from zmai.tool.registry import ToolRegistry

logger = logging.getLogger("zmai.gateway.tool_router")


class ToolRouter:
    """工具调用路由。

    负责将 LLM 的 ToolCall 分发给 ToolRegistry 中注册的 Tool 执行。
    支持超时控制和结果格式化。

    使用方式:
        router = ToolRouter(tool_registry)
        result = router.execute(tool_call, context)
    """

    def __init__(self, registry: ToolRegistry, config: dict[str, Any] | None = None) -> None:
        self._registry = registry
        self._config: dict[str, Any] = config or {}
        self._default_timeout: int = self._config.get("default_timeout", 60)

    def execute(self, tool_call: ToolCall, context: ToolContext) -> ToolResult:
        """执行工具调用。

        Args:
            tool_call: LLM 发起的工具调用请求。
            context: 执行上下文。

        Returns:
            工具执行结果。

        Raises:
            BackendError: 工具未注册时抛出。
        """
        try:
            tool = self._registry.get(tool_call.name)
        except Exception as e:
            raise BackendError(
                f"工具未注册: {tool_call.name}",
            ) from e

        logger.debug("执行工具: %s (id=%s)", tool_call.name, tool_call.id)

        effective_timeout = context.timeout or self._default_timeout
        exec_context = ToolContext(
            agent_id=context.agent_id,
            workspace_path=context.workspace_path,
            project_path=context.project_path,
            config={**context.config, **tool_call.params.get("_config", {})},
            timeout=effective_timeout,
            env=context.env,
            logger=context.logger,
        )

        start = time.monotonic()
        try:
            result = tool.execute(exec_context, tool_call.params)
            result.duration_ms = int((time.monotonic() - start) * 1000)
            logger.debug("工具执行完成: %s (%d ms)", tool_call.name, result.duration_ms)
            return result
        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("工具执行失败: %s (%d ms): %s", tool_call.name, elapsed, e)
            return ToolResult(
                success=False,
                output="",
                error=str(e),
                duration_ms=elapsed,
            )

    def definitions(self) -> list[ToolDefinition]:
        """获取所有已注册工具的定义列表。

        Returns:
            ToolDefinition 列表，供 LLM 注入工具列表。
        """
        return self._registry.definitions()

    def execute_with_timeout(
        self,
        tool_call: ToolCall,
        context: ToolContext,
        timeout: int,
    ) -> ToolResult:
        """以指定超时执行工具。

        Args:
            tool_call: LLM 发起的工具调用请求。
            context: 执行上下文。
            timeout: 超时秒数。

        Returns:
            工具执行结果。
        """
        ctx = ToolContext(
            agent_id=context.agent_id,
            workspace_path=context.workspace_path,
            project_path=context.project_path,
            config=context.config,
            timeout=timeout,
            env=context.env,
            logger=context.logger,
        )
        return self.execute(tool_call, ctx)
