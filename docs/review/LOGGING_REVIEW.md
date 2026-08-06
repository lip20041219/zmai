# ZMAI Logging Review

> 审查日期: 2026-07-17
> 范围: 所有 Tool、Runtime、Gateway 的日志输出
> 目标: 统一日志格式，消除 "ok"/"fail" 简单消息

---

## 一、当前日志全景

ZMAI 当前存在 **4 套独立的日志/输出机制**，互不协调：

| 机制 | 使用方 | 输出目标 | 格式 |
|------|--------|---------|------|
| Python `logging` | ToolRouter、Backend、ToolRegistry、Runtime | stderr（默认） | `LEVEL:logger:msg` |
| `_emit_tool_result` | 全部 8 个 SWE Tool | stderr | 结构化文本块 |
| `on_progress` 回调 | Runtime → CLI(REPL/oneshot) | stderr | 简写标签 |
| `print_*` / formatters | CLI（config/auth/doctor） | stdout/stderr | ANSI 着色文本 |

### 1.1 各组件日志量

| 组件 | 日志行数 | 主要输出方式 | 中文/英文 |
|------|---------|-------------|-----------|
| Tools (swe/tools.py) | ~32 行 | `_emit_tool_result` + `sys.stdout.write` | 英文（SUCCESS/FAIL） |
| Runtime (runtime.py) | ~1 行 logger + ~10 行 on_progress | `logger.error` + `on_progress` | 中文 mixed |
| Gateway ToolRouter | ~3 行 | `logger.debug/error` | 中文 |
| Gateway Claude Backend | ~5 行 | `logger.info/warning/error` | 中文 |
| Gateway DeepSeek Backend | ~2 行 | `logger.info/warning` | 中文 |
| ToolRegistry | ~5 行 | `logger.debug/warning/error` | 中文 |
| CLI progress callback | ~4 行 | `sys.stderr.write` | 英文（ok/fail） |

### 1.2 所有的 "ok"/"fail" 出现位置

| 位置 | 代码 | 输出 |
|------|------|------|
| `cli/main.py:270` | `'ok' if ok else 'fail'` | `ok` / `fail` |
| `cli/main.py:314` | `'ok' if ok else 'fail'` | `ok` / `fail` |
| `swe/tools.py:25` | `SUCCESS` / `FAIL` | `SUCCESS` / `FAIL` |
| `runtime.py:251` | `tag = "OK" if result.success else "FAIL"` | `OK:` / `FAIL:` |

**问题**：同一事件在 cli/main.py 中被简化为 `ok`/`fail`，丢失了所有上下文（工具名、目标路径、耗时、错误原因）。

---

## 二、核心问题

### 2.1 "ok"/"fail" 信息饥荒

```
当前 CLI 输出:
> read_file
    ok
> write_file
    fail

用户看到 "fail" 但不知道:
- 哪个文件写失败了？
- 为什么失败？（权限？磁盘满？路径错误？）
- 花了多久？

需要变成:
  read_file  src/main.py        12 lines  0.03s
  write_file src/output.html    error: Permission denied  0.01s
```

### 2.2 三套独立格式

**格式 A — `_emit_tool_result`（tools.py）**:
```
[read_file]
  Target:   src/main.py
  Workspace: D:\project\workspace
  Project:  D:\project
  Result:   SUCCESS (0.02s)
```

**格式 B — `on_progress`（runtime.py → cli/main.py）**:
```
> read_file
    ok
```

**格式 C — Python logging（gateway/registry）**:
```
INFO:zmai.gateway.tool_router:工具执行完成: read_file (23 ms)
```

**同一事件出现三次，三种不同格式，无法关联。**

### 2.3 其他问题

| 问题 | 说明 |
|------|------|
| 无日志级别 | `_emit_tool_result` 和 `on_progress` 无条件输出，无法静默 |
| 无 Correlation ID | 无法将 tool call → backend invoke → tool result 关联到同一请求 |
| 无 JSON 模式 | `--json` 只影响任务结果，不影响工具执行日志 |
| 无耗时统一单位 | tools.py 用秒（0.02s），tool_router 用毫秒（23ms） |
| 无 Token 用量 | Gateway 收集了 `TokenUsage`，但从未输出到任何日志 |
| `_quiet` 死代码 | `_emit_tool_result` 检查 `context.config.get("_quiet")` 但没有任何代码设置它 |
| 日志目标混乱 | stdout（print_success）、stderr（progress、logger、_emit_tool_result）混用 |

---

## 三、统一日志设计

### 3.1 设计原则

1. **单一格式** — 所有组件输出相同结构的日志
2. **可静默** — 支持 `--quiet` / `--verbose` / 日志级别控制
3. **可关联** — 每个 Agent 执行链有 trace_id
4. **结构化** — JSON 模式与人类可读模式并存
5. **无 "ok"/"fail"** — 用完整状态描述代替布尔标签

### 3.2 日志行格式

```
{timestamp} {level} {trace_id} [{component}] {event} {details}
```

**示例**：
```
10:30:01.234 INFO  a1b2c3 [runtime]  agent=agent_xyz  task="refactor auth"  backend=deepseek
10:30:01.456 INFO  a1b2c3 [gateway]  invoke  model=deepseek-chat  tokens=0→0  mode=stream
10:30:01.789 INFO  a1b2c3 [tool]     read_file  src/auth/store.py  lines=1-50  size=1.2KB  0.02s
10:30:02.012 WARN  a1b2c3 [tool]     write_file  src/auth/config.py  error="Permission denied"  0.01s
10:30:02.234 INFO  a1b2c3 [gateway]  response  stop_reason=end_turn  tokens=152→45  model=deepseek-chat  1.2s
10:30:02.456 INFO  a1b2c3 [runtime]  complete  agent=agent_xyz  steps=3  duration=3.2s
```

### 3.3 字段定义

| 字段 | 格式 | 说明 | 示例 |
|------|------|------|------|
| `timestamp` | `HH:MM:SS.fff` | 本地时间 | `10:30:01.234` |
| `level` | `DEBUG/INFO/WARN/ERROR` | 日志级别 | `INFO` |
| `trace_id` | 短哈希（前 8 位） | Agent 执行链 ID | `a1b2c3d4` |
| `component` | `[runtime]/[gateway]/[tool]/[auth]/[memory]` | 组件名 | `[tool]` |
| `event` | 动词 | 事件名 | `read_file` / `invoke` / `complete` |
| `details` | `key=value [key=value]` | 结构化详情 | `src/auth/store.py lines=1-50` |

### 3.4 日志级别映射

| 级别 | 用途 | 示例场景 |
|------|------|---------|
| `DEBUG` | 执行细节 | 完整 API 请求/响应体、memory 条目内容 |
| `INFO` | 正常流程 | 工具调用、backend invoke、task 完成 |
| `WARN` | 可恢复问题 | 重试、降级、配置缺失但有默认值 |
| `ERROR` | 失败 | 工具执行失败、API 调用最终失败 |

### 3.5 各组件日志事件

**Tools（全部 8 个）：**

| Tool | event | details |
|------|-------|---------|
| `read_file` | `read_file` | `path=<path> lines=<start>-<end> size=<size> <duration>` |
| `write_file` | `write_file` | `path=<path> size=<bytes> <duration>` |
| `edit` | `edit` | `path=<path> mode=<replace|regex|insert|append> <duration>` |
| `grep` | `grep` | `pattern=<pattern> glob=<glob> matches=<n> <duration>` |
| `shell_exec` | `shell_exec` | `cmd=<cmd[:60]> exit=<code> <duration>` |
| `git` | `git` | `cmd=<cmd[:40]> exit=<code> <duration>` |
| `show_to_user` | `show_to_user` | `size=<chars>` |
| `open_in_browser` | `open_in_browser` | `path=<path> <duration>` |

所有 tool 在**成功时用 INFO，失败时用 WARN/ERROR**。不再用 "SUCCESS"/"FAIL" 标签。

**Runtime：**

| event | 时机 | details |
|-------|------|---------|
| `start` | Agent 开始运行 | `agent=<id> task=<task[:60]> backend=<name>` |
| `restore` | 记忆恢复 | `agent=<id> entries=<n>` |
| `complete` | Agent 成功完成 | `agent=<id> steps=<n> duration=<s>` |
| `cancelled` | Agent 被取消 | `agent=<id> steps=<n>` |
| `failed` | Agent 执行失败 | `agent=<id> error=<msg[:80]> steps=<n>` |

**Gateway：**

| event | 时机 | details |
|-------|------|---------|
| `invoke` | Backend 请求开始 | `model=<name> mode=<stream|batch> tokens_in=<n>` |
| `response` | Backend 响应到达 | `stop_reason=<reason> tokens_in=<n> tokens_out=<n> duration=<s>` |
| `retry` | 自动重试 | `attempt=<n> max_retries=<n> wait=<s>` |
| `error` | Backend 调用失败 | `error=<msg[:80]> attempt=<n>` |

---

## 四、实施建议

### 4.1 Logger 类设计

统一由 `zmai.runtime.logger` 提供服务，所有组件通过 Logger 实例输出：

```python
# zmai/logging.py (新建)

import logging
import hashlib
import os
from datetime import datetime

class ZMAILogger:
    """ZMAI 统一日志器。每个组件获取一个子 logger。"""

    LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
              "warn": logging.WARNING, "error": logging.ERROR}

    def __init__(self, name: str, level: str = "info"):
        self._logger = logging.getLogger(f"zmai.{name}")
        self._logger.setLevel(self.LEVELS.get(level, logging.INFO))
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)-5s %(trace_id)s [%(component)s]  %(message)s",
                datefmt="%H:%M:%S",
            ))
            self._logger.addHandler(handler)

    def _log(self, level, trace_id, component, event, **details):
        detail_str = "  ".join(f"{k}={v}" for k, v in details.items() if v is not None)
        extra = {"trace_id": trace_id or "-", "component": component}
        self._logger.log(level, f"{event}  {detail_str}", extra=extra)
```

### 4.2 消除 "ok"/"fail" 的改动点

| 文件 | 改动 | 效果 |
|------|------|------|
| `cli/main.py:265-274` | 删除旧的 `on_progress`，改用 `ZMAILogger` 输出 | `ok`/`fail` → 完整日志行 |
| `cli/main.py:307-318` | 同上（oneshot 模式） | 同上 |
| `runtime.py:251-254` | `on_progress("result", f"{tag}:{brief}")` → `runtime_log.info(trace_id, "tool", ...)` | 携带全部上下文 |
| `swe/tools.py:18-38` | `_emit_tool_result` 改为调用 `ZMAILogger` | 与 gateway/runtime 同格式 |

### 4.3 静默模式

通过 `--quiet` 或 `ZMAI_LOG_LEVEL` 控制：

```python
# cli/main.py: 启动时根据 flags 设置日志级别
log_level = "warn" if args.quiet else ("debug" if args.verbose else "info")
os.environ["ZMAI_LOG_LEVEL"] = log_level
```

| 模式 | 日志级别 | 效果 |
|------|---------|------|
| `--quiet` | WARN | 仅显示错误 |
| 默认 | INFO | 工具调用 + 关键事件 |
| `--verbose` | DEBUG | 完整请求/响应 |

### 4.4 Trace ID 生成

在 runtime.py 中 `run()` 开始时生成，通过 AgentContext 传递：

```python
# runtime.py
trace_id = hashlib.sha256(f"{agent_id}:{time.time()}:{os.urandom(4)}".encode()).hexdigest()[:8]
```

### 4.5 输出对比

**当前（混乱）:**
```
> read_file
    ok
> write_file
    fail
  [deepseek] 模型耗时: 1.2s
  ✓ done
```

**统一后:**
```
10:30:01 INFO  a1b2 [tool]     read_file  src/main.py  lines=1-50  0.02s
10:30:02 WARN  a1b2 [tool]     write_file  src/output.html  error="Permission denied"  0.01s
10:30:03 INFO  a1b2 [gateway]  response  stop_reason=end_turn  tokens=152→45  1.2s
10:30:03 INFO  a1b2 [runtime]  complete  agent=agent_xyz  steps=3  duration=3.2s
```

---

## 五、实施优先级

| P | 改动 | 文件 | Effort | 价值 |
|---|------|------|--------|------|
| P0 | 新建 `zmai/logging.py` — ZMAILogger 类 | 新建 | 2h | 基础设施 |
| P0 | 消除 cli/main.py 中的 "ok"/"fail" | `cli/main.py` | 1h | **消除最差 UX** |
| P1 | swe/tools.py — `_emit_tool_result` 改用统一 Logger | `swe/tools.py` | 1h | Tool 日志统一 |
| P1 | runtime.py — on_progress 改用统一 Logger | `runtime.py` | 1h | Runtime 日志统一 |
| P2 | gateway/ — backend 和 tool_router 日志改为结构化 | `gateway/*.py` | 2h | Gateway 日志统一 |
| P2 | 添加 trace_id 贯穿调用链 | `runtime.py` + `agent/base.py` | 2h | 日志可关联 |
| P3 | 支持 `--quiet` / `--verbose` | `cli/main.py` | 1h | 日志级别控制 |
| P3 | JSON 日志模式 | `zmai/logging.py` | 2h | 机器可读 |

---

## 六、附录：当前 vs 未来对比

### 6.1 Tool 日志

```
当前 (_emit_tool_result):
[read_file]            ← 工具名
  Target:   src/main.py  ← 路径
  Workspace: D:\project
  Project:  D:\project
  Result:   SUCCESS (0.02s)  ← 带 SUCCESS/FAIL

统一后:
10:30:01 INFO  a1b2 [tool]  read_file  path=src/main.py  lines=1-50  0.02s
```

### 6.2 Runtime 日志

```
当前 (on_progress):
> read_file
    ok

统一后:
10:30:01 INFO  a1b2 [runtime]  start  agent=agent_xyz  task="refactor auth"
10:30:01 INFO  a1b2 [runtime]  restore  entries=5
10:30:03 INFO  a1b2 [runtime]  complete  agent=agent_xyz  steps=3  duration=3.2s
```

### 6.3 Gateway 日志

```
当前 (logger):
INFO:zmai.gateway.tool_router:工具执行完成: read_file (23 ms)

统一后:
10:30:01 INFO  a1b2 [gateway]  invoke  model=deepseek-chat  mode=stream
10:30:03 INFO  a1b2 [gateway]  response  stop_reason=end_turn  tokens=152→45  1.2s
```

---

*Report generated by `claude` — 基于全代码库日志审计*
