"""CredentialStatus — 统一的认证状态数据结构。

永不抛出异常 — 错误信息在 error / error_message 字段中。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Key 掩码 ────────────────────────────────────────────────


def mask_key(key: str) -> str:
    """对 API Key 做掩码处理，仅用于显示。绝不输出完整 Key。"""
    if not key:
        return "-"
    if len(key) < 7:
        return "****"
    return key[:7] + "****"


# ── 数据结构 ────────────────────────────────────────────────


@dataclass
class ConflictDetail:
    """单个来源的冲突详情。"""

    source: str          # "credential_store" | "config_file" | "environment"
    label: str           # 人类可读标签
    key_hash: str        # SHA256 前 8 位（用于比较，不暴露 Key）


@dataclass
class CredentialStatus:
    """Backend 凭据统一状态报告。

    所有消费者使用同一份结构化结果。
    get_status() 永不抛出异常 — 错误信息在 error / error_message 中。
    """

    # ── 身份 ──────────────────────────────────────────
    provider: str = ""

    # ── 凭据概要 ──────────────────────────────────────
    configured: bool = False       # 是否存在有效 Key
    source: str = "missing"        # 最终生效来源
    # "credential_store" | "config_file" | "environment" | "cli" | "missing"

    # ── Key 详情 ──────────────────────────────────────
    key_present: bool = False      # Key 不为空
    key_mask: str = ""             # 前 7 位 + "****"（显示用，绝不输出完整 Key）
    key_valid_format: bool = False # Key 格式合规

    # ── 各来源原始状态 ─────────────────────────────────
    credential_store_status: str = "not_found"
    # "ok" | "not_found" | "not_configured"
    # | "empty" | "corrupted" | "key_mismatch" | "format_error"
    config_file_status: str = "not_found"
    # "ok" | "not_found" | "no_key" | "error"
    env_var_status: str = "not_found"
    # "ok" | "not_found" | "empty"
    env_var_name: str = ""         # 环境变量名（显示用）

    # ── 冲突检测 ──────────────────────────────────────
    conflict: bool = False
    conflict_details: list[ConflictDetail] = field(default_factory=list)

    # ── 最终值（内部使用，传给 Backend config） ────────
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout: int = 0
    max_tokens: int = 0
    temperature: float = 0.0

    # ── 错误信息 ──────────────────────────────────────
    error: str = ""                # 错误码，空=无错误
    error_message: str = ""        # 用户可读的错误消息

    # ── 验证状态 ──────────────────────────────────────
    verification: str = "unknown"
    # "unknown" | "valid" | "invalid" | "network_error" | "rate_limited"

    # ── 向后兼容别名 ─────────────────────────────────
    @property
    def key_preview(self) -> str:
        """已弃用：使用 key_mask。"""
        return self.key_mask

    @property
    def credentials_file_status(self) -> str:
        """已弃用：使用 credential_store_status。"""
        return self.credential_store_status

    @property
    def error_details(self) -> str:
        """已弃用：使用 error_message。"""
        return self.error_message

    def to_dict(self) -> dict[str, Any]:
        """转换为 dict（不含完整 api_key）。"""
        return {
            "provider": self.provider,
            "configured": self.configured,
            "source": self.source,
            "key_present": self.key_present,
            "key_mask": self.key_mask,
            "key_valid_format": self.key_valid_format,
            "credential_store_status": self.credential_store_status,
            "config_file_status": self.config_file_status,
            "env_var_status": self.env_var_status,
            "env_var_name": self.env_var_name,
            "conflict": self.conflict,
            "conflict_details": [
                {"source": d.source, "label": d.label, "key_hash": d.key_hash}
                for d in self.conflict_details
            ],
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "error": self.error,
            "error_message": self.error_message,
            "verification": self.verification,
        }


# ── 来源标签 ────────────────────────────────────────────────

SOURCE_LABELS: dict[str, str] = {
    "credential_store": "Credential Store",
    "credentials_file": "Credential Store",
    "config_file": "Config File",
    "environment": "Environment Variable",
    "cli": "CLI Argument",
    "missing": "None",
}

STORE_STATUS_LABELS: dict[str, str] = {
    "ok": "Loaded",
    "not_found": "Not found",
    "not_configured": "Not configured",
    "empty": "Empty file",
    "corrupted": "Corrupted",
    "key_mismatch": "Key mismatch",
    "format_error": "Format error",
    "error": "Error",
}


def source_label(source: str) -> str:
    """获取来源的人类可读标签。"""
    return SOURCE_LABELS.get(source, source)


def store_status_label(status: str) -> str:
    """获取凭据存储状态的人类可读标签。"""
    return STORE_STATUS_LABELS.get(status, status)


# ── 旧名称兼容（已弃用） ────────────────────────────────────

# 保持从旧位置导入不中断
FILE_STATUS_LABELS = STORE_STATUS_LABELS
file_status_label = store_status_label
