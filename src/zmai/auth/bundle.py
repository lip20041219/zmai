"""CredentialBundle — 统一凭据数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CredentialBundle:
    """统一的 Backend 凭据结构体。

    由 CredentialResolver 按优先级（credentials_file < config_file < environment）装配后返回。
    调用方通过本结构体读取最终值，不再自行拼装。

    新字段（2026-07 升级）:
      file_key / config_key / env_key — 各来源原始值，用于冲突检测
      active_source — 最终生效的来源
      has_conflict / conflict_sources — 多来源不同 Key 的冲突标记
    """

    # ── 最终值（按优先级合并后） ──────────────────────
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout: int = 0
    max_tokens: int = 0
    temperature: float = 0.0

    # ── 各来源原始值（用于冲突检测和显示） ─────────────
    file_key: str = ""        # credentials 文件中的原始 Key
    config_key: str = ""      # Config 文件中的原始 Key
    env_key: str = ""         # 环境变量中的原始 Key

    # ── 来源标记 ─────────────────────────────────────
    from_file: bool = False   # file_key 非空
    from_config: bool = False  # config_key 非空
    from_env: bool = False    # env_key 非空

    # ── 最终来源 ─────────────────────────────────────
    active_source: str = "missing"
    # "credentials_file" | "config_file" | "environment" | "cli" | "missing"

    # ── 冲突状态 ─────────────────────────────────────
    has_conflict: bool = False

    @property
    def exists(self) -> bool:
        """是否存在有效的 API Key。"""
        return bool(self.api_key)

    def to_dict(self) -> dict[str, Any]:
        """转换为 dict（不含 metadata 字段）。"""
        return {
            "api_key": self.api_key,
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "active_source": self.active_source,
            "has_conflict": self.has_conflict,
            "from_env": self.from_env,
        }
