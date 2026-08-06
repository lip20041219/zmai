# ZMAI Tool System Architecture Review

**Reviewer:** Principal Software Engineer (10+ years)
**Focus:** Tool System only. No Runtime changes.
**Goal:** Find design flaws causing write_file/browser failures.

---

## P0 — Must Fix

### P0.1 `_resolve_tool_path` uses workspace_path for GrepTool but project_path for write_file

**File:** `src/zmai/swe/tools.py:400`

```python
# GrepTool searches workspace_path (agent sandbox)
root = context.workspace_path
for f in sorted(root.rglob("*")):
```

**Problem:** GrepTool searches the **workspace sandbox** (`workspace/agent_xxx/`), while WriteFileTool writes to `project_path` (terminal CWD). The Agent reads files from sandbox, writes to project root — they're different directories. Agent can't find files it wrote.

**Fix:** Change GrepTool to search `project_path` if available, fall back to `workspace_path`:

```python
root = context.project_path or context.workspace_path
```

---

### P0.2 PowerShell fallback is syntactically broken

**File:** `src/zmai/swe/tools.py:248-265`

```python
# Attempt 2: PowerShell
ps_cmd = f"Set-Content -Path '{full}' -Value @'\n{content}\n'@ -Encoding UTF8"
```

**Problem:** The PowerShell here-string `@'...'@` requires the closing `'@` to be on its OWN line at column 0. The code embeds it inline. This PowerShell command will ALWAYS fail with a syntax error. The fallback never works.

**Fix:** Remove the PowerShell fallback entirely. It's dead code. `Path.write_text()` and `open()` cover all cases. If those two fail, PowerShell won't help.

---

### P0.3 `_translate_cmd` prefix-matches `"ls "` but not `"ls"` alone

**File:** `src/zmai/swe/tools.py:430-448`

```python
_WINDOWS_CMD_MAP: dict[str, str] = {
    "ls": "dir",     # ← matches "ls some/path"
    "ls ": "dir ",   # ← ALSO matches "ls some/path" (same key prefix!)
    "pwd": "cd",
    "cat ": "type ",
}
```

`"ls"` and `"ls "` — if `stripped.startswith("ls ")`, BOTH `"ls"` and `"ls "` match because `"ls "` starts with `"ls"`. The dict iteration order means `"ls "` wins, which is correct. But `"ls"` alone (no space, just the command `ls`) matches `"ls"` → `"dir"` without space, producing `"dir"` which is correct. Actually this works by accident, not design.

**Fix:** Replace with a simple single-map approach:

```python
if stripped == "ls" or stripped.startswith("ls "):
    cmd = "dir" + cmd[2:]
```

---

## P1 — Should Fix

### P1.1 `_resolve_tool_path` re-resolves every call

**File:** `src/zmai/swe/tools.py:41-86`

Every tool call resolves `project_path` from string via `Path(project_path).resolve()`. This is unnecessary overhead. The ToolContext should store `project_path` as a `Path` object, not a string.

**Fix:** In `main.py`, change `config.set("project_path", terminal_cwd)` to store `Path` objects instead of strings. Or convert once in ToolContext construction.

---

### P1.2 PowerShell fallback is dead code (related to P0.2)

After removing the PowerShell attempt, the fallback chain goes:
1. `Path.write_text()`
2. `open()` with explicit encoding

These two cover every scenario. Remove the PowerShell block entirely.

---

### P1.3 Agent `step()` creates ToolContext with hardcoded timeout=30

**File:** `src/zmai/swe/agent.py:204-210`

```python
tctx = ToolContext(
    agent_id=context.agent_id,
    workspace_path=context.workspace or Path("."),
    project_path=context.config.get("project_path"),
    config=context.config,
    timeout=30,  # <-- hardcoded
)
```

ToolRouter line 57 also has a separate timeout handling:
```python
effective_timeout = context.timeout or self._default_timeout
```

**Fix:** Read timeout from `context.config` or `context` metadata instead of hardcoding 30.

---

### P1.4 `ReadFileTool` and `EditTool` use `full.exists()` check that fails after `os.chdir`

After `os.chdir` changes the CWD to project root, relative paths resolve against project root. But if `project_path` is set to terminal CWD (which may differ from project root after chdir), the path might resolve to a directory that doesn't exist.

**Fix:** Ensure `_resolve_tool_path` always returns paths within `project_path` first, and that `project_path` is an absolute path.

---

### P1.5 `EditTool.append` appends `\n` unconditionally

```python
f.write(new_text + ("\n" if not new_text.endswith("\n") else ""))
```

This always ensures a trailing newline. For binary files or files where trailing newlines matter, this changes content unexpectedly.

**Fix:** Write the content exactly as provided. Don't append newlines.

---

## P2 — Should Fix

### P2.1 `_emit_tool_result` uses `_quiet` config flag — fragile

A magic string `"_quiet"` in config dict controls logging output. This should be a proper field on ToolContext or a logging level.

**Fix:** Add a `verbose: bool = True` field to ToolContext instead.

---

### P2.2 `_resolve_tool_path` copies project_path resolution logic twice

Lines 55-67 (absolute path) and 69-78 (relative path) both convert `project_path` from string to Path and call `resolve()`. Code duplication.

**Fix:** Extract to a helper: `def _get_safe_base(context) -> Path | None`.

---

### P2.3 `GrepTool` ignores `project_path`

Greps only workspace sandbox, not project root. If the user asks "find 'function' in my code", it searches the empty agent sandbox.

**Fix:** Same as P0.1 — search `project_path` when available.

---

### P2.4 `ShellTool` always runs in workspace_path, not project_path

```python
subprocess.run(cmd, shell=True, cwd=str(context.workspace_path), ...)
```

Shell commands run in the agent sandbox, not the user's project directory. `dir` shows workspace files, not project files.

**Fix:** Set `cwd` to `project_path` when available, fall back to `workspace_path`.

---

### P2.5 ToolRouter creates ToolContext without `project_path`

```python
exec_context = ToolContext(
    agent_id=context.agent_id,
    workspace_path=context.workspace_path,
    project_path=context.project_path,  # <-- added but only in ToolRouter
    ...
)
```

Already fixed for ToolRouter. But `_make_tool_context` in Runtime still doesn't pass `project_path`.

**Fix:** Add `project_path` to Runtime's `_make_tool_context`.

---

## Summary

| ID | Issue | File | Severity |
|----|-------|------|----------|
| P0.1 | GrepTool searches workspace, not project | `tools.py:400` | P0 |
| P0.2 | PowerShell fallback always fails | `tools.py:248-265` | P0 |
| P0.3 | `_translate_cmd` fragile prefix matching | `tools.py:430-448` | P0 |
| P1.1 | project_path string→Path on every call | `tools.py:41-86` | P1 |
| P1.2 | Remove dead PowerShell code | `tools.py:248-265` | P1 |
| P1.3 | Hardcoded timeout=30 in agent | `swe/agent.py:210` | P1 |
| P1.4 | exists() checks after chdir | `tools.py:185,314` | P1 |
| P1.5 | EditTool unconditional newline | `tools.py:322` | P1 |
| P2.1 | _quiet magic string | `tools.py:22` | P2 |
| P2.2 | Duplicate path resolution code | `tools.py:55-78` | P2 |
| P2.3 | Grep ignores project_path | `tools.py:400` | P2 |
| P2.4 | Shell runs in workspace, not project | `tools.py:479` | P2 |
| P2.5 | _make_tool_context missing project_path | `runtime.py:249-255` | P2 |

## Minimal Fix Plan

### P0 (3 changes, ~10 lines)

1. `tools.py:400` — Change `root = context.workspace_path` to `root = context.project_path or context.workspace_path`
2. `tools.py:248-265` — Remove entire PowerShell fallback block (dead code)
3. `tools.py:430-448` — Simplify `_translate_cmd` to direct mapping

### P1 (4 changes, ~15 lines)

1. `tools.py` — Convert `project_path` to Path once in `_resolve_tool_path` signature
2. `swe/agent.py:210` — Replace `timeout=30` with `context.config.get("timeout", 30)`
3. `tools.py:322` — Remove trailing newline append in EditTool
4. `tools.py:185,314` — Ensure resolved path info in error messages

### P2 (3 changes, ~10 lines)

1. `tools.py:479` — Set `cwd` to `project_path` when available
2. `tools.py:400` — GrepToll searches project_path
3. `runtime.py:254` — Add `project_path` to `_make_tool_context`
