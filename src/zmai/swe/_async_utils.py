"""Async helper — offload sync Backend.invoke() to thread pool, avoid blocking the event loop."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from zmai.gateway.base import Backend, BackendRequest, BackendResponse


async def run_sync(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在默认线程池中执行同步函数，避免阻塞 asyncio 事件循环。

    用法:
        response = await run_sync(backend.invoke, request)
        plan = await run_sync(generate_plan, task, backend, config)
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: call(*args, **kwargs))


async def sync_invoke(backend: Backend, request: BackendRequest) -> BackendResponse:
    """将同步 backend.invoke() 放到线程池执行。

    专门用于 Backend 调用的便捷封装。
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, backend.invoke, request)
