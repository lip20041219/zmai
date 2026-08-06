"""Prompt template engine — template compilation and variable substitution."""

from __future__ import annotations

import logging
import re
import string as string_lib
from typing import Any

logger = logging.getLogger("zmai.prompt")


class TemplateEngine:
    """模板引擎。

    支持两级变量语法：
    - 简单变量: $var_name 或 ${var_name}
    - 条件块: {% if var %}...{% else %}...{% endif %}

    使用 Python string.Template 做基础变量替换，自定义条件块和循环块语法。
    """

    def __init__(self, **kwargs: Any) -> None:
        pass

    def render(self, template: str, variables: dict[str, Any]) -> str:
        """渲染模板，替换变量。

        Args:
            template: 模板字符串。
            variables: 变量字典。

        Returns:
            渲染后的字符串。
        """
        # 处理 {% if var %}...{% else %}...{% endif %}
        result = self._process_conditionals(template, variables)
        # 处理 {% for item in list %}...{% endfor %}
        result = self._process_loops(result, variables)
        # 使用 string.Template 做变量替换
        return self._safe_substitute(result, variables)

    def _process_conditionals(self, template: str, variables: dict[str, Any]) -> str:
        """处理 {% if var %}...{% else %}...{% endif %} 块。"""
        pattern = r"\{%\s*if\s+(\w+)\s*%\}(.*?)(?:\{%\s*else\s*%\}(.*?))?\{%\s*endif\s*%\}"
        return re.sub(
            pattern,
            lambda m: self._eval_conditional(m, variables),
            template,
            flags=re.DOTALL,
        )

    @staticmethod
    def _eval_conditional(match: re.Match, variables: dict[str, Any]) -> str:
        var_name = match.group(1)
        value = variables.get(var_name)
        if value:
            return match.group(2) or ""
        return match.group(3) or ""

    def _process_loops(self, template: str, variables: dict[str, Any]) -> str:
        """处理 {% for item in list %}...{% endfor %} 块。"""
        pattern = r"\{%\s*for\s+(\w+)\s+in\s+(\w+)\s*%\}(.*?)\{%\s*endfor\s*%\}"
        return re.sub(
            pattern,
            lambda m: self._eval_loop(m, variables),
            template,
            flags=re.DOTALL,
        )

    def _eval_loop(self, match: re.Match, variables: dict[str, Any]) -> str:
        item_name = match.group(1)
        list_name = match.group(2)
        body = match.group(3)
        items = variables.get(list_name, [])
        if not isinstance(items, (list, tuple)):
            return ""
        parts: list[str] = []
        for item in items:
            item_vars = {**variables, item_name: item}
            parts.append(self._safe_substitute(body, item_vars))
        return "".join(parts)

    @staticmethod
    def _safe_substitute(template: str, variables: dict[str, Any]) -> str:
        """安全地进行 string.Template 变量替换。

        使用 Python 标准库 string.Template.safe_substitute，
        保留无法替换的占位符原样输出（不抛 KeyError）。
        """
        t = string_lib.Template(template)
        return t.safe_substitute(**variables)


class PromptTemplate:
    """单个 Prompt 模板。

    封装模板字符串、角色、类型，支持变量渲染。

    使用方式:
        tmpl = PromptTemplate(
            prompt_type="system",
            role="system",
            template="You are $role. Task: $task",
        )
        rendered = tmpl.render({"role": "expert", "task": "fix bugs"})
    """

    def __init__(
        self,
        prompt_type: str,
        role: str = "system",
        template: str = "",
        *,
        engine: TemplateEngine | None = None,
    ) -> None:
        self.prompt_type: str = prompt_type
        self.role: str = role
        self.template: str = template
        self._engine = engine or TemplateEngine()

    def render(self, variables: dict[str, Any] | None = None) -> str:
        """渲染模板，替换变量。

        Args:
            variables: 变量字典。

        Returns:
            渲染后的字符串。
        """
        return self._engine.render(self.template, variables or {})

    def to_dict(self) -> dict[str, str]:
        return {
            "type": self.prompt_type,
            "role": self.role,
            "template": self.template,
        }
