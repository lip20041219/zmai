"""Task scheduler — manages async agent tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

from zmai.errors import RuntimeError

logger = logging.getLogger("zmai.runtime.scheduler")


class Scheduler:
    """异步任务调度器。"""

    def __init__(self, max_concurrent: int = 10) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._max = max_concurrent

    async def schedule(self, agent_id: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        if len(self._tasks) >= self._max:
            raise RuntimeError(f"并发 Agent 数超出限制: {self._max}")
        task = asyncio.create_task(coro, name=agent_id)
        self._tasks[agent_id] = task
        logger.info("Agent 任务已调度: %s", agent_id)

        # 自动清理已完成的任务
        def _on_done(_t: asyncio.Task[Any]) -> None:
            if agent_id in self._tasks and self._tasks[agent_id] is _t:
                self._tasks.pop(agent_id, None)
                logger.debug("Agent 任务已自动清理: %s", agent_id)

        task.add_done_callback(_on_done)
        return task

    async def cancel(self, agent_id: str) -> None:
        task = self._tasks.get(agent_id)
        if task and not task.done():
            task.cancel()
            logger.info("Agent 任务已取消: %s", agent_id)

    async def wait(self, agent_id: str) -> Any:
        task = self._tasks.get(agent_id)
        if not task:
            raise RuntimeError(f"Agent 任务不存在: {agent_id}")
        return await task

    def is_running(self, agent_id: str) -> bool:
        task = self._tasks.get(agent_id)
        return task is not None and not task.done()

    def running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

        return [aid for aid, t in self._tasks.items() if not t.done()]

    async def shutdown(self) -> None:
        for aid, t in self._tasks.items():
            if not t.done():
                t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
