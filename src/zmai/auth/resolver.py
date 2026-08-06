"""CredentialResolver — 统一凭证解析器（唯一凭据入口）。

禁止任何模块直接读取 os.environ / credentials 文件 / config 文件。

优先级（低 → 高）:
  1. credential_store  — ~/.zmai/credentials（加密文件）
  2. config_file       — zmai.json / ~/.zmai/config.json
  3. environment       — DEEPSEEK_API_KEY 等环境变量
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from zmai.auth.status import CredentialStatus, ConflictDetail, mask_key, source_label
from zmai.auth.store import AuthStore
from zmai.errors import CredentialError

logger = logging.getLogger("zmai.auth.resolver")

# ── 环境变量名称解析 ───────────────────────────────────────


def _resolve_env_names(name: str) -> tuple[str, str]:
    """获取 Backend 对应的环境变量名。"""
    from zmai.gateway.backends import get_backend_info
    info = get_backend_info(name)
    if info:
        return (
            info.get("env_api_key", f"{name.upper()}_API_KEY"),
            info.get("env_model", f"{name.upper()}_MODEL"),
        )
    try:
        from zmai.gateway.plugin import PluginRegistry
        reg = PluginRegistry()
        plugin = reg.get_plugin(name)
        if plugin:
            return plugin.env_api_key, plugin.env_model
    except Exception:
        pass
    return f"{name.upper()}_API_KEY", f"{name.upper()}_MODEL"


def _resolve_env_prefix(name: str) -> str:
    return name.upper()


# ── Key 格式验证 ────────────────────────────────────────────


def _validate_key_format(key: str, provider: str) -> bool:
    """验证 API Key 格式是否合规。"""
    if not key:
        return False
    key = key.strip()
    p = provider.lower()
    if p == "deepseek":
        return key.startswith("sk-") and len(key) >= 20
    elif p == "claude":
        return key.startswith("sk-ant-") and len(key) >= 20
    elif p == "gemini":
        return len(key) >= 8
    else:
        return len(key) >= 8


# ── 解析器 ─────────────────────────────────────────────────


class CredentialResolver:
    """统一凭据解析器。

    所有凭据读取必须通过 get_status() 方法。
    内部自动加载所有标准配置来源。

    优先级（低 → 高）:
      1. credential_store  — ~/.zmai/credentials（加密文件）
      2. config_file       — zmai.json / ~/.zmai/config.json
      3. environment       — DEEPSEEK_API_KEY 等环境变量

    原则:
      - 永不修改环境变量
      - 永不抛出异常（错误在 status.error 中）
      - 所有消费者得到相同结果
    """

    def __init__(self) -> None:
        self._config: Any = None  # 懒加载

    # ── 统一状态查询 ─────────────────────────────────────

    def get_status(self, name: str) -> CredentialStatus:
        """解析指定 Backend 的完整认证状态。

        所有消费者使用此方法，获得完全一致的结构化结果。
        永不抛出异常 — 错误信息在 status.error / status.error_message 中。

        Args:
            name: Backend 名称（如 "deepseek", "claude"）。

        Returns:
            CredentialStatus 结构化结果。
        """
        status = CredentialStatus(provider=name)

        try:
            self._check_credential_store(status)
            self._check_config_file(status)
            self._check_env_var(status)
            self._finalize(status)
            self._detect_conflict(status)
        except Exception as e:
            status.error = "RESOLVER_ERROR"
            status.error_message = str(e)

        return status

    # ── 内部检查步骤 ─────────────────────────────────────

    def _check_credential_store(self, status: CredentialStatus) -> None:
        """检查 ~/.zmai/credentials 加密文件。"""
        try:
            store = AuthStore()
            data = store.read(status.provider)
            if data:
                raw_key = data.get("api_key", "")
                if raw_key:
                    status.credential_store_status = "ok"
                    status.key_present = True
                    status.api_key = raw_key
                    status.source = "credential_store"
                    for k in ("model", "base_url", "timeout",
                              "max_tokens", "temperature"):
                        v = data.get(k)
                        if v is not None and v != "" and v != 0 and v != 0.0:
                            setattr(status, k, v)
                else:
                    status.credential_store_status = "not_configured"
            else:
                status.credential_store_status = "not_configured"

        except CredentialError as e:
            reason_map = {
                "FILE_EMPTY": "empty",
                "FILE_CORRUPTED": "corrupted",
                "KEY_MISMATCH": "key_mismatch",
                "KEY_FILE_CORRUPTED": "corrupted",
            }
            status.credential_store_status = reason_map.get(e.reason, "error")
            status.error = "CREDENTIAL_ERROR"
            status.error_message = str(e)
        except Exception as e:
            status.credential_store_status = "error"
            status.error = "CREDENTIAL_ERROR"
            status.error_message = str(e)

    def _ensure_config_loaded(self) -> None:
        """懒加载 Config。"""
        if self._config is not None:
            return
        from zmai.config import Config
        from zmai.config.sources import CLISource, EnvSource, FileSource
        root = Path.cwd()
        self._config = Config(sources=[
            FileSource(str(root / "zmai.json")),
            FileSource(str(Path.home() / ".zmai" / "config.json")),
            EnvSource(),
            CLISource(),
        ])

    def _check_config_file(self, status: CredentialStatus) -> None:
        """检查 Config 文件中的 api_key。"""
        try:
            self._ensure_config_loaded()
        except Exception:
            status.config_file_status = "error"
            return

        try:
            raw_key = self._config.get(
                f"backends.{status.provider}.api_key", ""
            )
            if raw_key:
                status.config_file_status = "ok"
                status.key_present = True
                status.api_key = raw_key
                status.source = "config_file"
                for k in ("model", "base_url", "timeout",
                          "max_tokens", "temperature"):
                    v = self._config.get(f"backends.{status.provider}.{k}")
                    if v is not None and v != "" and v != 0 and v != 0.0:
                        setattr(status, k, v)
            else:
                status.config_file_status = "no_key"

        except Exception as e:
            status.config_file_status = "error"
            if not status.error:
                status.error = "CONFIG_ERROR"
                status.error_message = str(e)

    def _check_env_var(self, status: CredentialStatus) -> None:
        """检查环境变量。"""
        env_key_name, env_model = _resolve_env_names(status.provider)
        status.env_var_name = env_key_name

        raw = os.environ.get(env_key_name)
        if raw and raw.strip():
            status.env_var_status = "ok"
            status.key_present = True
            status.api_key = raw
            status.source = "environment"
            prefix = _resolve_env_prefix(status.provider)
            status.model = os.environ.get(env_model, status.model)
            status.base_url = os.environ.get(
                f"{prefix}_BASE_URL", status.base_url
            )
            try:
                status.timeout = int(os.environ.get(
                    f"{prefix}_TIMEOUT", str(status.timeout)))
            except (ValueError, TypeError):
                pass
            try:
                status.max_tokens = int(os.environ.get(
                    f"{prefix}_MAX_TOKENS", str(status.max_tokens)))
            except (ValueError, TypeError):
                pass
            try:
                status.temperature = float(os.environ.get(
                    f"{prefix}_TEMPERATURE", str(status.temperature)))
            except (ValueError, TypeError):
                pass
        elif raw is not None:
            status.env_var_status = "empty"
        else:
            status.env_var_status = "not_found"

    # ── 最终化 ──────────────────────────────────────────

    def _finalize(self, status: CredentialStatus) -> None:
        """设置最终状态标记。"""
        status.configured = bool(status.api_key)
        if not status.configured:
            status.source = "missing"

        status.key_mask = mask_key(status.api_key)
        status.key_valid_format = _validate_key_format(
            status.api_key, status.provider
        )

    # ── 冲突检测 ────────────────────────────────────────

    def _detect_conflict(self, status: CredentialStatus) -> None:
        """检测多来源 Key 冲突。"""
        source_keys: list[tuple[str, str]] = []
        if status.credential_store_status == "ok":
            try:
                store = AuthStore()
                data = store.read(status.provider)
                if data and data.get("api_key"):
                    source_keys.append(("credential_store", data["api_key"]))
            except Exception:
                pass

        if status.config_file_status == "ok":
            try:
                self._ensure_config_loaded()
                k = self._config.get(f"backends.{status.provider}.api_key", "")
                if k:
                    source_keys.append(("config_file", k))
            except Exception:
                pass

        if status.env_var_status == "ok":
            k = os.environ.get(status.env_var_name, "")
            if k:
                source_keys.append(("environment", k))

        if len(source_keys) < 2:
            return

        unique: dict[str, str] = {}
        for source, key in source_keys:
            h = hashlib.sha256(key.encode()).hexdigest()[:8]
            if h not in unique:
                unique[h] = source

        if len(unique) <= 1:
            return

        status.conflict = True
        seen_sources: set[str] = set()
        for h, src in sorted(unique.items(), key=lambda x: x[1]):
            status.conflict_details.append(ConflictDetail(
                source=src,
                label=source_label(src),
                key_hash=h,
            ))
            seen_sources.add(src)

        for source, key in source_keys:
            if source not in seen_sources:
                h = hashlib.sha256(key.encode()).hexdigest()[:8]
                if h in unique:
                    status.conflict_details.append(ConflictDetail(
                        source=source,
                        label=source_label(source),
                        key_hash=h,
                    ))
                    seen_sources.add(source)

    # ── 环境注入（默认不注入，需显式 opt-in） ───────────

    # 安全说明：
    #   inject_to_env() 将 API Key 写入进程环境变量。
    #   子进程可通过 /proc/self/environ（Unix）或 os.environ（Python）读取。
    #   此方法默认不注入，仅用于向后兼容旧代码。
    #   新代码应通过 CredentialResolver.get_status().api_key 直接获取。

    def inject_to_env(self, name: str | None = None) -> None:
        """将凭据注入进程环境变量（需显式调用，默认不注入）。

        安全警告：
          环境变量中的 API Key 会被子进程继承。
          在 Unix 上可通过 /proc/self/environ 读取。
          避免使用此方法，优先通过 get_status() 直接获取。

        行为：
          - 不覆盖已存在的环境变量
          - 不修改 Shell 配置文件
          - 仅写入当前进程的环境变量
        """
        if name is not None:
            self._inject_one(name)
            return

        from zmai.gateway.backends import get_available_backends
        for _name in get_available_backends():
            if not os.environ.get(_resolve_env_names(_name)[0]):
                self._inject_one(_name)

        try:
            from zmai.gateway.plugin import discover_plugins
            for plugin in discover_plugins():
                if not os.environ.get(plugin.env_api_key):
                    self._inject_one(plugin.name)
        except Exception:
            pass

    def _inject_one(self, name: str) -> None:
        """注入单个 Backend 的凭据到环境变量（仅当未设时）。"""
        env_api_key, env_model = _resolve_env_names(name)
        status = self.get_status(name)

        if status.env_var_status == "ok":
            if status.conflict:
                other_sources = [
                    d.label for d in status.conflict_details
                    if d.source != "environment"
                ]
                logger.warning(
                    "%s: environment variable %s is set, "
                    "but %s has a different key. "
                    "Using environment value. "
                    "To use the credential store key, unset %s.",
                    name, env_api_key,
                    ", ".join(other_sources), env_api_key,
                )
            return

        if status.api_key:
            logger.debug(
                "注入 %s 到环境变量 %s（安全警告：环境变量可被子进程读取）",
                name, env_api_key,
            )
            os.environ[env_api_key] = status.api_key
            if status.model and not os.environ.get(env_model):
                os.environ[env_model] = status.model
