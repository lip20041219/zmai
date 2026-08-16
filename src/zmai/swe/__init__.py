"""SWE Agent — software engineering agent toolkit and implementation."""

from zmai.swe.agent import SWEAgent
from zmai.swe.context import ContextManager
from zmai.swe.loop_guard import LOOP_THRESHOLD, LoopGuard, LoopResult, create_loop_guard
from zmai.swe.models import MAX_REPLANS, Plan, PlanStep, format_plan_summary, validate_plan_dict
from zmai.swe.plan_agent import PlanAgent
from zmai.swe.plan_guard import PlanModeGuard
from zmai.swe.planner import generate_plan, parse_plan_response
from zmai.swe.scanner import RepositoryInfo, RepositoryScanner, scan_repository

__all__ = [
    "SWEAgent",
    "Plan",
    "PlanStep",
    "MAX_REPLANS",
    "validate_plan_dict",
    "format_plan_summary",
    "generate_plan",
    "parse_plan_response",
    "ContextManager",
    "PlanAgent",
    "PlanModeGuard",
    "LoopGuard",
    "LoopResult",
    "create_loop_guard",
    "LOOP_THRESHOLD",
    "RepositoryInfo",
    "RepositoryScanner",
    "scan_repository",
]
