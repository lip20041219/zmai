# ZMAI REPL Design v2.0

Version: 2.0
Date: 2026-07-16

> **默认进入 REPL，持续接受自然语言。不每次退出。**
>
> 不修改 Runtime / Agent / Gateway / Memory / Workspace / Workflow / Backend 模块。
>
> 仅修改 `src/zmai/cli/` 层的交互循环。

---

## 目录

1. [现状审查](#1-现状审查)
2. [设计原则](#2-设计原则)
3. [用户流程](#3-用户流程)
4. [架构设计](#4-架构设计)
5. [输入层](#5-输入层)
6. [输出层](#6-输出层)
7. [上下文管理层](#7-上下文管理层)
8. [内建命令](#8-内建命令)
9. [快捷键](#9-快捷键)
10. [多轮对话](#10-多轮对话)
11. [与现有 CLI 的关系](#11-与现有-cli-的关系)
12. [文件清单与实现计划](#12-文件清单与实现计划)

---

## 1. 现状审查

### 1.1 当前 REPL 实现

```python
# main.py:247-264 (当前 REPL，17 行)
def _cmd_interactive(runtime, config, args):
    theme = _get_theme(args, config)
    session_task = _load_latest_session()
    if session_task:
        print_info(f"last task: {session_task[:60]}", theme)
    try:
        while True:
            try:
                task = input(f"{theme.highlight('zmai> ')}").strip()
            except EOFError:
                break
            if not task:
                continue
            if task.lower() in ("exit", "quit"):
                break
            _cmd_run(task, runtime, config, args)
    except KeyboardInterrupt:
        print()
```

### 1.2 已知缺陷

| 缺陷 | 位置 | 严重性 | 影响 |
|------|------|--------|------|
| **_cmd_run 调用 sys.exit()** | `main.py:244` | 🔴 致命 | REPL 执行第一个任务后进程退出 |
| 无 Readline 历史 | `input()` | 🟡 | 无法上下翻历史 |
| 无 Tab 补全 | `input()` | 🟡 | 无法 TAB 补全命令 |
| 无持久化历史文件 | 不存在 | 🟡 | 关闭终端后历史丢失 |
| 无多行输入 | `input()` 逐行 | 🟡 | 无法粘贴多行代码 |
| Ctrl+C 不处理任务中 | 仅捕获 REPL 层 | 🟡 | 任务执行中按 Ctrl+C 无响应 |
| 每次任务重建 Runtime | `_cmd_run` 内部 | 🟡 | 每次创建新 Runtime、新 Agent |
| 无多轮上下文 | 每次新建 agent_id | 🟡 | 任务间无记忆 |
| 无 Banner | 无 | 🟢 | 缺少进入感 |
| 无内建命令 | 仅有 exit/quit | 🟢 | 无 /help /status 等 |

### 1.3 根因

```
当前设计:
  input() 读取 → _cmd_run() 执行 → sys.exit() 退出
                                      ↑ 这是 bug

问题链:
  1. _cmd_run 末尾有 sys.exit()
  2. REPL 调用 _cmd_run 后进程退出
  3. REPL 只能处理一条命令
  4. 所谓的"REPL"其实是一次性的
  5. 用户认为 zmai 是"执行一次就退出"的工具

修复:
  移除 _cmd_run 中的 sys.exit()。
  将返回值返回给调用者，而非直接退出进程。
```

---

## 2. 设计原则

### 2.1 默认 REPL

```
$ zmai         → 进入 REPL（没有任何标志）
$ zmai 重构    → 单次执行（位置参数 = 单次任务）
$ zmai --help  → 显示帮助（非交互）

原则：无参数 = REPL。有位置参数 = 单次执行。
```

### 2.2 持续运行

```
REPL 不退出，除非用户明确要求。

┌─────────────────────────────────────┐
│ zmai> 读取 README                   │
│ 🤖 正在读取...                       │
│ ✅ README 已读取                     │
│                                     │
│ zmai> 总结一下刚读的内容             │
│ 🤖 项目是一个 Agent Runtime...       │
│                                     │
│ zmai> 帮我画个架构图                 │
│ 🤖 正在生成...                       │
│                                     │
│ zmai> exit                          │
│ Bye 👋                              │
└─────────────────────────────────────┘
```

**永远不自动退出。** 用户输入 exit/quit/Ctrl+D 才退出。

### 2.3 上下文持续

```
REPL 内的所有任务共享同一个 Runtime 实例。

第一次: zmai> 读取 README
         → runtime.run(agent_1, ...)

第二次: zmai> 总结刚才的内容
         → runtime.run(agent_1, ...)  ← 同一个 agent_id
         → Memory 持续可用

第三次: zmai> 架构是怎么设计的
         → runtime.run(agent_1, ...)  ← 对话上下文延续
```

### 2.4 不修改下游

```
仅修改:  src/zmai/cli/main.py     ← 重写 _cmd_interactive / _cmd_run
         src/zmai/cli/repl.py     ← 新增

不修改:  src/zmai/runtime/*       ✗
         src/zmai/agent/*         ✗
         src/zmai/gateway/*       ✗
         src/zmai/memory/*        ✗
         src/zmai/workspace/*     ✗
         src/zmai/workflow/*      ✗
         src/zmai/swe/*           ✗
         src/zmai/auth/*          ✗
```

---

## 3. 用户流程

### 3.1 进入 REPL

```
$ zmai

  ⚡ zmai v0.1.0  my-project (python 3.13)  DeepSeek
  ─────────────────────────────────────────────────────
  输入自然语言描述任务，或使用 /help 查看内建命令。

  zmai>
```

### 3.2 REPL 内流程

```
zmai> 帮我看看这个 README
      │
      ├── Runtime.run(agent, task)
      ├── 显示进度
      │     🔍 read_file("README.md")
      │     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
      ├── 显示结果
      │     ✅ README 内容已读取
      │
zmai> 总结一下
      │
      ├── Runtime.run(agent, task)  ← 同上 agent
      ├── Agent 已有前文记忆
      │
zmai> exit
      Bye 👋
```

### 3.3 退出 REPL

| 方式 | 输入 | 行为 |
|------|------|------|
| 输入 exit | `zmai> exit` | 保存 Session → 退出 |
| 输入 quit | `zmai> quit` | 同上 |
| Ctrl+D | — | 同上 |
| Ctrl+C（空闲） | — | 不退出，换新行 |
| Ctrl+C（任务中） | — | 暂停当前任务 |
| Ctrl+C 两次 | — | 确认退出 |

---

## 4. 架构设计

### 4.1 整体架构

```
main()
  │
  ├── 参数解析 → 有位置参数? → _cmd_run(一次) → exit
  │
  └── 无参数 → REPL
        │
        ┌── REPL
        │   ├── InputHandler    ← 输入处理（readline/history/补全）
        │   ├── OutputHandler   ← 输出渲染（进度/结果/错误）
        │   ├── ContextManager  ← 上下文管理（Runtime/Memory 生命周期）
        │   └── CommandRegistry ← 内建命令注册
        │
        └── 循环:
              input → 内建命令？→ 执行 → 继续
                    → 自然语言？→ run() → 继续
                    → exit?      → 清理 → 退出
```

### 4.2 组件关系

```
REPL
  │
  ├── InputHandler
  │   ├── readline 历史（持久化 ~/.zmai/history）
  │   ├── Tab 补全（命令名 / 子命令）
  │   ├── 多行输入（检测到空行提交）
  │   └── Ctrl+C/D 信号处理
  │
  ├── OutputHandler
  │   ├── Banner（启动信息）
  │   ├── 进度条（任务执行中）
  │   ├── 结果展示（任务完成）
  │   └── Prompt（zmai> ）
  │
  ├── ContextManager
  │   ├── Runtime 实例（延迟创建，跨任务复用）
  │   ├── agent_id（同一 REPL 共享）
  │   ├── Memory 上下文（对话历史保持）
  │   └── Session 快照（退出时保存）
  │
  └── CommandRegistry
      ├── /help     — 显示帮助
      ├── /status   — 显示状态
      ├── /memory   — 查看记忆
      ├── /resume   — 恢复任务
      ├── /cancel   — 取消任务
      ├── /clear    — 清屏
      └── exit/quit — 退出 REPL
```

### 4.3 数据流

```
用户输入
    │
    ▼
InputHandler.readline() → 原始输入
    │
    ├── 空行 → 忽略，继续
    │
    ├── 以 "/" 开头 → CommandRegistry.execute(cmd)
    │   ├── /help /status /memory → 立即输出 → 继续循环
    │   ├── /cancel → Runtime.cancel(agent_id) → 继续循环
    │   └── /resume → Runtime.resume(agent_id) → 继续任务
    │
    ├── "exit" / "quit" → 保存 Session → 退出循环
    │
    └── 自然语言 → ContextManager.run(task)
        │
        ├── Runtime.run(agent_id, task, ...)
        ├── OutputHandler.show_progress(...)
        ├── OutputHandler.show_result(result)
        └── ContextManager.update_context(result)
            │
            ▼
        继续循环
```

---

## 5. 输入层

### 5.1 Readline 历史支持

使用 Python 标准库 `readline`，不引入第三方依赖。

```python
# cli/repl/input.py

import atexit
import os
import sys
from pathlib import Path

HISTORY_FILE = Path.home() / ".zmai" / "history"
HISTORY_MAX_LENGTH = 2000


class InputHandler:
    """REPL 输入处理。带 Readline 历史、Tab 补全、多行输入。"""

    def __init__(self, completions: list[str] | None = None):
        self._completions = completions or []
        self._setup_readline()

    def _setup_readline(self) -> None:
        """配置 readline。"""
        try:
            import readline
            # 历史文件
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            if HISTORY_FILE.exists():
                readline.read_history_file(str(HISTORY_FILE))
            readline.set_history_length(HISTORY_MAX_LENGTH)
            atexit.register(readline.write_history_file, str(HISTORY_FILE))

            # Tab 补全
            if self._completions:
                readline.set_completer(self._complete)
                readline.parse_and_bind("tab: complete")
                # 不区分大小写
                readline.set_completion_display_matches_hooks(
                    self._display_matches)

        except ImportError:
            pass  # Windows 或受限环境，降级到 input()

    def _complete(self, text: str, state: int) -> str | None:
        """Tab 补全回调。"""
        matches = [c for c in self._completions if c.startswith(text)]
        try:
            return matches[state] + " "
        except IndexError:
            return None

    @staticmethod
    def _display_matches(substitution: str, matches: list[str],
                         longest_match_length: int) -> None:
        """显示匹配项。"""
        print()
        for m in matches:
            print(f"  {m}")

    def read(self, prompt: str = "zmai> ") -> str:
        """读取一行输入。自动判断是否需要多行模式。"""
        try:
            line = input(prompt).strip()
        except EOFError:
            return "exit"
        except KeyboardInterrupt:
            return ""  # Ctrl+C → 换行，不退出

        # 多行输入检测：如果以 "  " 或 ``` 开头，进入多行模式
        if line.startswith("```") or line.endswith("\\"):
            return self._read_multiline(line)

        return line

    def _read_multiline(self, first_line: str) -> str:
        """多行输入模式。连续读取直到空行提交。"""
        # Windows 降级：不支持多行时直接返回
        if sys.platform == "win32" and "readline" not in sys.modules:
            return first_line.rstrip("\\")

        lines = [first_line.rstrip("\\")]
        try:
            while True:
                line = input("... ").strip()
                if not line:
                    break
                lines.append(line.rstrip("\\"))
        except (EOFError, KeyboardInterrupt):
            pass
        return "\n".join(lines)
```

### 5.2 历史持久化

```
~/.zmai/history        ← 纯文本，一行一条命令

格式:
  read README
  帮我重构 auth 模块
  /status
  /help

特点:
  - 最多 2000 条
  - 自动去重（连续重复只保存一次）
  - 跨会话持久（上次 REPL 的历史下次可用）
  - ↑↓ 键翻阅
  - Ctrl+R 搜索（如果 readline 支持）
```

### 5.3 Tab 补全

```python
# 补全词列表，动态生成
REPL_COMPLETIONS = [
    # 内建命令
    "/help", "/status", "/memory", "/resume",
    "/cancel", "/clear", "/exit",
    # 常用动词（提示而非强制）
    "read ", "write ", "edit ", "search ",
    "refactor ", "analyze ", "test ", "build ",
    # 项目文件名（如果检测到项目）
    "README.md", "setup.py", "package.json",
]
```

### 5.4 Windows 兼容

Windows 不支持 `readline` 标准库。降级策略：

```python
def _setup_readline(self):
    try:
        import readline
        # ... 正常配置
    except ImportError:
        try:
            # Windows: 尝试 pyreadline3（可选）
            import pyreadline3 as readline
            # ... 同样配置
        except ImportError:
            # 降级：标准 input()，无历史
            pass
```

**不引入硬依赖。** `pyreadline3` 是可选依赖。

### 5.5 输入提示

```python
def _format_prompt(agent_id: str, is_running: bool) -> str:
    """格式化 Prompt。"""
    status = "⚡" if is_running else ""
    return f"{status} zmai> "
```

显示：

```
zmai>                       # 空闲状态
⚡ zmai>                    # 任务运行中（不可输入，但显示状态）
```

---

## 6. 输出层

### 6.1 Banner

```python
# cli/repl/banner.py

class Banner:
    """REPL 启动时显示的 Banner。"""

    def show(self, project_ctx, backend_name, version):
        theme = Theme.dark()
        lines = [
            f"  {theme.highlight('⚡ zmai')} {theme.dim(version)}  "
            f"{project_ctx.summary() if project_ctx else ''}  "
            f"{theme.dim(backend_name)}",
            f"  {theme.dim('─' * 50)}",
            f"  {theme.dim('输入自然语言描述任务 · /help 查看命令 · exit 退出')}",
            "",
        ]
        sys.stderr.write("\n".join(lines))
```

显示效果：

```
  ⚡ zmai v0.1.0  my-project (python 3.13)  DeepSeek
  ─────────────────────────────────────────────────────
  输入自然语言描述任务 · /help 查看命令 · exit 退出

  zmai>
```

### 6.2 执行中输出

```python
class ProgressRenderer:
    """任务进度的实时渲染。"""

    def __init__(self):
        self._spinner_idx = 0
        self._spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._last_output = ""

    def on_tool(self, tool_name: str, detail: str = "") -> None:
        """工具调用时显示。"""
        spinner = self._spinner_chars[self._spinner_idx % len(self._spinner_chars)]
        self._spinner_idx += 1
        sys.stderr.write(f"\r  {spinner} {tool_name}  {detail}\033[K")

    def on_result(self, status: str, summary: str) -> None:
        """工具完成时显示。"""
        icon = "✅" if status == "OK" else "❌"
        sys.stderr.write(f"\r  {icon} {summary}\033[K\n")
```

显示效果：

```
  ⠋ read_file("README.md")  正在读取...
  ✅ read_file("README.md")  完成 (0.2s)
  ⠋ grep("bottleneck", "src/")  搜索中...
  ✅ grep("bottleneck", "src/")  找到 3 处 (0.3s)
```

### 6.3 结果展示

```python
def show_result(result: dict[str, Any], theme: Theme) -> None:
    """展示任务执行结果。"""
    status = result.get("status", "?")
    output = result.get("output", "")
    steps = result.get("steps", 0)
    error = result.get("error", "")

    if status == "completed":
        # 成功完成
        if output:
            # 截取关键内容
            lines = output.strip().splitlines()
            preview = "\n".join(lines[:10])
            if len(lines) > 10:
                preview += f"\n  {theme.dim(f'... 还有 {len(lines)-10} 行')}"
            sys.stderr.write(f"\n{preview}\n")
        sys.stderr.write(f"  {theme.success('✅ 完成')}  {theme.dim(f'({steps} 步)')}\n")
    elif status == "cancelled":
        sys.stderr.write(f"\n  {theme.warning('⏸ 已暂停')}\n")
    elif status == "failed":
        sys.stderr.write(f"\n  {theme.error(f'❌ {error}')}\n")
```

### 6.4 错误展示

```python
def show_error(err: Exception, theme: Theme) -> None:
    """展示错误信息。"""
    msg = str(err)

    # 常见错误格式化
    if "401" in msg or "Unauthorized" in msg:
        sys.stderr.write(f"\n  {theme.error('❌ API Key 无效')}\n")
        sys.stderr.write(f"  {theme.dim('请运行 zmai auth update 更新 Key')}\n")
    elif "429" in msg or "Rate limited" in msg:
        sys.stderr.write(f"\n  {theme.warning('⚠ API 速率限制')}\n")
        sys.stderr.write(f"  {theme.dim('等待 30 秒后重试')}\n")
    else:
        sys.stderr.write(f"\n  {theme.error(f'❌ {msg}')}\n")
```

---

## 7. 上下文管理层

### 7.1 核心：修复 sys.exit() 问题

**当前 bug：** `_cmd_run()` 在 `main.py:244` 调用 `sys.exit()`。

**修复：** 分离"REPL 内执行"和"单次执行"两条路径。

```python
# REPL 路径：不 exit
async def _repl_run(runtime, agent_id, task, config, on_progress):
    result = await runtime.run(
        agent_id=agent_id,
        task=task,
        backend=None,
        on_progress=on_progress,
        config=config,
    )
    # 不 exit，返回 result
    return result

# 单次执行路径：exit（仅 CLI 模式）
def _oneshot_run(runtime, task, config, args):
    result = asyncio.run(runtime.run(...))
    # 仅 CLI 模式 exit
    sys.exit(0 if result.get("status") == "completed" else 3)
```

### 7.2 Runtime 生命周期

```python
class ContextManager:
    """REPL 上下文管理器。管理 Runtime 生命周期。"""

    def __init__(self, config: Config):
        self._config = config
        self._runtime: Runtime | None = None
        self._agent_id: str = ""
        self._session_id: str = ""
        self._task_count: int = 0

    def get_runtime(self) -> Runtime:
        """延迟创建 Runtime，跨任务复用。"""
        if self._runtime is None:
            self._runtime = Runtime(config=self._config)
            self._agent_id = f"agent_{os.getpid()}"
        return self._runtime

    def next_agent_id(self) -> str:
        """同一 REPL 使用同一个 agent_id（累积上下文）。"""
        return self._agent_id

    def run(self, task: str, on_progress) -> dict[str, Any]:
        """执行任务，同一 REPL 内共享上下文。"""
        runtime = self.get_runtime()
        agent_id = self.next_agent_id()
        self._task_count += 1

        result = asyncio.run(runtime.run(
            agent_id=agent_id,
            task=task,
            backend=None,
            on_progress=on_progress,
            config={},
        ))
        return result

    async def shutdown(self) -> None:
        """退出时清理。"""
        if self._runtime:
            await self._runtime.shutdown()
```

### 7.3 共享 Runtime

```
REPL 启动:
  config = Config()
  ctx = ContextManager(config)
  # 不创建 Runtime（延迟到第一次任务）

第一次任务:
  zmai> 读取 README
  ctx.run("读取 README", ...)
    → Runtime.__init__()     ← 第一次调用时创建
    → Runtime.run(agent_main, "读取 README")
    → Runtime 内部保持 agent_main 的状态

第二次任务:
  zmai> 总结
  ctx.run("总结", ...)
    → Runtime.run(agent_main, "总结")  ← 同一个 agent_id
    → Agent 有之前的对话上下文

第 N 次任务:
  zmai> 重构 auth
  ctx.run("重构 auth", ...)
    → 同上
    → Runtime.run() 在同一个 agent 实例上持续累积
```

### 7.4 任务间状态维持

```python
@dataclass
class REPLState:
    """REPL 会话状态。"""
    task_count: int = 0
    current_agent_id: str = ""
    last_task: str = ""
    last_status: str = ""
    is_running: bool = False
    start_time: str = ""
```

状态显示：

```
zmai> /status

  当前会话
  ─────────────────────────────────────
  Runtime:  运行中 (3 个任务)
  Agent:    agent_main
  上次任务: "重构 auth 模块" ✅ 完成
  Memory:   5 个条目 · 1.2k tokens
```

---

## 8. 内建命令

### 8.1 命令表

| 命令 | 别名 | 作用 | 示例 |
|------|------|------|------|
| `/help` | `/?` | 显示内建命令列表 | `/help` |
| `/status` |—| 显示会话状态 | `/status` |
| `/memory` | `/mem` | 显示当前 Memory 摘要 | `/memory` |
| `/resume` |—| 恢复上次暂停的任务 | `/resume` |
| `/cancel` |—| 取消当前运行的任务 | `/cancel` |
| `/clear` |—| 清屏 | `/clear` |
| `/exit` | `/quit` | 退出 REPL | `/exit` |

### 8.2 命令注册

```python
class CommandRegistry:
    """内建命令注册与分发。"""

    def __init__(self, ctx: ContextManager):
        self._ctx = ctx
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def execute(self, line: str) -> bool:
        """执行内建命令。返回 True 表示已处理。"""
        if not line.startswith("/"):
            return False  # 不是命令

        parts = line[1:].split()
        name = parts[0]
        args = parts[1:]

        cmd = self._commands.get(name)
        if not cmd:
            print(f"未知命令: /{name}  输入 /help 查看可用命令")
            return True

        try:
            cmd.handler(args)
        except Exception as e:
            print(f"命令执行失败: {e}")
        return True


# 内建命令定义
class Command:
    def __init__(self, name, aliases, description, handler):
        self.name = name
        self.aliases = aliases
        self.description = description
        self.handler = handler
```

### 8.3 /help

```
zmai> /help

  内建命令:
    /help           显示本帮助
    /status         显示当前会话状态
    /memory         查看当前 Memory 摘要
    /resume         恢复上次暂停的任务
    /cancel         取消当前运行的任务
    /clear          清屏
    /exit           退出 REPL

  快捷键:
    ↑↓              翻阅历史
    Tab             命令/文件补全
    Ctrl+C          暂停当前任务
    Ctrl+D          退出 REPL

  提示:
    直接输入自然语言描述任务，Agent 会理解并执行。
```

### 8.4 /status

```
zmai> /status

  ⚡ zmai v0.1.0
  ──────────────────────────────────────
  项目      my-project (python 3.13)
  Backend   DeepSeek (deepseek-chat)
  会话      task#3 · agent_main · 运行中
  上次任务  "重构 auth 模块" ✅ (5 步)
  Memory    3 条目 · 0.8k tokens
  Workspace ./workspace/ · 1 活跃 Agent
```

### 8.5 /memory

```
zmai> /memory

  Memory 摘要
  ──────────────────────────────────────
  Working:  2 条目
    task.current_focus → "修复 token 刷新"
    last.result → "测试通过 42/45"

  Long-term: 3 条目
    project.type → "python 3.13"
    auth.module → "src/auth/login.py"
    decision → "使用 JWT 而非 session"
```

### 8.6 /resume 和 /cancel

```
zmai> /resume
  ⟳ 正在恢复上次暂停的任务...
  ⟳ 加载任务状态...
  ✅ 已恢复: "重构 auth 模块" (步骤 5/8)

zmai> /cancel
  ✅ 当前任务已取消
```

---

## 9. 快捷键

### 9.1 键位表

| 按键 | 空闲时 | 任务执行中 |
|------|--------|-----------|
| **Enter** | 提交当前输入 | —（不可输入） |
| **↑** | 上一条历史 | — |
| **↓** | 下一条历史 | — |
| **Tab** | 命令/路径补全 | — |
| **Ctrl+C** | 忽略（保持 REPL） | **暂停当前任务** |
| **Ctrl+C 两次** | **确认退出** | **强制退出** |
| **Ctrl+D** | **退出 REPL** | — |
| **Ctrl+L** | 清屏 | — |
| **Ctrl+R** | 反向搜索历史 | — |

### 9.2 Ctrl+C 处理

```python
class SignalHandler:
    """信号处理。多层 Ctrl+C 策略。"""

    def __init__(self, ctx: ContextManager):
        self._ctx = ctx
        self._interrupt_count = 0
        self._last_interrupt = 0.0

    def handle_sigint(self, signum, frame):
        """Ctrl+C 处理器。"""
        now = time.time()

        # 5 秒内连续两次 Ctrl+C → 强制退出
        if now - self._last_interrupt < 5.0:
            self._interrupt_count += 1
        else:
            self._interrupt_count = 1
        self._last_interrupt = now

        if self._interrupt_count >= 2:
            # 二次 Ctrl+C → 强制退出
            print("\n  ⚠ 强制退出")
            self._save_and_exit()
            return

        if self._ctx.is_running:
            # 任务执行中 → 暂停
            print("\n  ⏸ 任务已暂停")
            print("  输入 /resume 继续，/cancel 取消，再按 Ctrl+C 强制退出")
            self._ctx.cancel_current_task()
        else:
            # 空闲状态 → 换行，不退出
            print()
```

### 9.3 Ctrl+D 处理

```python
# InputHandler.read 中处理
try:
    line = input(prompt).strip()
except EOFError:  # Ctrl+D
    return "exit"
```

---

## 10. 多轮对话

### 10.1 对话上下文

```python
class ConversationManager:
    """多轮对话上下文管理。"""

    MAX_HISTORY = 50  # 最大保留消息数

    def __init__(self):
        self._messages: list[dict[str, str]] = []

    def add_user_message(self, text: str) -> None:
        """记录用户消息。"""
        self._messages.append({"role": "user", "content": text})

    def add_assistant_message(self, text: str) -> None:
        """记录 Agent 响应摘要。"""
        truncated = text[:500] if len(text) > 500 else text
        self._messages.append({"role": "assistant", "content": truncated})

        # 裁剪旧消息
        if len(self._messages) > self.MAX_HISTORY:
            self._messages = self._messages[-self.MAX_HISTORY:]

    def build_context(self) -> str:
        """构建多轮对话上下文。"""
        if not self._messages:
            return ""

        recent = self._messages[-6:]  # 最近 3 轮
        lines = ["\n=== 对话上下文 (自动恢复) ==="]
        for msg in recent:
            role = "用户" if msg["role"] == "user" else "你"
            content = msg["content"][:200]
            lines.append(f"  {role}: {content}")
        return "\n".join(lines)
```

### 10.2 上下文注入

```python
def run_with_context(self, task: str) -> dict:
    """带对话上下文的任务执行。"""
    # 1. 记录用户消息
    self._conv.add_user_message(task)

    # 2. 构建上下文 Prompt
    context_prompt = self._conv.build_context()
    full_task = f"{context_prompt}\n\n当前任务: {task}" if context_prompt else task

    # 3. 执行
    result = self._ctx.run(full_task)

    # 4. 记录 Agent 响应
    output = result.get("output", "")
    self._conv.add_assistant_message(output)

    return result
```

### 10.3 对话示例

```
zmai> 这个项目用什么语言写的？
  🤖 项目是 Python 3.13，使用 setuptools 构建...

zmai> 测试框架是什么？
  🤖 （Agent 知道上文在讨论项目工具链）
  使用 pytest，配置文件在 pyproject.toml [tool.pytest]

zmai> 帮我跑一下测试
  🤖 （Agent 知道测试框架是 pytest，直接执行）
  shell_exec("pytest tests/")
  ✅ 测试通过 (42 passed)
```

---

## 11. 与现有 CLI 的关系

### 11.1 命令路由

```python
# main.py (优化后)

def main(argv=None):
    argv = argv or sys.argv[1:]

    # 子命令（直接路由，不进入 REPL）
    if argv and argv[0] in ("config", "auth", "doctor", "init"):
        _run_subcommand(argv[0], argv[1:])
        return

    # 单次执行（有参数）
    if argv and not argv[0].startswith("-"):
        _run_oneshot(" ".join(argv))
        return

    # 标志（--version / --help / --json 等）
    if argv:
        args = _parse_flags(argv)
        if args.task:
            _run_oneshot(args.task)
            return
        if args.version or args.help:
            _print_info(args)
            return

    # 默认：进入 REPL
    _run_repl()
```

### 11.2 文件路由

```
main() 入口
    │
    ├── argv[0] = "config"    → _run_config()      ← 不进入 REPL
    ├── argv[0] = "auth"      → _run_auth()         ← 不进入 REPL
    ├── argv[0] = "doctor"    → _run_doctor()       ← 不进入 REPL
    ├── argv[0] = "init"      → _run_init()         ← 不进入 REPL
    ├── argv = ["重构 auth"]  → _run_oneshot()      ← 执行一次退出
    ├── argv = ["--version"]  → print_version()     ← 显示退出
    ├── argv = []             → _run_repl()          ← 进入 REPL
    └── stdin 有管道输入      → _run_pipe()          ← 执行一次退出
```

### 11.3 向后兼容

| 旧行为 | 新行为 | 兼容性 |
|--------|--------|--------|
| `zmai -p "task"` | `zmai task` | ✅ 仍支持 `-p` |
| `zmai -i` | `zmai`（默认 REPL） | ✅ `-i` 忽略 |
| `zmai -r` | `zmai` + `/resume` | ✅ `-r` 仍支持 |
| `zmai --json` | `zmai --json`（单次） | ✅ 不变 |
| `zmai config list` | `zmai config list` | ✅ 不变 |
| `zmai auth` | `zmai auth` | ✅ 不变 |

### 11.4 不变文件

```
src/zmai/runtime/*         ✅ Runtime.run() 接口不变
src/zmai/agent/*           ✅ Agent 接口不变
src/zmai/gateway/*         ✅ 不变
src/zmai/workspace/*       ✅ 不变
src/zmai/memory/*          ✅ 不变
src/zmai/workflow/*        ✅ 不变
src/zmai/swe/*             ✅ 不变
src/zmai/config/*          ✅ 不变
src/zmai/auth/*            ✅ 不变
```

---

## 12. 文件清单与实现计划

### 12.1 新增文件

```
src/zmai/cli/repl/
├── __init__.py             # 导出 REPL
├── repl.py                 # REPL 主循环
├── input.py                # 输入处理（readline/历史/补全）
├── output.py               # 输出渲染（banner/进度/结果）
├── commands.py             # 内建命令注册
├── context.py              # 上下文管理（Runtime 生命周期）
├── conversation.py         # 多轮对话上下文
└── signals.py              # 信号处理（Ctrl+C/D）
```

### 12.2 修改文件

```
src/zmai/cli/main.py        # 🔧 重写 — 分离 REPL/单次/子命令路径
                              # 修复 sys.exit() bug
```

### 12.3 不变文件

```
src/zmai/runtime/runtime.py    ✅ 不变
src/zmai/cli/formatters.py     ✅ 不变
src/zmai/cli/detector.py       ✅ 不变
src/zmai/cli/context.py        ✅ 不变
src/zmai/cli/detectors/*       ✅ 不变
```

### 12.4 代码量变化

```
v1.0:
  main.py REPL 相关:     ~20 行 (_cmd_interactive)

v2.0:
  cli/repl/__init__.py      ~10 行
  cli/repl/repl.py          ~120 行  ← 主循环
  cli/repl/input.py         ~100 行  ← readline/历史/补全
  cli/repl/output.py        ~100 行  ← banner/进度/结果
  cli/repl/commands.py      ~100 行  ← 内建命令
  cli/repl/context.py       ~80 行   ← Runtime 生命周期
  cli/repl/conversation.py  ~60 行   ← 多轮上下文
  cli/repl/signals.py       ~60 行   ← 信号处理
  cli/main.py (修改)        ~-50 行  ← 精简
  新增总计                  ~630 行
```

### 12.5 关键修复点

```python
# main.py 中的关键修复

# 1. 移除 _cmd_run 中的 sys.exit()
def _cmd_run(task, runtime, config, args):
    ...
    result = asyncio.run(runtime.run(...))
    # 删掉: sys.exit(0 if ... else 3)  ← 这一行
    return result  # ← 改为返回结果

# 2. 分离单次执行（使用 sys.exit）和 REPL 执行（不使用 sys.exit）
def _run_oneshot(task):
    """CLI 模式：执行一次并退出。"""
    runtime, config = _init_runtime()
    result = _cmd_run(task, runtime, config, {})
    sys.exit(0 if result.get("status") == "completed" else 3)

def _run_repl():
    """REPL 模式：持续交互，永不 exit。"""
    config = Config()
    repl = REPL(config)
    repl.run()  # 不会调用 sys.exit()

# 3. 保留旧的 _cmd_run 签名但改返回值
# 4. 确保 Oneshot 路径不变，REPL 路径是新代码
```

### 12.6 实现优先级

```
P0 — 修复 + 基础 REPL（1 天）
├── 修复 sys.exit() bug          ← 最高优先级
├── cli/repl/repl.py             ← REPL 主循环
├── cli/repl/context.py          ← Runtime 生命周期
├── cli/main.py 重写              ← 分离路径
└── exit/quit/Ctrl+D/Ctrl+C       ← 退出处理

P1 — 输入体验（1 天）
├── cli/repl/input.py             ← readline 历史 + 持久化
├── cli/repl/commands.py          ← 内建命令 (/help /status 等)
├── cli/repl/output.py            ← Banner + 进度渲染
└── cli/repl/signals.py           ← Ctrl+C 多层处理

P2 — 上下文（0.5 天）
├── cli/repl/conversation.py      ← 多轮对话上下文
├── Tab 补全                      ← 命令/文件名补全
└── 多行输入                      ← 粘贴代码块
```

---

> **总结：**
>
> ZMAI REPL v2.0 从 17 行的 `input()` 循环进化为完整的交互式会话系统：
>
> 1. **修复致命 bug** — 移除 `_cmd_run` 中的 `sys.exit()`，REPL 不再执行一次就退出
> 2. **默认 REPL** — `zmai` 无参数直接进入交互模式，有参数才单次执行
> 3. **Readline 历史** — 2000 条持久化历史，↑↓ 翻阅，Ctrl+R 搜索
> 4. **Tab 补全** — 命令名 + 文件路径补全
> 5. **多轮上下文** — 同一 Runtime/Agent 跨任务共享，对话历史持续累积
> 6. **多层 Ctrl+C** — 空闲忽略、任务中暂停、两次强制退出
> 7. **内建命令** — `/help` `/status` `/memory` `/resume` `/cancel` `/clear`
> 8. **Banner + 进度渲染** — 结构化的输出展示
>
> **没参数 → REPL。有任务 → 执行。要退出 → exit。不自动退出。**
