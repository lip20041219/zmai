"""Planner — generates, parses, and formats execution plans.

Responsibilities:
  1. generate_plan() — calls Backend to produce a structured Plan JSON.
  2. parse_plan_response() — extracts and parses Plan from LLM response.
  3. Integrated with SWEAgent.step(), called by SWEAgent when needed.

Does not use a standalone PlanAgent class; operates as an internal phase
within SWEAgent.step().
"""

from __future__ import annotations

import json
import logging
from typing import Any

from zmai.gateway.base import Backend, BackendRequest, BackendResponse
from zmai.swe.models import Plan, PlanStep, validate_plan_dict

logger = logging.getLogger("zmai.swe.planner")

# ── Plan-specific System Prompt ──────────────────────────────

_PLAN_SYSTEM_PROMPT = """You are a task planner. Your goal is to decompose the user's task into executable steps.

Output MUST be strict JSON. Do not include any other text. Do NOT wrap in markdown code blocks.

Available tools:
- read_file: Read file content
- write_file: Write/overwrite file
- edit: Line-level editing (replace/regex/insert/append)
- grep: Search text
- shell_exec: Execute shell commands
- git: Execute git commands
- show_to_user: Print content to terminal
- open_in_browser: Open HTML in browser

Output JSON format:
{
  "goal": "One-sentence task objective",
  "steps": [
    {
      "id": 1,
      "action": "Description of the action",
      "tool": "Expected tool name (or null)",
      "params": {"key": "value"},
      "expected_outcome": "What you expect to see after completion",
      "verification_strategy": "How to verify this step succeeded"
    }
  ],
  "estimated_complexity": "simple | medium | complex",
  "risks": ["Risk point 1", "Risk point 2"]
}

Requirements:
- goal must be concise (one sentence).
- steps: at least 1, at most 10.
- Each action must be executable and verifiable.
- verification_strategy must be realistic (e.g. "file_exists", "grep pattern", "shell_exec exit 0").
- If multiple files are involved, distribute across different steps.
- Do NOT combine verification and delivery into the same step."""  # noqa: E501


def generate_plan(task: str, backend: Backend, config: dict[str, Any] | None = None) -> Plan:
    """Call Backend to generate a structured Plan.

    Args:
        task: User task description.
        backend: Available Backend instance.
        config: Optional config (max_tokens, temperature, etc.).

    Returns:
        Parsed Plan object.

    Raises:
        ValueError: JSON parse failure or invalid Plan format.
        RuntimeError: Backend invocation failure.
    """
    bc = config or {}
    request = BackendRequest(
        messages=[{"role": "user", "content": task}],
        system_prompt=_PLAN_SYSTEM_PROMPT,
        max_tokens=bc.get("max_tokens", 1024),
        temperature=bc.get("temperature", 0.3),
    )

    response: BackendResponse = backend.invoke(request)
    raw = response.content or ""

    if not raw.strip():
        raise ValueError("Backend returned empty Plan response")

    return parse_plan_response(raw)


def parse_plan_response(raw: str) -> Plan:
    """Parse Plan from LLM response text.

    Supports:
      - Plain JSON
      - JSON wrapped in ```json ... ```

    Args:
        raw: Raw text returned by the LLM.

    Returns:
        Parsed Plan object.

    Raises:
        ValueError: JSON parse failure or invalid Plan format.
    """
    text = raw.strip()

    # Try to extract ```json ... ``` wrapped content
    if text.startswith("```"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Plan JSON parse failed: {e}\nRaw response: {raw[:300]}") from e

    valid, reason = validate_plan_dict(data)
    if not valid:
        raise ValueError(f"Invalid Plan format: {reason}")

    steps = [PlanStep.from_dict(s) for s in data["steps"]]
    return Plan(
        goal=data["goal"],
        steps=steps,
        estimated_complexity=data.get("estimated_complexity", "medium"),
        risks=data.get("risks", []),
    )
