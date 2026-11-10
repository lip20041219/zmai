"""Tool registry — thread-safe tool registration and discovery."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from zmai.errors import ToolError
from zmai.tool.base import Tool, ToolContext, ToolDefinition, ToolResult

logger = logging.getLogger("zmai.tool.registry")


def _log_tool_error(
    tool_name: str,
    error_type: str,
    available_tools: list[str],
    context: ToolContext | None = None,
) -> None:
    """将工具调用错误追加写入 logs/tool_errors.json，便于 benchmark 分析。

    Args:
        tool_name: 被调用的工具名称。
        error_type: 错误类型，如 tool_not_found。
        available_tools: 当前已注册工具名称列表。
        context: 可选执行上下文，用于定位日志路径。
    """
    import time

    entry = {
        "timestamp": time.time(),
        "tool_name": tool_name,
        "error_type": error_type,
        "available_tools": available_tools,
    }
    try:
        path = "logs/tool_errors.json"
        if context is not None:
            base = context.project_path or context.workspace_path
            if base is not None:
                path = str(Path(base) / "logs" / "tool_errors.json")
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    data.append(entry)
                else:
                    data = [data, entry]
            except Exception:
                data = [entry]
        else:
            data = [entry]
        p.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:  # 日志失败不应影响工具执行
        logger.warning("无法写入 tool_errors 日志: %s", e)


class ToolRegistry:
    """工具注册表。线程安全。

    管理所有可用工具的注册、查找、执行。

    使用方式:
        registry = ToolRegistry()
        registry.register(MyTool())
        tool = registry.get("my_tool")
        result = registry.execute("my_tool", params, context)
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._lock = threading.Lock()

    def register(self, tool: Tool) -> None:
        """注册工具。

        同名工具会被覆盖（记录 warning）。

        Args:
            tool: Tool 实例。
        """
        with self._lock:
            if tool.name in self._tools:
                logger.warning("工具已存在，将被覆盖: %s", tool.name)
            self._tools[tool.name] = tool
            logger.debug("工具已注册: %s", tool.name)

    def unregister(self, name: str) -> None:
        """注销工具。

        Args:
            name: 工具名称。

        Raises:
            ToolError: 工具不存在时抛出。
        """
        with self._lock:
            if name not in self._tools:
                raise ToolError(f"工具未注册: {name}", tool_name=name)
            del self._tools[name]
            logger.debug("工具已注销: %s", name)

    def has(self, name: str) -> bool:
        """判断工具是否已注册（不抛异常）。"""
        with self._lock:
            return name in self._tools

    def get(self, name: str) -> Tool:
        """获取已注册的工具实例。

        Args:
            name: 工具名称。

        Returns:
            Tool 实例。

        Raises:
            ToolError: 工具不存在时抛出。
        """
        with self._lock:
            tool = self._tools.get(name)
            if tool is None:
                raise ToolError(f"工具未注册: {name}", tool_name=name)
            return tool

    def list(self) -> list[Tool]:
        """列出所有已注册的工具。"""
        with self._lock:
            return list(self._tools.values())

    def definitions(self) -> list[ToolDefinition]:
        """生成所有工具的定义（供 LLM 注入工具列表）。"""
        with self._lock:
            return [t.to_definition() for t in self._tools.values()]

    def execute_tool(
        self, name: str, params: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        """执行工具；工具不存在时返回结构化错误而非抛异常。

        供 Agent dispatch 使用。与 :meth:`execute` 相比，不存在的工具
        不会抛 ToolError，而是返回带 error_type="tool_not_found" 的结果，
        让 Agent 能据此重新规划正确调用。

        Args:
            name: 工具名称。
            params: 参数字典。
            context: 执行上下文。

        Returns:
            工具执行结果。工具不存在时 success=False。
        """
        if not self.has(name):
            with self._lock:
                available = sorted(self._tools.keys())
            _log_tool_error(name, "tool_not_found", available, context)
            return ToolResult.err(
                f"工具未注册: {name}。可用工具: {', '.join(available) or '(无)'}。"
                f"请重新选择一个可用工具。",
                metadata={
                    "error_type": "tool_not_found",
                    "tool_name": name,
                    "available_tools": available,
                },
            )
        return self.execute(name, params, context)

    def execute(self, name: str, params: dict[str, Any], context: ToolContext) -> ToolResult:
        """执行已注册的工具。先校验参数，再执行。

        Args:
            name: 工具名称。
            params: 参数字典。
            context: 执行上下文。

        Returns:
            工具执行结果。

        Raises:
            ToolError: 工具不存在时抛出。
            需要容错调用请使用 :meth:`execute_tool`。
        """
        tool = self.get(name)
        if not tool.validate(params):
            missing = [r for r in tool.parameters.get("required", []) if r not in params]
            if missing:
                return ToolResult.err(
                    f"{name}: 缺少必需参数: {', '.join(missing)}"
                )
            return ToolResult.err(f"{name}: 参数校验失败")

        # 外层超时取"调用方预算"与"工具自报预算"的较大者：工具的专用预算（如 shell
        # 的测试命令）不得被默认 30s 提前截断。不覆盖 `timeout_for` 的工具返回 None，
        # 行为与改动前完全一致；外层保护本身保留。
        tool_timeout = tool.timeout_for(context, params) or 0
        cleanup_margin = (2 if tool.name == "shell_exec"
                          and context.config.get("_wall_deadline_monotonic") is not None else 0)
        timeout = max(context.timeout or 0, tool_timeout + cleanup_margin)
        if timeout > 0:
            return self._execute_with_timeout(tool, context, params, timeout)
        return tool.execute(context, params)

    @staticmethod
    def _execute_with_timeout(
        tool: Tool, context: ToolContext, params: dict[str, Any], timeout: int
    ) -> ToolResult:
        """在守护线程中执行工具，超时后放弃等待。

        用 daemon=True 的 threading.Thread 替代 ThreadPoolExecutor：
        ThreadPoolExecutor 的工作线程默认非守护，shutdown(wait=False)
        后线程仍存活，Python 退出时会 join 所有非守护线程 → 进程挂起、
        execution_log 无法持久化（A/B 实验中 A2/B2 均因此卡住）。
        守护线程在主线程退出时自动终止，不会阻塞进程退出。

        线程无法强杀，卡住的工具线程会随进程结束被回收；要真正回收
        需要子进程隔离，此处先解决进程退出阻塞问题。
        """
        import queue
        import threading
        import time

        result_queue: queue.Queue[Any] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                result_queue.put(tool.execute(context, params))
            except Exception as exc:
                result_queue.put(exc)

        t = threading.Thread(target=worker, daemon=True, name=f"tool-{tool.name}")
        t.start()
        start = time.monotonic()
        try:
            result = result_queue.get(timeout=timeout)
        except queue.Empty:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("工具执行超时: %s (%ds)", tool.name, timeout)
            return ToolResult(
                success=False, output="",
                error=f"{tool.name}: 执行超时 ({timeout}s)",
                duration_ms=elapsed,
            )
        if isinstance(result, Exception):
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("工具执行失败: %s: %s", tool.name, result)
            return ToolResult(
                success=False, output="", error=str(result), duration_ms=elapsed,
            )
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result
