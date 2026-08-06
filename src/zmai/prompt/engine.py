"""PromptEngine — Prompt 引擎主类。

管理所有 Prompt 类型的模板注册、变量注入、编译输出。
"""

from __future__ import annotations

import logging
from typing import Any

from zmai.prompt.base import PromptTemplate, TemplateEngine
from zmai.prompt.templates import DEFAULT_TEMPLATES
from zmai.prompt.types import PromptRole, PromptType

logger = logging.getLogger("zmai.prompt.engine")

_VAR_DOCS = "agent_name", "description", "workspace_path", "backend_name", "max_steps"


class PromptEngine:
    """Prompt 引擎。

    管理五种核心 Prompt 类型（System / Planner / Executor / Verifier / Report）的
    模板注册、变量注入和编译输出。

    使用方式:
        engine = PromptEngine()
        system_prompt = engine.render(PromptType.SYSTEM, {
            "agent_name": "SWE Agent",
            "task": "Fix bug in parser",
            "workspace_path": "/workspace/agent_1",
        })
    """

    def __init__(
        self,
        *,
        templates: dict[str, str] | None = None,
        engine: TemplateEngine | None = None,
    ) -> None:
        self._engine = engine or TemplateEngine()
        # 合并默认模板和自定义模板
        self._templates: dict[str, PromptTemplate] = {}
        merged = {**DEFAULT_TEMPLATES, **(templates or {})}
        for pt in PromptType:
            tmpl_str = merged.get(pt.value, "")
            self._templates[pt.value] = PromptTemplate(
                prompt_type=pt.value,
                role=PromptRole.SYSTEM.value,
                template=tmpl_str,
                engine=self._engine,
            )
        logger.info("PromptEngine 初始化: %d 个模板已加载", len(self._templates))

    # ── 模板管理 ──────────────────────────────────────────

    def set_template(
        self,
        prompt_type: PromptType | str,
        template: str,
        role: str | None = None,
    ) -> None:
        """设置/覆盖指定类型的模板。

        Args:
            prompt_type: Prompt 类型。
            template: 模板字符串。
            role: 角色（默认 system）。
        """
        key = prompt_type.value if isinstance(prompt_type, PromptType) else prompt_type
        current = self._templates.get(key)
        self._templates[key] = PromptTemplate(
            prompt_type=key,
            role=role or (current.role if current else PromptRole.SYSTEM.value),
            template=template,
            engine=self._engine,
        )
        logger.info("模板已更新: %s", key)

    def get_template(self, prompt_type: PromptType | str) -> PromptTemplate | None:
        """获取指定类型的模板。

        Args:
            prompt_type: Prompt 类型。

        Returns:
            PromptTemplate 或 None。
        """
        key = prompt_type.value if isinstance(prompt_type, PromptType) else prompt_type
        return self._templates.get(key)

    def list_templates(self) -> dict[str, str]:
        """列出所有已注册的模板概要。

        Returns:
            {类型名: 模板前 80 字符} 字典。
        """
        return {
            k: v.template[:80] + ("..." if len(v.template) > 80 else "")
            for k, v in self._templates.items()
        }

    def reset_template(self, prompt_type: PromptType | str) -> None:
        """恢复指定类型的默认模板。

        Args:
            prompt_type: Prompt 类型。
        """
        key = prompt_type.value if isinstance(prompt_type, PromptType) else prompt_type
        if key in DEFAULT_TEMPLATES:
            self._templates[key] = PromptTemplate(
                prompt_type=key,
                role=PromptRole.SYSTEM.value,
                template=DEFAULT_TEMPLATES[key],
                engine=self._engine,
            )
            logger.info("模板已重置为默认: %s", key)
        else:
            logger.warning("无默认模板可重置: %s", key)

    def reset_all(self) -> None:
        """恢复所有模板为默认值。"""
        for key, tmpl_str in DEFAULT_TEMPLATES.items():
            self._templates[key] = PromptTemplate(
                prompt_type=key,
                role=PromptRole.SYSTEM.value,
                template=tmpl_str,
                engine=self._engine,
            )
        logger.info("所有模板已重置为默认值")

    # ── Prompt 编译 ───────────────────────────────────────

    def render(
        self,
        prompt_type: PromptType | str,
        variables: dict[str, Any] | None = None,
    ) -> str:
        """渲染指定类型的 Prompt。

        Args:
            prompt_type: Prompt 类型。
            variables: 变量字典。

        Returns:
            渲染后的 Prompt 字符串。

        Raises:
            ValueError: 模板类型不存在时抛出。
        """
        key = prompt_type.value if isinstance(prompt_type, PromptType) else prompt_type
        tmpl = self._templates.get(key)
        if tmpl is None:
            raise ValueError(f"未知的 Prompt 类型: {key}")
        return tmpl.render(variables)

    def render_system(
        self,
        agent_name: str = "ZMAI Agent",
        description: str = "",
        workspace_path: str = "",
        backend_name: str = "",
        max_steps: int = 100,
        **extra: Any,
    ) -> str:
        """渲染 System Prompt 的便捷方法。

        Args:
            agent_name: Agent 名称。
            description: Agent 描述。
            workspace_path: 工作区路径。
            backend_name: Backend 名称。
            max_steps: 最大步数。
            **extra: 额外变量。

        Returns:
            渲染后的 System Prompt。
        """
        return self.render(PromptType.SYSTEM, {
            "agent_name": agent_name,
            "description": description,
            "workspace_path": workspace_path,
            "backend_name": backend_name,
            "max_steps": str(max_steps),
            **extra,
        })

    def render_planner(
        self,
        task: str = "",
        context: str = "",
        max_steps: int = 10,
        additional_guidelines: str = "",
        **extra: Any,
    ) -> str:
        """渲染 Planner Prompt 的便捷方法。"""
        return self.render(PromptType.PLANNER, {
            "task": task,
            "context": context,
            "max_steps": str(max_steps),
            "additional_guidelines": additional_guidelines,
            **extra,
        })

    def render_executor(
        self,
        plan: str = "",
        step_number: int = 1,
        step_description: str = "",
        completed_steps: int = 0,
        total_steps: int = 1,
        tool_descriptions: str = "",
        **extra: Any,
    ) -> str:
        """渲染 Executor Prompt 的便捷方法。"""
        return self.render(PromptType.EXECUTOR, {
            "plan": plan,
            "step_number": str(step_number),
            "step_description": step_description,
            "completed_steps": str(completed_steps),
            "total_steps": str(total_steps),
            "tool_descriptions": tool_descriptions,
            **extra,
        })

    def render_verifier(
        self,
        step_description: str = "",
        execution_result: str = "",
        verification_criteria: str = "",
        **extra: Any,
    ) -> str:
        """渲染 Verifier Prompt 的便捷方法。"""
        return self.render(PromptType.VERIFIER, {
            "step_description": step_description,
            "execution_result": execution_result,
            "verification_criteria": verification_criteria,
            **extra,
        })

    def render_report(
        self,
        task: str = "",
        execution_summary: str = "",
        steps_details: str = "",
        success: bool = True,
        error_info: str = "",
        total_steps: int = 0,
        completed_steps: int = 0,
        failed_steps: int = 0,
        total_tokens: int = 0,
        **extra: Any,
    ) -> str:
        """渲染 Report Prompt 的便捷方法。"""
        return self.render(PromptType.REPORT, {
            "task": task,
            "execution_summary": execution_summary,
            "steps_details": steps_details,
            "success": success,
            "error_info": error_info,
            "total_steps": str(total_steps),
            "completed_steps": str(completed_steps),
            "failed_steps": str(failed_steps),
            "total_tokens": str(total_tokens),
            **extra,
        })

    # ── 编译为消息格式 ────────────────────────────────────

    def to_message(
        self,
        prompt_type: PromptType | str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """编译 Prompt 并包装为消息格式（供 BackendRequest 使用）。

        Args:
            prompt_type: Prompt 类型。
            variables: 变量字典。

        Returns:
            {"role": str, "content": str} 消息字典。
        """
        key = prompt_type.value if isinstance(prompt_type, PromptType) else prompt_type
        content = self.render(key, variables)
        tmpl = self._templates.get(key)
        role = tmpl.role if tmpl else "system"
        return {"role": role, "content": content}

    def to_messages(
        self,
        prompt_types: list[PromptType | str],
        base_variables: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """批量编译多个 Prompt 为消息列表。

        Args:
            prompt_types: Prompt 类型列表。
            base_variables: 共享变量（每个 Prompt 都会注入）。

        Returns:
            消息列表。
        """
        vars = base_variables or {}
        return [self.to_message(pt, dict(vars)) for pt in prompt_types]
