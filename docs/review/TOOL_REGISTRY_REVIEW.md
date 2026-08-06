# ZMAI ToolRegistry Review

## 问题

每次用户在 REPL 中输入任务，`SWEAgent.initialize()` 被调用，向 `ToolRegistry` 重新注册所有 8 个工具。因为 `ToolRegistry` 是 `Runtime` 持有的单例，工具早已存在，每次触发 "工具已存在，将被覆盖" 警告。

## 重现路径

```
REPL 第 1 次输入:
  _repl_run("读取 README")
    → runtime.run()
      → SWEAgent("repl_xxx_1").initialize(ctx)
        → context.tools(同一个 Runtime 的 ToolRegistry).register(ReadFileTool)  ✅ 首次注册
        → context.tools.register(WriteFileTool)  ✅

REPL 第 2 次输入:
  _repl_run("总结")
    → runtime.run()
      → SWEAgent("repl_xxx_2").initialize(ctx)
        → context.tools(同一个 Runtime 的 ToolRegistry).register(ReadFileTool)  ⚠ "工具已存在，将被覆盖"
        → context.tools.register(WriteFileTool)  ⚠
```

每次产生 8 条 warning。

## 修复

`swe/agent.py:160-168` — 注册前检查工具是否已存在：

```python
existing = {t.name for t in context.tools.list()}
for tool in [...]:
    if tool.name not in existing:
        context.tools.register(tool)
```

只注册新工具。已存在的跳过。这是最小的改动，不修改 Runtime，不改 ToolRegistry 的行为。
