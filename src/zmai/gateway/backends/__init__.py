"""ZMAI Gateway Backends — LLM Backend implementations."""

from __future__ import annotations

import os
from typing import Any

from zmai.gateway.backends.claude import ClaudeBackend
from zmai.gateway.backends.deepseek import DeepSeekBackend
from zmai.gateway.backends.gemini import GeminiBackend

__all__ = ["ClaudeBackend", "DeepSeekBackend", "GeminiBackend"]

# ── 单一数据源：所有 Backend 的元信息 ──────────────────────────
#
# 新增官方 Backend 的步骤：
#   1. 在 gateway/backends/ 下创建实现文件
#   2. 在文件末尾添加 plugin = BackendPlugin(...) 描述符
#   3. 在此 BACKEND_METADATA 中追加一项（用于运行时发现）
#   4. 在此文件顶部 import （用于 IDE 类型发现）
#
# 第三方 Backend 使用 Plugin API：
#   创建 .py 文件 → 导出 plugin 变量 → 放入 ~/.zmai/backends/
#
# runtime、CLI、AuthStore 全部从此字典读取，不再硬编码。

BACKEND_METADATA: dict[str, dict] = {
    "claude": {
        "label": "Claude (Anthropic)",
        "default_model": "claude-sonnet-4-6",
        "env_api_key": "ANTHROPIC_API_KEY",
        "env_model": "ANTHROPIC_MODEL",
        "module": "zmai.gateway.backends.claude",
        "class": "ClaudeBackend",
        "verify_url": "https://api.anthropic.com/v1/messages",
        "verify_method": "POST",
        "verify_headers": {"anthropic-version": "2023-06-01"},
    },
    "deepseek": {
        "label": "DeepSeek",
        "default_model": "deepseek-chat",
        "env_api_key": "DEEPSEEK_API_KEY",
        "env_model": "DEEPSEEK_MODEL",
        "module": "zmai.gateway.backends.deepseek",
        "class": "DeepSeekBackend",
        "verify_url": "https://api.deepseek.com/v1/models",
        "verify_method": "GET",
    },
    "gemini": {
        "label": "Gemini (Google)",
        "default_model": "gemini-2.0-flash",
        "env_api_key": "GEMINI_API_KEY",
        "env_model": "GEMINI_MODEL",
        "module": "zmai.gateway.backends.gemini",
        "class": "GeminiBackend",
        "verify_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "verify_method": "GET",
    },
}

# ── 统一 Backend 配置默认值 ──────────────────────────────────
#
# 所有 Backend 子类接受相同的 7 个配置字段：
#   model, api_key, base_url, timeout, max_tokens, temperature
#
# Runtime 按以下优先级合并：
#   hardcoded default < global config < project config < env var < CLI

BACKEND_DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "claude": {
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com/v1",
        "timeout": 300,
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "deepseek": {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "timeout": 120,
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "gemini": {
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "timeout": 120,
        "max_tokens": 4096,
        "temperature": 0.7,
    },
}


def resolve_backend_config(
    name: str,
    global_cfg: dict[str, Any] | None = None,
    project_cfg: dict[str, Any] | None = None,
    env_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """合并 Backend 配置，按优先级: default < global < project < env。

    Args:
        name: Backend 名称。
        global_cfg: 全局配置中 backends.<name> 子字典。
        project_cfg: 项目配置中 backends.<name> 子字典。
        env_cfg: 环境变量中 backends.<name> 子字典。

    Returns:
        包含全部 7 个字段的配置字典。
    """
    cfg = dict(BACKEND_DEFAULT_CONFIG.get(name, {}))
    if global_cfg:
        cfg.update(global_cfg)
    if project_cfg:
        cfg.update(project_cfg)
    if env_cfg:
        cfg.update(env_cfg)
    cfg["backend"] = name
    return cfg


def get_backend_info(name: str) -> dict | None:
    """获取指定 Backend 的元信息。"""
    return BACKEND_METADATA.get(name)


def get_available_backends() -> dict[str, dict]:
    """获取所有已注册的 Backend 元信息。"""
    return dict(BACKEND_METADATA)
