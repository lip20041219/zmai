"""basic_task.py — 最简单的 ZMAI Runtime 调用示例。

功能：
  创建 Runtime 实例，执行一条简单任务，获取结果。

运行方式：
  # 有 API Key（使用真实 Backend）:
  export DEEPSEEK_API_KEY=sk-xxx
  python examples/basic_task.py

  # 无 API Key（使用内置 Mock，演示代码流程）:
  python examples/basic_task.py

需要配置：
  至少一个 Backend 的 API Key（DeepSeek / Claude / Gemini）。
  无 API Key 时自动使用 MockBackend 演示流程。

预期结果：
  终端输出任务执行结果。
"""

import asyncio
import os
import sys

# ── 将项目 src 目录加入导入路径 ──────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _has_real_backend() -> bool:
    """检查是否至少有一个 Backend 配置了 API Key。"""
    for env_var in ["DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"]:
        val = os.environ.get(env_var, "")
        if val and val.strip():
            return True
    return False


# ── MockBackend（无 API Key 时使用） ──────────────────────

if not _has_real_backend():

    from zmai.gateway.base import (
        Backend,
        BackendCapability,
        BackendEvent,
        BackendRequest,
        BackendResponse,
    )

    class MockBackend(Backend):
        """一个返回固定响应的 Mock Backend，用于演示。"""

        name = "mock"

        def __init__(self, config: dict | None = None) -> None:
            self._config = config or {}
            self._model = "mock-model"

        @property
        def model(self) -> str:
            return self._model

        @property
        def capabilities(self) -> set[BackendCapability]:
            return {BackendCapability.SYSTEM_PROMPT}

        def invoke(self, request: BackendRequest) -> BackendResponse:
            task = request.messages[-1]["content"] if request.messages else ""
            return BackendResponse(
                content=f"[MockBackend] 收到任务: {task[:80]}。"
                        f"配置 API Key 后使用真实 Backend。",
            )

        def stream(self, request: BackendRequest) -> BackendResponse:  # type: ignore[override]
            yield BackendEvent(type="text", data="[MockBackend] ")
            yield BackendEvent(type="text", data="Streaming mock response")
            yield BackendEvent(type="done", data=None)


# ── 核心示例 ──────────────────────────────────────────────


async def main() -> None:
    from zmai.config import Config
    from zmai.runtime import Runtime

    # 1. 创建配置
    config = Config()
    runtime = Runtime(config=config)

    # 2. 无真实 Backend 时注册 MockBackend
    if not _has_real_backend():
        os.environ["MOCK_API_KEY"] = "sk-mock"
        runtime._gateway.register("mock", MockBackend, default=True)
        print("  [使用 MockBackend — 无 API Key 检测到]")
        print()

    # 3. 定义进度回调（可选）
    def on_progress(typ: str, msg: str) -> None:
        if typ == "tool":
            print(f"  > 工具调用: {msg}")
        elif typ == "result":
            print(f"    -> {msg}")

    # 4. 执行任务
    result = await runtime.run(
        agent_id="demo_basic",
        task="用一句话回答 1+1=?",
        on_progress=on_progress,
    )

    # 5. 输出结果
    status = result.get("status", "?")
    output = result.get("output", "")
    steps = result.get("steps", 0)

    print()
    print(f"  状态: {status}")
    print(f"  输出: {output}")
    print(f"  步数: {steps}")
    print()

    if _has_real_backend():
        print("  [OK] 使用真实 Backend 执行完成。")
    else:
        print("  [INFO] 使用真实 Backend 可看到实际 LLM 输出。")
        print("        设置环境变量后重试:")
        print("           export DEEPSEEK_API_KEY=sk-xxx")


if __name__ == "__main__":
    asyncio.run(main())
