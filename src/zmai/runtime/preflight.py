"""Preflight Check — 任务启动前的系统健康检查。

在 Runtime 调用 Backend API 之前，先检查：
  1. Backend 是否已选择
  2. Backend 是否已注册
  3. API Key 是否存在
  4. API Key 是否为空
  5. Config 是否有效

所有检查通过 → 启动 Agent。
任一检查失败 → 输出友好错误，不发送 HTTP 请求。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from zmai.config import Config

logger = logging.getLogger("zmai.preflight")


class PreflightResult:
    """Preflight 检查结果。

    Attributes:
        passed: True 表示所有检查通过。
        message: 失败时的友好提示信息。
        reason: 失败原因代码。
        backend_name: 当前选择的 Backend 名称。
    """

    def __init__(
        self,
        passed: bool,
        message: str = "",
        reason: str = "",
        backend_name: str = "",
    ) -> None:
        self.passed = passed
        self.message = message
        self.reason = reason
        self.backend_name = backend_name

    def print(self) -> None:
        """输出友好的检查结果到 stderr。"""
        if self.passed:
            return
        sep = "━" * 50
        print(f"\n  {sep}", file=sys.stderr)
        print(f"  Preflight Check 失败", file=sys.stderr)
        print(f"", file=sys.stderr)
        for line in self.message.split("\n"):
            print(f"  {line}", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"  {sep}\n", file=sys.stderr)


# ── 主检查函数 ──────────────────────────────────────────


def check(
    backend_name: str | None,
    gateway: Any,
    config: Config | None = None,
) -> PreflightResult:
    """执行 Preflight 检查。

    Args:
        backend_name: 用户指定的 Backend 名称（可能为 None）。
        gateway: BackendRegistry 或 PluginRegistry 实例。
        config: Config 实例（可选）。

    Returns:
        PreflightResult — passed=True 表示可以继续。
    """
    # ── 1. 解析当前 Backend ──────────────────────────
    resolved = backend_name or getattr(gateway, "default_name", None)
    if not resolved:
        return PreflightResult(
            passed=False,
            message=(
                "未选择 Backend。\n"
                "\n"
                "  请执行：\n"
                "    zmai --backend <name> <task>  # 指定 Backend\n"
                "  或者设置环境变量：\n"
                "    ANTHROPIC_API_KEY  /  DEEPSEEK_API_KEY  /  GEMINI_API_KEY"
            ),
            reason="NO_BACKEND_SELECTED",
            backend_name="",
        )

    # ── 2. Backend 是否已注册 ────────────────────────
    registered = gateway.list() if hasattr(gateway, "list") else []
    if resolved not in registered:
        available = ", ".join(registered) if registered else "无"
        return PreflightResult(
            passed=False,
            message=(
                f"Backend '{resolved}' 未注册。\n"
                f"\n"
                f"  当前已注册的 Backend：{available}\n"
                f"\n"
                f"  请执行：\n"
                f"    zmai auth update {resolved}"
            ),
            reason="BACKEND_NOT_REGISTERED",
            backend_name=resolved,
        )

    # ── 3. 获取 API Key 的环境变量名和 Backend 标签 ──
    env_key_name = _resolve_env_key_name(resolved, gateway)
    label = _resolve_label(resolved, gateway)

    # ── 4. API Key 是否存在且有效 ────────────────────
    api_key = _find_api_key(resolved, env_key_name)
    need_fallback = False

    if not api_key or not api_key.strip():
        # 检查是否有 Fallback 可用
        if _has_fallback(resolved, gateway):
            # Fallback 可用，Preflight 通过，由 get() 处理切换
            need_fallback = True
            logger.debug(
                "Preflight: %s 无有效 API Key，但有 fallback 可用，继续",
                resolved,
            )
        elif not api_key:
            return _missing_key_result(resolved, env_key_name, label, registered)
        else:
            return PreflightResult(
                passed=False,
                message=(
                    f"当前 Backend：{label}\n"
                    f"{env_key_name} 是空字符串。\n"
                    f"\n"
                    f"  请执行：\n"
                    f"    zmai auth update {resolved}"
                ),
                reason="API_KEY_EMPTY",
                backend_name=resolved,
            )

    # 如果有 fallback，跳过后续 Key 检查
    if need_fallback:
        return PreflightResult(passed=True, backend_name=resolved)

    # ── 5. Config 检查（可选） ────────────────────────
    if config is not None:
        cfg_ok, cfg_msg = _check_config(config, resolved)
        if not cfg_ok:
            return PreflightResult(
                passed=False,
                message=cfg_msg,
                reason="CONFIG_ERROR",
                backend_name=resolved,
            )

    # ── 全部通过 ──────────────────────────────────────
    logger.debug("Preflight check passed: backend=%s", resolved)
    return PreflightResult(passed=True, backend_name=resolved)


# ── 辅助函数 ─────────────────────────────────────────────


def _resolve_env_key_name(name: str, gateway: Any) -> str:
    """从 PluginRegistry 或 BACKEND_METADATA 获取 env var 名称。"""
    if hasattr(gateway, "get_plugin"):
        plugin = gateway.get_plugin(name)
        if plugin and plugin.env_api_key:
            return plugin.env_api_key
    try:
        from zmai.gateway.backends import get_backend_info
        info = get_backend_info(name)
        if info and info.get("env_api_key"):
            return info["env_api_key"]
    except Exception:
        pass
    return f"{name.upper()}_API_KEY"


def _resolve_label(name: str, gateway: Any) -> str:
    """获取 Backend 的人类可读名称。"""
    if hasattr(gateway, "get_plugin"):
        plugin = gateway.get_plugin(name)
        if plugin and plugin.label:
            return plugin.label
    try:
        from zmai.gateway.backends import get_backend_info
        info = get_backend_info(name)
        if info and info.get("label"):
            return info["label"]
    except Exception:
        pass
    return name.title()


def _find_api_key(name: str, env_key_name: str = "") -> str:
    """使用 CredentialResolver 统一查找 API Key。

    env_key_name 仅用于向后兼容，实际由 resolver 自动解析。
    """
    from zmai.auth.resolver import CredentialResolver
    status = CredentialResolver().get_status(name)
    return status.api_key


def _missing_key_result(
    name: str,
    env_key_name: str,
    label: str,
    registered: list[str],
) -> PreflightResult:
    """API Key 缺失时构造友好的错误信息。"""
    others = [n for n in registered if n != name]

    lines = [
        f"当前 Backend：{label}",
        f"未检测到 {env_key_name}。",
        "",
        f"  请执行：",
        f"    zmai auth update {name}",
    ]
    if others:
        lines += [
            "",
            f"  或者切换到其他 Backend：",
        ]
        for alt in others:
            lines.append(f"    zmai --backend {alt} <task>")

    return PreflightResult(
        passed=False,
        message="\n".join(lines),
        reason="API_KEY_MISSING",
        backend_name=name,
    )


def _check_config(config: Config, backend_name: str) -> tuple[bool, str]:
    """检查必要配置项。"""
    gw = config.get("gateway")
    if gw is not None and not isinstance(gw, dict):
        return False, "gateway 配置格式错误，应为 JSON 对象。"

    return True, ""


def _has_fallback(failed: str, gateway: Any) -> bool:
    """检查是否有其他 Backend 可用作 Fallback。

    Args:
        failed: 当前失败的 Backend 名称。
        gateway: BackendRegistry 或 PluginRegistry 实例。

    Returns:
        True 表示存在至少一个可用的 Fallback Backend。
    """
    # 优先使用 PluginRegistry 的插件信息
    if not hasattr(gateway, "list_plugins"):
        return False

    for plugin in gateway.list_plugins():
        if plugin.name == failed:
            continue
        if plugin.name in gateway._instances:
            return True
        key = _find_api_key(plugin.name, plugin.env_api_key)
        if key:
            return True

    return False
