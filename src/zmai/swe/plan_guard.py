"""PlanModeGuard — tool permission guard for Plan mode.

Before Plan confirmation:
  - Read-only tools (read_file, grep, show_to_user) → allowed
  - Write tools (write_file, edit) → denied
  - Dangerous shell commands (rm, del, mv, cp, >, etc.) → denied
  - Safe shell commands (echo, dir, cd, python, etc.) → allowed

After confirmation, guard is disarmed and all tools can be used normally.
"""

from __future__ import annotations

import re
from typing import Any

from zmai.tool import ToolContext, ToolResult

# ── Tool classification ────────────────────────────────────────────

_READ_ONLY_TOOLS = frozenset({
    "read_file",
    "grep",
    "show_to_user",
    "open_in_browser",
})

_WRITE_TOOLS = frozenset({
    "write_file",
    "edit",
})

_GIT_WRITE_COMMANDS = frozenset({
    "add", "commit", "push", "pull", "merge",
    "branch", "checkout", "reset", "rebase",
    "tag", "rm", "mv",
    "stash", "cherry-pick", "apply",
})

_DANGEROUS_CMD_PATTERNS: list[tuple[str, str, str]] = [
    # (regex pattern, example, reason)
    (r"^\s*(rm|del|erase)\s+", "rm file.txt", "delete file"),
    (r"^\s*(rmdir|deltree)\s+", "rmdir /s dir", "delete directory"),
    (r"^\s*(mv|move|ren|rename)\s+", "mv a.txt b.txt", "move/rename file"),
    (r"^\s*(cp|copy|xcopy|robocopy)\s+", "cp a.txt b.txt", "copy file"),
    (r"^\s*mkdir\s+", "mkdir newdir", "create directory"),
    (r"^\s*(chmod|chown|attrib)\s+", "chmod +x file", "modify permissions"),
    (r"[>]{1,2}\s", "echo x > file.txt", "output redirection (write file)"),
    (r"[|]\s*(tee|out-file|set-content)", "echo x | tee file.txt", "pipe to file"),
    (r"^\s*(format|fdisk|dd|mkfs)\s+", "format d:", "disk operation"),
    (r"^\s*(npm|yarn|pnpm)\s+(install|add|publish|remove)", "npm install", "package manager write"),
]

_SAFE_CMD_PREFIXES = frozenset({
    "echo", "dir", "ls", "cd", "pwd", "type", "cat",
    "python", "node", "deno", "go run", "rustc",
    "gcc", "clang",
    "pip list", "pip show",
    "npm list", "npm view",
    "git status", "git diff", "git log", "git show",
    "git --version",
    "where", "which", "find", "findstr",
    "head", "tail", "wc", "sort", "more", "less",
    "date", "time", "ver",
    "tasklist", "ps",
    "help", "man",
})


class PlanModeGuard:
    """Plan Mode guard — blocks write operations before confirmation.

    Usage:
        guard = PlanModeGuard()
        result = guard.check("write_file", {"path": "main.py"}, ctx)
        if not result.success:
            print("blocked:", result.error)
        guard.disarm()  # Call after user confirms Plan
    """

    def __init__(self) -> None:
        self._armed: bool = True

    @property
    def is_armed(self) -> bool:
        return self._armed

    def disarm(self) -> None:
        """Disarm the guard. Called after user confirms the Plan."""
        self._armed = False

    def check(
        self,
        tool_name: str,
        params: dict[str, Any] | None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Check if a tool call is allowed.

        Returns:
            ToolResult.ok() — allowed
            ToolResult.err() — denied (with reason)
        """
        if not self._armed:
            return ToolResult.ok(output="guard disarmed")

        if tool_name in _READ_ONLY_TOOLS:
            return ToolResult.ok(output="allowed (read-only)")

        if tool_name in _WRITE_TOOLS:
            return ToolResult.err(
                f"PlanModeGuard: {tool_name} is forbidden before Plan confirmation. "
                f"Confirm the Plan first before modifying files."
            )

        if tool_name == "shell_exec":
            return self._check_shell(params)

        if tool_name == "git":
            return self._check_git(params)

        # Unknown tool → deny for safety
        return ToolResult.err(
            f"PlanModeGuard: Unknown tool '{tool_name}' is forbidden before Plan confirmation."
        )

    def _check_shell(self, params: dict[str, Any] | None) -> ToolResult:
        """Check if a shell command is safe."""
        cmd = (params or {}).get("command", "")
        if not cmd:
            return ToolResult.err("PlanModeGuard: empty command denied")

        stripped = cmd.strip()

        # ── Check dangerous command patterns (including redirection) ──
        for pattern, example, reason in _DANGEROUS_CMD_PATTERNS:
            if re.search(pattern, stripped, re.IGNORECASE):
                return ToolResult.err(
                    f"PlanModeGuard: Command denied — {reason} ({example}). "
                    f"Commands that may modify files are forbidden before Plan confirmation."
                )

        # ── Check safe command prefixes ──────────────────────────
        for prefix in _SAFE_CMD_PREFIXES:
            if stripped.lower().startswith(prefix):
                return ToolResult.ok(output="allowed (safe command)")

        # ── Contains dangerous keywords (catch-all) ──────────────
        dangerous_keywords = [
            "rm ", "del ", "rd ", "move ", "ren ",
            "install", "uninstall",
            "mkfs", "format",
        ]
        lower = stripped.lower()
        for kw in dangerous_keywords:
            if kw in lower:
                return ToolResult.err(
                    f"PlanModeGuard: Command contains potentially dangerous operation '{kw.strip()}', "
                    f"denied before Plan confirmation."
                )

        # No match → allow
        return ToolResult.ok(output="allowed")

    def _check_git(self, params: dict[str, Any] | None) -> ToolResult:
        """Check if a git command is safe."""
        args = (params or {}).get("args", "")
        if not args:
            return ToolResult.ok(output="allowed")
        first = args.strip().split()[0].lower() if args.strip().split() else ""
        if first in _GIT_WRITE_COMMANDS:
            return ToolResult.err(
                f"PlanModeGuard: git {first} is forbidden before Plan confirmation "
                f"(may modify code history)."
            )
        return ToolResult.ok(output="allowed (read-only git)")
