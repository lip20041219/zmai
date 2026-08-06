"""create_html.py — 使用 SWE Agent 创建文件。

功能：
  Agent 接收一个文件创建任务，在 Workspace 中生成文件，
  执行完成后读取并展示文件内容。

运行方式：
  # 有 API Key:
  export DEEPSEEK_API_KEY=sk-xxx
  python examples/create_html.py

  # 无 API Key（内置 Mock 演示流程）:
  python examples/create_html.py

需要配置：
  至少一个 Backend 的 API Key。
  无 API Key 时使用 MockBackend 演示（不实际创建文件）。

预期结果：
  终端输出生成的文件路径和内容。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _has_real_backend() -> bool:
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
        """返回固定响应的 Mock Backend。"""

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
            return BackendResponse(
                content="[MockBackend] 模拟文件创建完成。"
                        "配置 API Key 后使用真实 LLM 生成文件。",
            )

        def stream(self, request: BackendRequest) -> BackendResponse:  # type: ignore[override]
            yield BackendEvent(type="text", data="[MockBackend] ")
            yield BackendEvent(type="text", data="Streaming not supported in mock")
            yield BackendEvent(type="done", data=None)


# ── 核心示例 ──────────────────────────────────────────────


async def main() -> None:
    from zmai.config import Config
    from zmai.runtime import Runtime
    from zmai.workspace import Workspace

    # 1. 创建配置和 Runtime
    config = Config()
    runtime = Runtime(config=config)

    agent_id = "demo_create_html"

    # 2. 无真实 Backend 时注册 MockBackend
    if not _has_real_backend():
        os.environ["MOCK_API_KEY"] = "sk-mock"
        runtime._gateway.register("mock", MockBackend, default=True)
        print("  [使用 MockBackend — 无 API Key 检测到]")
        print()

    # 3. 定义进度回调
    def on_progress(typ: str, msg: str) -> None:
        if typ == "tool":
            print(f"  > 工具调用: {msg}")
        elif typ == "result":
            tag = msg[:80]
            print(f"    -> {tag}")

    # 4. 执行任务：让 Agent 创建一个 HTML 文件
    task = (
        "创建一个 HTML 文件 demo.html，"
        "包含一个蓝色标题和一个绿色按钮，"
        "保存到 workspace 中。"
    )
    result = await runtime.run(
        agent_id=agent_id,
        task=task,
        on_progress=on_progress,
    )

    # 5. 读取生成的 HTML 文件
    status = result.get("status", "?")
    steps = result.get("steps", 0)

    print()
    print(f"  状态: {status}")
    print(f"  步数: {steps}")

    if status == "completed" and _has_real_backend():
        # 从 Workspace 读取 Agent 生成的文件
        ws = Workspace()
        files = ws.list(agent_id, "**/*.html")
        if files:
            for fpath in files:
                content = ws.read_text(agent_id, str(fpath))
                print()
                print(f"  生成文件: {fpath}")
                print(f"  内容 ({len(content)} 字符):")
                print(f"  {'='*40}")
                print(content[:500])
                print(f"  {'='*40}")
        else:
            print("  (未找到 HTML 文件)")
    elif _has_real_backend():
        print(f"  输出: {result.get('output', '')[:200]}")
    else:
        print("  [INFO] 使用真实 Backend 可看到实际文件生成效果。")

    # 6. 清理工作区
    ws = Workspace()
    ws.cleanup(agent_id, keep_output=False)


if __name__ == "__main__":
    asyncio.run(main())
