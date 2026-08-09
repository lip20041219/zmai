"""Fix Planner — 根据语义化的测试失败生成有序的修复计划。

Fix Planner 是修复闭环的"计划"环节：解析失败（failure.py）后，
把语义问题映射为一组有序、可执行的步骤，并注入上下文，让 Agent
不再盲目 read_file，而是按计划定位 → 修改 → 验证。

依赖:
  - failure.py 的 FailureIssue（语义化失败）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from zmai.swe.failure import FailureIssue

# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════


@dataclass
class FixPlan:
    """一份针对单一失败的修复计划。

    Attributes:
        goal: 计划目标（简短）。
        steps: 有序修复步骤（人类可读，供 Agent 执行）。
        issue_type: 失败类型（NotFound / MissingField …）。
        is_empty: 是否无有效步骤（空计划）。
    """

    goal: str
    steps: list[str]
    issue_type: str = ""
    is_empty: bool = False


_EMPTY_PLAN = FixPlan(
    goal="",
    steps=[],
    is_empty=True,
)


# ═══════════════════════════════════════════════════════════════════
# 计划生成
# ═══════════════════════════════════════════════════════════════════


def _default_steps(issue: FailureIssue) -> list[str]:
    """通用修复步骤（任意失败类型都适用）。"""
    src = issue.file or "相关源文件"
    return [
        "1. 重跑 `python -m pytest` 复现失败，读取失败摘要",
        f"2. 读取 {src} 与失败测试，定位根因",
        "3. 用 `edit` 或 `write_file` 做出最小、有针对性的修改",
        "4. 重新运行 `python -m pytest` 验证修复",
        "5. 若仍失败，回到第 2 步继续",
    ]


def _route_steps(issue: FailureIssue) -> list[str]:
    """404 / 路由问题 的修复步骤。"""
    return [
        "1. 读取 app.py，列出所有已注册的 @app.route 路由",
        "2. 找到缺失或路径不匹配的目标端点",
        "3. 为目标函数补上正确的 @app.route('...') 装饰器",
        "4. 运行 `python -m pytest` 验证对应测试通过",
    ]


def _field_steps(issue: FailureIssue) -> list[str]:
    """API 字段缺失 的修复步骤。"""
    return [
        "1. 读取返回 JSON 的接口源码",
        "2. 对比测试期望的字段名与实际返回的字段名",
        "3. 用 `edit` 把字段名改成测试期望的名称",
        "4. 运行 `python -m pytest` 验证",
    ]


# 失败类型 → 专属步骤生成器
_STEP_GENERATORS: dict[str, object] = {
    "NotFound": _route_steps,
    "MissingField": _field_steps,
}


def generate_fix_plan(issue: FailureIssue) -> FixPlan:
    """根据语义化失败生成修复计划。

    Args:
        issue: failure.parse_test_failure() 的产物。

    Returns:
        FixPlan；失败无效时返回空计划。
    """
    if not issue or not issue.semantic:
        return _EMPTY_PLAN

    generator = _STEP_GENERATORS.get(issue.issue_type)
    steps = generator(issue) if generator else _default_steps(issue)

    goal = f"修复 {issue.test_name or '失败测试'}（{issue.semantic[:60]}）"
    return FixPlan(goal=goal, steps=steps, issue_type=issue.issue_type)


def format_plan(plan: FixPlan) -> str:
    """把 FixPlan 格式化成可注入上下文的文本。"""
    if plan.is_empty or not plan.steps:
        return ""
    lines = [f"## Fix Plan: {plan.goal}"]
    for s in plan.steps:
        lines.append(f"- {s}")
    return "\n".join(lines)
