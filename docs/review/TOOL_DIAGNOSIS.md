# ZMAI Tool System Diagnosis

## Root Cause

**project_path is set on `Config` object but NOT passed to `Runtime.run()`.**

The data flow breaks at a single point:

```
main.py:631  config.set("project_path", str(root))     ✅ Config 对象上有
main.py:287  config={}  →  Runtime.run(config={})       ❌ 空 dict 传进去了
runtime.py:130  AgentContext(config={**(config or {})})  ❌ AgentContext.config = {}
swe/agent.py:207  project_path=context.config.get("project_path")  → None
tool_router.py:61  project_path=context.project_path  → None
```

## Full Chain Trace

### 1. ToolRegistry Registration

```
Runtime.__init__()
  → self._tools = ToolRegistry()
  → _register_available_backends()         ← 注册 Gateway backends
  → (SWEAgent.initialize() 注册 Tools)     ← Tools 注册在第一次任务时
```

**Verdict:** ToolRegistry is empty until SWEAgent.initialize() runs. After init, all 8 tools are registered correctly. Registration is not the issue.

### 2. write_file Call Chain

```
User: "帮我把这个 HTML 写到文件"
  → SWEAgent.step()
    → LLM returns tool_call(name="write_file", params={"path": "test.html", "content": "..."})
    → ToolContext(workspace_path="/workspace/agent_xxx", project_path=None)  ← project_path=None
    → WriteFileTool.execute(tctx, params)
      → _resolve_tool_path(tctx, "test.html")
        → project_path is None → skip project resolution
        → workspace_path / "test.html" → /workspace/agent_xxx/test.html
      → writes to /workspace/agent_xxx/test.html
```

### 3. Write Target

| Agent sends | Resolves to (current) | Resolves to (expected) |
|---|---|---|
| `test.html` | `workspace/agent_xxx/test.html` | `project_root/test.html` |
| `output/test.html` | `workspace/agent_xxx/output/test.html` | `project_root/output/test.html` |
| `./report.md` | `workspace/agent_xxx/report.md` | `project_root/report.md` |

**Verdict:** All files go to workspace sandbox, NOT the project root. User can't find them.

### 4. Current Workspace Root

```
Workspace root:    <project-root>\workspace
Agent workspace:   <project-root>\workspace\repl_XXXX_N\
```

26 stale agent directories exist in workspace/. Each REPL turn creates a new one.

### 5. Current Working Directory (cwd)

```
cwd at startup:    <project-root>         (set by os.chdir(root) in main.py)
shell_exec cwd:    <project-root>\workspace\agent_xxx  (ToolContext.workspace_path)
```

**Verdict:** shell_exec runs in workspace sandbox. Files written by Agent and files visible via shell are in different places.

### 6. Runtime Current Directory

```
Runtime._workspace root:  <project-root>\workspace
Runtime._config:          includes project_path = "<project-root>"
```

The Config object HAS project_path. Runtime.run() doesn't pass it through.

### 7. AgentContext.workspace

```
AgentContext.workspace = <project-root>\workspace\repl_XXXX_N   ✅
AgentContext.config    = {}                                     ❌ empty
```

**Verdict:** workspace is correctly set. config is incorrectly empty.

### 8. Windows Path Handling

```
_resolve_tool_path uses Path.resolve() and Path.relative_to()
On Windows, these handle backslashes and case correctly.
Path traversal protection works: ../test.html and C:/Windows/test rejected.
```

**Verdict:** Path handling is correct. Not the issue.

### 9. UTF-8 Encoding

```
write_text(content, encoding="utf-8")     ✅
read_text(encoding="utf-8")               ✅
shell_exec(encoding="utf-8", errors="replace")  ✅
```

**Verdict:** Encoding is correct. Not the issue.

### 10. Permissions

```
Workspace root writable:  ✅  (confirmed: os.access returns True)
User: <user> (admin)    ✅
```

**Verdict:** Permissions are fine. Not the issue.

### 11. Real Exception

```python
try:
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
except Exception as e:
    return ToolResult.err(f"write error: {e}")
```

The only time this catches an exception is if `full.path` is invalid (e.g., path has invalid chars for Windows) or disk is full. In normal operation, this doesn't throw — the file is silently written to the wrong location.

## Summary

| Check | Result | Issue? |
|-------|--------|--------|
| ToolRegistry | 8 tools registered after init | ✅ |
| write_file call chain | Works correctly, wrong target | ❌ |
| Write target | workspace sandbox | ❌ |
| Workspace Root | `./workspace/` | ✅ |
| cwd | changes per context | ⚠️ |
| Runtime directory | correct | ✅ |
| AgentContext.workspace | correctly set | ✅ |
| Windows paths | correct | ✅ |
| UTF-8 | correct | ✅ |
| Permissions | writable | ✅ |
| Real exception | never thrown (wrong path, not error) | ❌ |

**The tool system doesn't fail. It succeeds at writing to the wrong place.** `write_file` returns "success" with path like `workspace/agent_xxx/test.html`, but the user looks for `test.html` in the project root and doesn't find it. `open_in_browser` opens the file from the sandbox, which is technically correct but the user doesn't know where the file is.

## Fix Required (1 line)

```python
# main.py, _repl_run() and _oneshot_run():
# Change:
config={},
# To:
config={"project_path": config.get("project_path", "")},
```
