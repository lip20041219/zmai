"""Prompt type and role definitions."""

from __future__ import annotations

from enum import Enum


class PromptType(Enum):
    """Prompt 类型枚举。

    定义 ZMAI 支持的五种核心 Prompt 类型：
    - SYSTEM: 系统级基础设定
    - PLANNER: 任务规划
    - EXECUTOR: 任务执行
    - VERIFIER: 结果验证
    - REPORT: 报告生成
    """

    SYSTEM = "system"
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    REPORT = "report"

    @property
    def label(self) -> str:
        labels = {
            "system": "System Prompt",
            "planner": "Planner Prompt",
            "executor": "Executor Prompt",
            "verifier": "Verifier Prompt",
            "report": "Report Prompt",
        }
        return labels[self.value]

    @classmethod
    def list(cls) -> list[str]:
        return [t.value for t in cls]


class PromptRole(Enum):
    """Prompt 角色枚举。

    定义每条 Prompt 在对话中的角色身份。
    """

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
