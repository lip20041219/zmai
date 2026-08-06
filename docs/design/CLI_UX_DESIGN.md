# ZMAI CLI UX Design v2.0

Version: 2.0
Date: 2026-07-16

> **本文档仅优化 CLI 交互层的开发者体验（Developer Experience）。**
>
> **不修改任何 Runtime / Agent / Gateway / Memory / Workflow / Backend 模块。**
>
> **目标：让 `zmai` 成为进入开发状态的最短路径。**

---

## 目录

1. [现状审查](#1-现状审查)
2. [设计原则](#2-设计原则)
3. [启动体验优化](#3-启动体验优化)
4. [命令精简](#4-命令精简)
5. [自动恢复体系](#5-自动恢复体系)
6. [REPL 交互优化](#6-repl-交互优化)
7. [终端输出优化](#7-终端输出优化)
8. [安全与退出](#8-安全与退出)
9. [Shell 集成](#9-shell-集成)
10. [实现计划](#10-实现计划)
11. [与现有代码的关系](#11-与现有代码的关系)

---

## 1. 现状审查

### 1.1 现有实现已覆盖

| 能力 | 文件 | 状态 |
|------|------|------|
| 项目自动检测 | `cli/detector.py` + `cli/detectors/*` | ✅ 已完成 |
| 上下文构建 | `cli/context.py` | ✅ 已完成 |
| 主题/着色 | `cli/formatters.py` | ✅ 已完成 |
| Session 保存/恢复 | `main.py:_save_session/_load_latest_session` | ✅ 基础版 |
| 交互式输入 | `main.py:_cmd_interactive` | ✅ 基础版 |
| Backend 自动选择 | `runtime.py:_auto_select_default_backend` | ✅ 已完成 |
| 初始化向导 | `main.py:_run_init_wizard` | ✅ 已完成 |

### 1.2 现有实现未覆盖

| 能力 | 缺失情况 | 影响 |
|------|----------|------|
| 命令历史 | `input()` 无 readline 支持 | 无法上下翻历史、无法搜索 |
| 命令补全 | 无 Shell Completion 脚本 | 每次需记忆参数 |
| Workspace 自动恢复 | CLI 启动未检查 workspace 已有状态 | 每次重新创建，无法续跑 |
| Memory 自动恢复 | `MemoryManager.restore()` 未从 CLI 触发 | Agent 启动时上下文为空 |
| 安全退出 | `KeyboardInterrupt` 仅捕获，未暂停任务 | 任务丢失，无法恢复 |
| 进度可视化 | 仅文本回调，无进度条/动画 | 长任务无视觉反馈 |
| 状态总览 | `zmai status` 未实现 | 用户无法查看当前状态 |

### 1.3 根因

所有缺失能力都有一个共同根因：**CLI 层仅做了参数解析和简单转发，没有利用已有的 Runtime / Workspace / Memory 基础设施**。

`main.py` 的 `_cmd_run` 方法只做了：
```
args → Runtime.run()
```

没有做的：
```
args → 加载 Workspace → 恢复 Memory → 构建上下文 → 恢复 Session → Runtime.run()
```

修复方式：**在 CLI 层串联已有模块的恢复能力**，不修改任何下游模块。

---

## 2. 设计原则

### 2.1 一条命令原则

```
$ zmai                    # REPL 交互模式（智能恢复一切）
$ zmai <自然语言任务>       # 单次执行（智能检测一切）
```

**没有任何非必要的参数。** 优先级从高到低：
1. 自然语言任务描述（最常见）
2. 无参数 = REPL 交互模式（第二常见）
3. 管理子命令 `config` / `auth` / `status`（极少使用）

### 2.2 自动恢复原则

启动时，CLI 自动串联已有能力：

```
zmai  startup
   ├── 检测项目 (detector.py)
   ├── 构建上下文 (context.py)
   ├── 恢复 Session (latest.json)
   ├── 恢复 Workspace (workspace/state.json)
   ├── 恢复 Memory (MemoryManager.restore)
   └── 选择 Backend (auto)
```

用户**不需要**为恢复状态输入任何参数。

### 2.3 最小惊讶原则

- 第一次运行 → 配置向导
- 第二次运行 → 记住上次选择
- `zmai` 在项目目录 → 项目模式
- `zmai` 在非项目目录 → 聊天模式
- `zmai 帮我重构` → 任务模式
- `zmai status` → 信息查询

### 2.4 不修改下游原则

所有优化仅限于 `src/zmai/cli/` 目录。

```
src/zmai/cli/  ← 唯一修改范围
src/zmai/runtime/  ✗
src/zmai/agent/    ✗
src/zmai/gateway/  ✗
src/zmai/memory/   ✗
src/zmai/workspace/✗
src/zmai/workflow/ ✗
src/zmai/swe/      ✗
```

---

## 3. 启动体验优化

### 3.1 启动 Banner

当前（main.py:386-390）:
```python
sys.stderr.write(f"\033[2mzmai  {project_ctx.summary()}  [{runtime._gateway.default_name or ''}]\033[0m\n")
```

优化为：

```
$ cd my-project
$ zmai

  ⚡ zmai v0.1.0
  ───────────────────────────────────────────
  项目    my-project (python 3.13)
  上下文  4 个检测器已构建
  Workspace   agent_1 (活跃) · agent_2 (已完成)
  记忆    上次对话可用 · 3 个条目
  Backend    DeepSeek (deepseek-chat)
  ───────────────────────────────────────────
  zmai>
```

**实现方式**：新增 `cli/banner.py`，调用已有的 `detector.py`、`context.py`、Workspace、MemoryManager 获取摘要信息。

### 3.2 首次使用向导改进

当前（main.py:66-136）:
- 纯文本交互，无着色引导
- 配置完成后无直接进入操作

优化：
- 使用 formatter 的 Theme 系统（已存在）
- 向导完成后直接进入 REPL 而非显示帮助
- 添加"跳过并稍后配置"选项

**实现方式**：仅修改 `main.py` 中的 `_run_init_wizard`，复用已有的 `formatters.py`。

### 3.3 非项目目录行为

当前：`detect()` 返回 `mode="chat"`，但提示信息不清晰。

优化：
- `zmai` 在非项目目录 → 显示 "💬 聊天模式（未检测到项目）"
- 仍可输入任务，但建议 `cd` 到项目目录
- 不阻断使用

**实现方式**：修改 `main.py` 的非项目分支，增强提示。

---

## 4. 命令精简

### 4.1 当前参数全景

`main.py:167-182` 当前参数：

| 参数 | 必要性 | 建议 |
|------|--------|------|
| `--version` | 低 | 保留 |
| `--json` | 低 | 保留（管道友好） |
| `--no-color` | 低 | 保留 |
| `-p / --prompt` | **中** | 合并到位置参数 |
| `task nargs*` | 高 | 保留（核心） |
| `-i / --interactive` | **低** | 自动检测，删除参数 |
| `-r / --resume` | **中** | 合并为自动恢复 |
| `-c / --confirm` | **低** | 移入 config |

### 4.2 优化后参数表

```bash
# ── 核心（零参数即可） ────
zmai                          进入 REPL 交互模式（自动恢复一切）
zmai <自然语言任务>            直接执行任务（自动检测一切）

# ── 选项（极少需要） ──────
zmai --backend <name>         临时切换 Backend
zmai --model <name>           临时切换模型
zmai --json                   JSON 输出（管道专用）
zmai --no-color               禁用着色

# ── 管理（config 子命令） ──
zmai config                   查看/修改配置

# ── 版本/帮助 ────────────
zmai --version                版本信息
zmai --help                   帮助
```

**删除的参数：**
| 删除 | 替代方案 |
|------|---------|
| `-p / --prompt` | 位置参数直接作为任务 |
| `-i / --interactive` | 自动检测：有 tty + 无参数 = 交互模式 |
| `-r / --resume` | 自动恢复：启动时自动检测未完成任务 |
| `-c / --confirm` | 移入 `zmai config set cli.confirm true` |
| `task nargs*` 与 `-p` 并存 | 统一为单一位置参数 |

**实现方式**：重写 `_build_parser()` 为简洁版本。

### 4.3 参数数量对比

```
当前：  10 个参数 / 标志
优化后： 5 个参数 / 标志（含 --help 和 --version）
精简：  50%
```

---

## 5. 自动恢复体系

这是本次优化的**核心**。CLI 层需要串联已有的恢复能力。

### 5.1 恢复流程（启动时自动执行）

```
zmai startup
   │
   ├─ 1. detect_project()      ← cli/detector.py（已有）
   │    └─ 返回 ProjectInfo {root, type, name, workspace_root}
   │
   ├─ 2. build_context()       ← cli/context.py（已有）
   │    └─ 返回 ProjectContext {type, git, test_framework, ...}
   │
   ├─ 3. restore_session()     ← main.py（已有 _load_latest_session）
   │    └─ 返回上次任务描述（如果有）
   │
   ├─ 4. restore_workspace()   ← 🔴 新增 cli/recovery.py
   │    └─ 过滤 workspace/ 中的活跃 Agent
   │    └─ 列出可恢复的任务列表
   │
   ├─ 5. restore_memory()      ← 🔴 新增 cli/recovery.py
   │    └─ 调用 MemoryManager.restore(project_name)
   │    └─ 返回可用 Memory 条目数
   │
   └─ 6. select_backend()      ← runtime.py（已有 _auto_select_default_backend）
        └─ 按优先级选择可用 Backend
```

所有恢复逻辑封装在 `cli/recovery.py` 中：

```python
# cli/recovery.py (新增)
class RecoveryManager:
    """启动时的自动恢复管理器。
    
    调用已有模块的恢复能力，不做新增功能。
    """

    def __init__(self, project_root: Path | None):
        self._project_root = project_root

    def restore_session(self) -> str | None:
        """从 .zmai/sessions/latest.json 恢复上次任务。"""
        ...

    def restore_workspace(self) -> list[AgentState]:
        """从 workspace/ 恢复活跃 Agent 工作区。"""
        ...  # 调用 workspace.get_global_state()

    def restore_memory(self) -> MemorySummary:
        """恢复项目相关的 Memory 上下文。"""
        ...  # 调用 MemoryManager.restore()

    def summary(self) -> RecoverySummary:
        """返回所有恢复状态的摘要。"""
        ...
```

### 5.2 恢复 Workspace（具体实现）

现有 `Workspace` 已有：
- `get_global_state()` → 所有 Agent 状态
- `list_agents()` → 列出所有 Agent
- `get_state(agent_id)` → 单个 Agent 状态

CLI 层只需要调用：

```python
# 在 cli/recovery.py 中
from zmai.workspace import Workspace

ws = Workspace(root=proj_config.get("workspace.root", "./workspace"))
global_state = ws.get_global_state()
active_agents = [
    (aid, state)
    for aid, state in global_state.workspaces.items()
    if state.status == "active"
]
```

**没有修改 Workspace 模块。** 只调用已有的 API。

### 5.3 恢复 Memory（具体实现）

现有 `MemoryManager` 已有：
- `restore(agent_id)` → 从 Long-term Memory 恢复到 Working Memory
- `exists(agent_id)` → 检查是否有持久化 Memory
- `working(agent_id)` → 获取 Working Memory

CLI 层只需要调用：

```python
# 在 cli/recovery.py 中
from zmai.memory import MemoryManager

mm = MemoryManager(long_term_root=".zmai/memory")
if mm.exists(project_name):
    mm.restore(project_name)
    wm = mm.working(project_name)
    entry_count = len(wm._data)  # 或已有方法
```

**没有修改 Memory 模块。**

### 5.4 恢复 Session（具体实现）

已有：

```python
# main.py 已有
def _load_latest_session() -> str | None:
    ...

def _save_session(task: str) -> None:
    ...
```

优化：将 Session 与 Workspace/Memory 信息合并展示。

### 5.5 恢复链的可见性

用户不应感到"恢复"是一个操作。CLI 仅通过 Banner 告知：

```
$ zmai

  ⚡ zmai v0.1.0
  ───────────────────────────────────────────
  项目    my-project
  ▶ 恢复  Workspace: agent_1 (续跑) + agent_2 (已完成)
  ▶ 恢复  记忆: 上次对话 (3 条目)
  ▶ 恢复  Session: "修复 auth 模块" (上次未完成)
  Backend  DeepSeek
  ───────────────────────────────────────────
  zmai>
```

**未完成任务自动提示续跑：**

```
  zmai> 继续修复 auth 模块

  (如果 detect 到上次未完成任务，自动询问)
  ⚠ 检测到上次未完成任务: "修复 auth 模块" (进度 60%)
  恢复并继续？[Y/n] Y
  ⟳ 正在恢复 Workspace ...
  ⟳ 正在恢复 Memory ...
  ⟳ 正在继续任务 ...
```

---

## 6. REPL 交互优化

### 6.1 当前实现

```python
# main.py:247-264
def _cmd_interactive(runtime, config, args):
    session_task = _load_latest_session()
    if session_task:
        print_info(f"last task: {session_task[:60]}", theme)
    try:
        while True:
            try:
                task = input(f"{theme.highlight('zmai> ')}").strip()
            except EOFError:
                break
            ...
    except KeyboardInterrupt:
        print()
```

**问题：**
1. 使用 `input()` — 无历史记录、无行编辑、无补全
2. 无持久化历史文件
3. 无多行输入支持
4. 无 Ctrl+C 安全退出（在 `_cmd_run` 执行中无法中断）
5. 无 tab 补全

### 6.2 优化方案：增量改进，不引入大依赖

使用 Python 标准库 `readline` / `msvcrt`，不引入 `prompt_toolkit` 等第三方库。

#### 6.2.1 添加 Readline 历史支持

```python
# cli/repl.py (新增)
import atexit
import os
from pathlib import Path

HISTORY_FILE = Path.home() / ".zmai" / "history"

class REPL:
    """交互式 REPL。"""

    def __init__(self, prompt: str = "zmai> "):
        self._prompt = prompt
        self._history_file = HISTORY_FILE

    def _setup_readline(self):
        """配置 readline 历史。"""
        try:
            import readline
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            if self._history_file.exists():
                readline.read_history_file(str(self._history_file))
            readline.set_history_length(1000)
            atexit.register(readline.write_history_file, str(self._history_file))
        except ImportError:
            pass  # Windows 或没有 readline 时优雅降级

    def run(self, handler):
        """REPL 主循环。"""
        self._setup_readline()
        try:
            while True:
                try:
                    line = input(self._prompt).strip()
                except EOFError:
                    break
                if not line:
                    continue
                if line.lower() in ("exit", "quit"):
                    break
                handler(line)
        except KeyboardInterrupt:
            print()
```

#### 6.2.2 Windows 兼容方案

Windows 自带的 `msvcrt` 支持简单行编辑。检测到 Windows 时，使用 `pyreadline3`（轻量）或回退到 `input()`。

#### 6.2.3 任务内 Ctrl+C 安全处理

当前问题：`_cmd_run` 使用 `asyncio.run(runtime.run(...))`，Ctrl+C 直接终止进程。

优化方案：

```python
# 在 REPL 中运行任务时：
async def _run_with_cancel(task, runtime):
    run_task = asyncio.create_task(runtime.run(...))
    try:
        return await asyncio.wait_for(run_task, timeout=None)
    except asyncio.CancelledError:
        # 任务被取消 → 暂停而非终止
        await runtime.pause(agent_id, reason="user_interrupt")
        print("\n⚠ 任务已暂停，输入 /resume 继续，/cancel 取消")
        return {"status": "paused"}
```

在 CLI 层的 REPL 中，按 Ctrl+C 时：
1. 如果在 `_cmd_run` 执行中 → `asyncio.Task.cancel()` → Runtime 捕获 CancelledError → 暂停状态
2. 如果在 REPL 输入中 → 换行，不退出（类似 shell 行为）

### 6.3 REPL 命令

在 REPL 中支持一些内建命令：

| 命令 | 作用 |
|------|------|
| `/resume` | 恢复上次暂停的任务 |
| `/cancel` | 取消当前任务 |
| `/status` | 显示当前状态 |
| `/mem` | 查看 Memory 摘要 |
| `/ws` | 查看 Workspace 文件 |
| `/exit` 或 Ctrl+D | 退出 |
| `/help` | 查看内建命令 |

**实现方式**：在 REPL handler 中检查 `line.startswith("/")`。

### 6.4 持久化历史文件

```
~/.zmai/history         ← 命令历史（最多 1000 条）
~/.zmai/sessions/       ← 会话保存目录（已有）
```

Readline 的 `write_history_file` 自动处理。

---

## 7. 终端输出优化

### 7.1 执行进度可视化

当前（main.py:200-213）：纯文本回调。

优化为带进度条和执行步骤展示：

```python
# cli/progress.py (新增)
class ProgressDisplay:
    """进度渲染。"""

    def __init__(self, steps: int | None = None):
        self._steps = steps
        self._current = 0
        self._status_line = ""

    def update(self, step: int | None = None, status: str = ""):
        """更新进度。"""
        if step is not None:
            self._current = step
        if self._steps:
            pct = min(int(self._current / self._steps * 100), 100)
            bar = self._render_bar(pct)
            sys.stderr.write(f"\r  {bar} {pct}%  {status}\033[K")

    @staticmethod
    def _render_bar(pct: int, width: int = 30) -> str:
        filled = "━" * int(pct / 100 * width)
        empty = "─" * (width - len(filled))
        return f"[{filled}{empty}]"

    def done(self):
        sys.stderr.write("\n")
```

**不引入第三方库。** 使用纯 Unicode 字符 + ANSI 转义。

### 7.2 工具执行显示

当前回调（main.py:200-213）输出散乱。

优化为结构化的执行流显示：

```
  ── ── ── ── ── ── ── ── ── ──
  [1/5] 📂 read_file("main.py")          (0.2s)
  [2/5] 🔍 grep("bottleneck", "*.py")    (0.3s)
  [3/5] ✏️  edit("main.py", L45)          (0.1s)
  [4/5] 🖥️  shell_exec("pytest")          (3.2s)
  [5/5] ✅ 测试通过 (42 passed)
  ── ── ── ── ── ── ── ── ── ──
```

**实现方式**：修改 `_cmd_run` 的 `on_progress` 回调，使用 `cli/progress.py`。

### 7.3 输出格式一致性

当前：`formatters.py` 使用 `+`, `x`, `!`, `i` 前缀。

| 当前符号 | 含义 | 新符号 | 含义 |
|---------|------|--------|------|
| `+` | 成功 | `✔` | 成功 |
| `x` | 错误 | `✘` | 错误 |
| `!` | 警告 | `⚠` | 警告 |
| `i` | 信息 | `ℹ` | 信息 |
| — | — | `▶` | 恢复/开始 |
| — | — | `⟳` | 进度中 |

**实现方式**：修改 `formatters.py` 的符号。

### 7.4 彩色输出检测

当前仅在 `--no-color` 时禁用。优化为自动检测：

```python
def _should_use_color() -> bool:
    """自动检测终端是否支持颜色。"""
    if not sys.stdout.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return True
```

---

## 8. 安全与退出

### 8.1 Ctrl+C 三层处理

```
             Ctrl+C 按下
                 │
          ┌──────┴──────┐
          │ 在输入中?    │ → 换新行，不退出（保持 REPL）
          │ 在任务中?    │ → 暂停任务，提示恢复
          │ 在配置向导?  │ → 优雅退出，数据不损坏
          └─────────────┘
```

当前：`KeyboardInterrupt` 仅被 `_cmd_interactive` 捕获，`_cmd_run` 执行中按 Ctrl+C 会杀死进程。

优化后：

```python
# main.py setup_signal_handlers
import signal

def _handle_sigint(signum, frame):
    """安全中断处理。"""
    if _current_task:
        # 正在执行任务 → 暂停
        _current_task.cancel()
        print("\n  ⚠ 任务已暂停。输入 /resume 继续，/cancel 取消。")
    else:
        # 空闲状态 → 不退出（按两次 Ctrl+C 或 Ctrl+D 退出）
        print()
```

### 8.2 退出前清理

退出 REPL 时自动：

```
  ── 退出前 ──
  ⟳ 保存 Session ...
  ⟳ 持久化 Memory ...
  ⟳ 清理临时文件 ...
  Bye 👋
```

实现方式：`atexit.register(shutdown_handler)`。

### 8.3 双重 Ctrl+C 强制退出

```
第一次 Ctrl+C → 暂停任务
第二次 Ctrl+C → 确认退出
第三次 Ctrl+C → 强制退出（不保存）
```

---

## 9. Shell 集成

### 9.1 Shell Completion 脚本生成

新增 `cli/completions.py`，为 bash/zsh/fish 生成补全脚本。

```python
# cli/completions.py (新增)
def generate_bash() -> str:
    return """_zmai() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"

    # subcommands
    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=($(compgen -W "--backend --model --json --no-color --version --help config auth status --" -- "$cur"))
        return 0
    fi

    # --backend values
    if [[ "$prev" == "--backend" ]]; then
        COMPREPLY=($(compgen -W "claude deepseek openai ollama" -- "$cur"))
        return 0
    fi
}
complete -F _zmai zmai"""

def generate_zsh() -> str: ...
def generate_fish() -> str: ...

def install(shell: str = "") -> None:
    """自动安装补全到 shell 配置。"""
```

### 9.2 `zmai init` 子命令

```bash
$ zmai init
  ▶ 检测到 shell: zsh (oh-my-zsh)
  ▶ 安装命令补全 ...
  ▶ 设置环境变量 (自动识别 Backend) ...
  ✅ ZMAI shell 集成完成
  重启终端或 source ~/.zshrc
```

### 9.3 环境变量感知

| 变量 | 作用 |
|------|------|
| `ZMAI_CONFIG` | 配置文件路径 |
| `ZMAI_DEBUG` | 开启调试日志 |
| `NO_COLOR` | 禁用着色（标准约定） |
| `CLICOLOR_FORCE` | 强制着色 |

这些已在 `config/sources.py` 中部分支持，CLI 层只需确保启动时检查。

---

## 10. 实现计划

### 10.1 文件清单

```
src/zmai/cli/
├── __init__.py          # 无修改
├── main.py              # 🔧 精简参数，串联恢复链
├── formatters.py        # 🔧 符号更新，NO_COLOR 检测
├── detector.py          # ✅ 无修改（已完善）
├── context.py           # ✅ 无修改（已完善）
├── detectors/           # ✅ 无修改（已完善）
├── recovery.py          # 🔴 新增 — 自动恢复管理器
├── repl.py              # 🔴 新增 — REPL 带历史/安全退出
├── progress.py          # 🔴 新增 — 进度条/执行流显示
├── completions.py       # 🔴 新增 — Shell 补全生成
└── banner.py            # 🔴 新增 — 启动摘要展示
```

### 10.2 修改范围

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `main.py` | 🔧 修改 | 精简参数，集成 recovery/repl/banner |
| `formatters.py` | 🔧 修改 | 符号更新 + NO_COLOR 自动检测 |
| `recovery.py` | 🔴 新增 | RecoveryManager 类 |
| `repl.py` | 🔴 新增 | REPL + readline 历史 + 安全退出 |
| `progress.py` | 🔴 新增 | 进度条 + 工具执行流显示 |
| `completions.py` | 🔴 新增 | Shell 补全脚本生成 |
| `banner.py` | 🔴 新增 | 启动摘要 Banner |

### 10.3 代码变更量化

```
现有 CLI 代码量：
  main.py        419 行
  formatters.py  154 行
  detector.py    114 行
  context.py      90 行
  detectors/      ~260 行
  总计            ~1037 行

优化后新增：
  recovery.py     ~80 行
  repl.py         ~100 行
  progress.py     ~80 行
  completions.py  ~120 行
  banner.py       ~60 行
  新增总计        ~440 行

main.py 简化后：
  419 行 → ~250 行（精简 40%）

所有改动仅在 cli/ 目录内。
```

### 10.4 第零方依赖原则

所有新增代码仅依赖 Python 标准库：

| 功能 | 依赖 | 理由 |
|------|------|------|
| Readline 历史 | `readline`（stdlib） | 历史记录 |
| 进度渲染 | ANSI 转义（纯字符串） | 无依赖 |
| Banner | `formatters.py`（已有） | 复用主题 |
| 颜色检测 | `os.environ` / `sys.stdout.isatty` | 标准做法 |
| Shell 补全 | 字符串模板 | 纯文本生成 |

**不引入任何第三方库。** 这是硬约束，确保 `pip install zmai` 后立即可用。

---

## 11. 与现有代码的关系

### 11.1 调用关系图

```
用户输入: $ zmai
              │
              ▼
         main.py (精简版 parser)
              │
       ┌──────┴────────┐
       │                │
  还原参数           检测 stdin
       │                │
  ┌────┴────┐      ┌───┴───┐
  │ config  │      │ REPL  │ (repl.py)
  │ auth    │      │ task  │
  │ status  │      └───┬───┘
  └─────────┘          │
                  ┌────┴────┐
                  │ recovery│ (recovery.py)
                  │ banner  │ (banner.py)
                  └────┬────┘
                       │
                  ┌────┴────┐
                  │ Runtime │ (不变)
                  │ .run()  │
                  └────┬────┘
                       │
                  ┌────┴────┐
                  │ progress│ (progress.py)
                  │         │ → on_progress 回调
                  └─────────┘
```

### 11.2 不修改的已有文件对照

| 已有文件 | 在本设计中 | 实际 |
|----------|-----------|------|
| `cli/detector.py` | "🔴 新增" (v1.0 误标) | ✅ 已有，不修改 |
| `cli/context.py` | — | ✅ 已有，不修改 |
| `cli/detectors/lang.py` | — | ✅ 已有，不修改 |
| `cli/detectors/git_detector.py` | — | ✅ 已有，不修改 |
| `cli/formatters.py` | — | 🔧 仅符号更新 |

### 11.3 向后兼容

- 旧参数（`-p`, `-i`, `-r`, `-c`）仍然支持但不展示在帮助中
- 旧的 `zmai config` / `zmai auth` 子命令行为不变
- 所有新增文件以新文件名独立存在，不影响已有导入

---

> **总结：**
>
> ZMAI CLI v2.0 的优化核心不是增加功能，而是**用最少的命令、最少的参数、最少的用户操作，把用户送入开发状态。**
>
> 通过串联已有的 Workspace、Memory、Detector、Runtime 基础设施，实现**启动即恢复**。通过增加 readline 历史、Shell 补全、进度可视化、安全退出，提升日常交互的流畅度。
>
> **所有改动仅在 `cli/` 层，不修改任何下游模块。**
