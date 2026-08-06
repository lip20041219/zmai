"""PlanAgent — planning-phase dedicated agent.

Responsibilities:
  1. Analyze project code in read-only mode.
  2. Call Backend to generate a structured Plan.
  3. Return the Plan for user confirmation.
  4. Does NOT perform any write operations.

Repository discovery:
  Uses RepositoryScanner to find source files in the user project root.
  NEVER scans workspace/, .state/, or other internal directories.

PlanAgent is used in Runtime.plan(), separate from SWEAgent.
After confirmation, SWEAgent executes the plan.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from zmai.agent import AgentContext
from zmai.errors import BackendError
from zmai.gateway.base import Backend, BackendRequest, BackendResponse
from zmai.swe._async_utils import run_sync
from zmai.swe.models import Plan, MAX_REPLANS, validate_plan_dict
from zmai.swe.plan_guard import PlanModeGuard
from zmai.swe.scanner import RepositoryInfo, RepositoryScanner
from zmai.tool import ToolRegistry, ToolContext as _ToolContext

logger = logging.getLogger("zmai.swe.plan_agent")

# ── Constants ──────────────────────────────────────────────────────

MAX_PLAN_STEPS = 10
"""Maximum LLM call steps for plan generation. Exceeding this is considered a failure."""


_PLANNER_SYSTEM_PROMPT = """You are a software engineering planner. Your task is to analyze the project and generate an execution plan WITHOUT modifying any files.

## Rules (mandatory)
1. You may ONLY use read-only tools (read_file, grep, show_to_user).
2. You are FORBIDDEN from using write tools: write_file, edit, shell_exec (with dangerous commands), git.
3. Your output MUST be strict JSON (see format below). Do NOT include any other text.
4. Do NOT wrap the JSON in markdown code blocks.

## Output JSON Format
{
  "goal": "One-sentence task objective",
  "assumptions": ["Assumption 1 about the project", "Assumption 2"],
  "files_to_modify": ["path/to/file1", "path/to/file2"],
  "expected_outcome": "Expected result after completion",
  "verification_strategy": "How to verify overall success",
  "steps": [
    {
      "id": 1,
      "action": "Description of the action",
      "tool": "Expected tool name (or null)",
      "params": {"key": "value"},
      "expected_outcome": "Expected result after this step",
      "verification_strategy": "How to verify this step succeeded"
    }
  ],
  "estimated_complexity": "simple | medium | complex",
  "risks": ["Risk 1", "Risk 2"]
}

## Requirements
- First analyze the project structure (using read_file/grep), then generate the plan.
- goal must be concise (one sentence).
- steps: at least 1, at most 10.
- Each action must be executable and verifiable.
- Explicitly list all files that need to be modified.
- verification_strategy must be realistic and actionable."""

_FALLBACK_PLAN_PROMPT = """Generate an execution plan based on the task description. Output JSON directly without analysis."""


class PlanAgent:
    """Planning-phase Agent — read-only mode, generates structured Plans."""

    def __init__(
        self,
        agent_id: str,
        backend: Backend,
        tools: ToolRegistry,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.backend = backend
        self.tools = tools
        self.config = config or {}
        self.guard = PlanModeGuard()
        self._plan: Plan | None = None

    @property
    def plan(self) -> Plan | None:
        return self._plan

    async def create_plan(self, task: str, context: AgentContext) -> Plan:
        """Generate a structured Plan.

        Analyzes the project in read-only mode and calls the Backend to generate a Plan.
        Does not modify any files.

        Returns:
            Plan object.

        Raises:
            RuntimeError: Generation failed or max steps exceeded.
        """
        # Phase 1: Build the planner prompt
        prompt = self._build_planner_prompt(task, context)

        # Phase 2: Call LLM to generate Plan
        request = BackendRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_PLANNER_SYSTEM_PROMPT,
            max_tokens=self.config.get("max_tokens", 2048),
            temperature=self.config.get("temperature", 0.3),
        )

        try:
            response: BackendResponse = await run_sync(self.backend.invoke, request)
        except BackendError as e:
            raise RuntimeError(f"Plan generation — Backend call failed: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Plan generation — Network error: {e}") from e

        raw = (response.content or "").strip()
        if not raw:
            raise RuntimeError("Plan generation — Backend returned empty response")

        # Phase 3: Parse JSON → Plan
        plan = self._parse_plan(raw)
        self._plan = plan

        logger.info(
            "Plan generated: %s (%d steps, %d files)",
            plan.goal, len(plan.steps), len(plan.files_to_modify),
        )
        return plan

    def _build_planner_prompt(self, task: str, context: AgentContext) -> str:
        """Build the planner prompt sent to the LLM.

        Uses RepositoryScanner to discover source files in the user project root.
        NEVER scans workspace/ or .state/ directories.

        The project root is resolved in this order:
          1. context.config["project_path"] (explicitly set by Runtime or user)
          2. RepositoryScanner.find_project_root() (auto-detect from CWD)
          3. context.workspace (fallback, but only the project portion)
        """
        lines = [f"## Task\n{task}\n"]

        # ── Resolve project root ───────────────────────────────
        project_root = context.config.get("project_path")
        if project_root:
            project_root = Path(project_root).resolve()
        else:
            detected = RepositoryScanner.find_project_root()
            if detected:
                project_root = detected
                logger.info("Auto-detected project root: %s", project_root)

        # ── Scan project files (NOT workspace/ or .state/) ─────
        repo_info: RepositoryInfo | None = context.metadata.get("repo_info")
        if repo_info is None and project_root:
            try:
                repo_info = RepositoryScanner.scan(project_root)
                context.metadata["repo_info"] = repo_info
                logger.info(
                    "Repository scanned: %s (%d source files, %d test files)",
                    project_root, len(repo_info.source_files), len(repo_info.test_files),
                )
            except Exception as e:
                logger.warning("Repository scan failed: %s", e)

        if repo_info and repo_info.file_count > 0:
            lines.append(repo_info.summary)
        elif project_root:
            # Fallback: just list the project directory (briefly)
            try:
                entries = sorted(
                    p.name for p in Path(project_root).iterdir()
                    if not RepositoryScanner.is_excluded_dir(p.name)
                )
                if entries:
                    lines.append("## 项目目录")
                    for name in entries[:30]:
                        lines.append(f"  - {name}")
            except Exception:
                pass

        lines.append("")
        lines.append(
            "Analyze the project structure above, then generate the execution plan in JSON format. "
            "Output the JSON directly without analysis."
        )
        return "\n".join(lines)

    def _parse_plan(self, raw: str) -> Plan:
        """Parse the LLM's JSON response into a Plan object.

        Supports plain JSON and ```json...``` wrapped formats.
        """
        import json

        text = raw.strip()
        if text.startswith("```"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start: end + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Plan JSON parse failed: {e}")

        valid, reason = validate_plan_dict(data)
        if not valid:
            raise RuntimeError(f"Invalid Plan format: {reason}")

        from zmai.swe.models import PlanStep
        steps = [PlanStep.from_dict(s) for s in data["steps"]]
        return Plan(
            goal=data["goal"],
            steps=steps,
            assumptions=data.get("assumptions", []),
            files_to_modify=data.get("files_to_modify", []),
            expected_outcome=data.get("expected_outcome", ""),
            verification_strategy=data.get("verification_strategy", ""),
            estimated_complexity=data.get("estimated_complexity", "medium"),
            risks=data.get("risks", []),
        )

    def format_plan(self) -> str:
        """Format Plan as human-readable text."""
        if not self._plan:
            return "(No Plan)"
        plan = self._plan
        lines = [
            "━" * 50,
            f"  Plan",
            "━" * 50,
            f"",
            f"  Objective: {plan.goal}",
            f"  Complexity: {plan.estimated_complexity}  |  Steps: {len(plan.steps)}",
        ]
        if plan.assumptions:
            lines.append(f"  Assumptions:")
            for a in plan.assumptions:
                lines.append(f"    • {a}")
        if plan.files_to_modify:
            lines.append(f"  Files: {', '.join(plan.files_to_modify)}")
        if plan.expected_outcome:
            lines.append(f"  Expected: {plan.expected_outcome[:200]}")
        if plan.verification_strategy:
            lines.append(f"  Verification: {plan.verification_strategy[:200]}")
        if plan.risks:
            lines.append(f"  Risks:")
            for r in plan.risks:
                lines.append(f"    ⚠ {r}")
        lines.append("")
        lines.append(f"  Steps:")
        for s in plan.steps:
            tool_str = f" [{s.tool}]" if s.tool else ""
            lines.append(f"    {s.id}. {s.action}{tool_str}")
            if s.expected_outcome:
                lines.append(f"       → {s.expected_outcome[:100]}")
        lines.append("")
        lines.append("━" * 50)
        return "\n".join(lines)
