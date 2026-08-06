# ZMAI Tool Logging System

## Current State

All 8 tools return `ToolResult.ok("msg")` or `ToolResult.err("msg")`. The progress callback in `main.py` strips this to just `ok` or `fail`:

```python
# Current progress output:
> read_file
    ok
> write_file
    fail
```

No context, no path, no error reason.

## Fix

Add a `_emit_tool_log()` function in `tools.py` that outputs a structured text block to stderr. Each tool calls it before returning.

### Output Format

```
[write_file]
  Target: D:\Project\index.html
  Workspace: D:\Project
  CWD: D:\Project
  Result: SUCCESS (0.02s)
```

```
[write_file]
  Target: D:\Project\index.html
  Workspace: D:\Project
  CWD: D:\Project
  Result: FAIL (0.01s)
  Reason: Permission denied
```

### Called From

Every tool's `execute()` method calls `_emit_tool_log()` just before returning.
