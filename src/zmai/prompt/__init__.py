"""ZMAI Prompt Engine — 模板化 Prompt 管理。

支持 System / Planner / Executor / Verifier / Report 五种 Prompt 类型，
模板化 + 变量注入，内置默认模板。
"""

from zmai.prompt.base import PromptTemplate, TemplateEngine
from zmai.prompt.engine import PromptEngine
from zmai.prompt.types import PromptRole, PromptType

__all__ = [
    "PromptEngine",
    "PromptRole",
    "PromptTemplate",
    "PromptType",
    "TemplateEngine",
]
