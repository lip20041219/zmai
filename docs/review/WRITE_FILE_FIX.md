# ZMAI WriteFile Fix v1.0

## Problem

`write_file` succeeds but writes to the workspace sandbox (`workspace/agent_xxx/`) instead of the terminal directory where `zmai` was launched.

## Root Cause

```
main.py: config.set("project_path", str(root))     ✅ stored in Config
main.py: config={}  →  Runtime.run(config={})        ❌ not passed through
swe/agent.py: project_path=context.config.get(...)   → None
swe/tools.py: project_path is None → fallback to workspace sandbox
```

## Changes

### 1. `_resolve_tool_path` — fix path type handling, allow absolute paths

Convert `project_path` from string to Path if needed. Allow absolute paths but validate they stay within project scope. Return detailed error messages.

### 2. `main.py` — pass project_path through to Runtime.run()

Capture terminal CWD at startup, store in Config, pass through `config` dict to `Runtime.run()` → `AgentContext.config` → `ToolContext.project_path`.

### 3. `WriteFileTool` — detailed error reporting

Return the actual resolved path in success and the real OS error in failure.

## Data Flow After Fix

```
User launches zmai in D:\AIProject
  → main() captures cwd = D:\AIProject
  → config = Config(); config.set("project_path", "D:\AIProject")
  → Runtime.run(config={"project_path": "D:\AIProject"})
    → AgentContext.config = {"project_path": "D:\AIProject"}
    → ToolContext(project_path="D:\AIProject")
    → WriteFileTool.execute()
      → _resolve_tool_path(project_path="D:\AIProject", "index.html")
        → D:\AIProject\index.html
        → ✅ writes to D:\AIProject\index.html
```
