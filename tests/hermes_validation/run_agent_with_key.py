"""run_agent.py 测试包装：从 ZMAI Credential Store 读取 DeepSeek key，
配置 claude CLI 的 Anthropic 兼容环境变量后，调用真实 run_agent.py。

背景: claude CLI 未登录 (authMethod: none)，但 ZMAI credential store
已配置 DeepSeek key。通过 ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN
让 claude CLI 走 DeepSeek 的 Anthropic 兼容端点（与项目此前用法一致，
见 process_result.json 中 deepseek-v4-flash 模型记录）。

用法: python run_agent_with_key.py --workspace <ws> --prompt <p> [--skip-permissions]
"""

import os
import sys

from zmai.auth.store import AuthStore


def main() -> None:
    store = AuthStore()
    backend = store.get_backend("deepseek")
    if not backend or not backend.get("api_key"):
        print("ERROR: DeepSeek key not found in Credential Store")
        sys.exit(2)

    os.environ["ANTHROPIC_BASE_URL"] = backend.get("base_url", "https://api.deepseek.com/v1")
    os.environ["ANTHROPIC_AUTH_TOKEN"] = backend["api_key"]
    # 与项目此前 claude CLI 用法保持一致（process_result.json 记录为 deepseek-v4-flash）
    os.environ["ANTHROPIC_MODEL"] = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")
    os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")
    os.environ["ANTHROPIC_DEFAULT_SONNET_MODEL"] = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")
    os.environ["ANTHROPIC_SMALL_FAST_MODEL"] = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")
    os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

    # 调用真实 run_agent.py（透传所有 CLI 参数）
    import run_agent

    run_agent.main()


if __name__ == "__main__":
    main()
