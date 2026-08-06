# ZMAI CLI 优化审查报告

> 审查日期: 2026-07-17
> 范围: CLI 入口、子命令、REPL、恢复、退出、帮助
> 参考: Git, Docker, Claude Code

---

## 一、执行摘要

ZMAI CLI 当前处于 **v1 工作状态**，核心功能完整：

- 三个子命令（`config`、`auth`、`doctor`）可用
- REPL 交互模式支持多轮任务、历史记录、斜杠命令
- 一次性任务支持管道输入和 `--json` 输出
- 项目检测 + 多 backend 自动选择

**核心问题**：CLI 实现在单一 `main.py`（629 行），未利用已有的设计文档（10+ 份设计文档，约 9000 行）。设计文档规划的 `repl.py`、`recovery.py`、`progress.py`、`banner.py`、`completions.py` 全部未实现。

**三大参考 CLIs 的核心启示**：

| 来源 | 对 ZMAI 最相关的模式 | 当前实现状态 |
|------|---------------------|-------------|
| Git | verb-first 命令、layered config、exit codes | 部分实现 |
| Docker | JSON output、context switch、system prune | 待实现 |
| Claude Code | slash commands、REPL-first、auto-compact | 部分实现 |

---

## 二、当前 CLI 全景分析

### 2.1 命令结构

```
zmai                            → REPL 交互模式
zmai config <get|set|list>      → 配置管理
zmai auth <list|switch|update|remove> → 凭证管理
zmai doctor [--json]            → 诊断检查
zmai <task description...>      → 一次性任务
zmai --json <task>              → JSON 输出模式
zmai --backend <name> <task>    → 指定 backend
```

**架构模式**：手动子命令分发（`if cmd == "config":`），非 argparse subparsers。3 个子命令 + 1 个自由文本入口。

### 2.2 每个子命令的步骤数

| 子命令 | 使用者输入 | 内部步骤数 | 评价 |
|--------|-----------|-----------|------|
| `zmai` | 0 | 10 | 10 阶段启动，可行 |
| `zmai config list` | 3 | 1 | 简洁 |
| `zmai auth update claude sk-...` | 4 | 5 | 略长，但合理 |
| `zmai doctor` | 2 | 8 | 合理 |
| `zmai "重构 auth 模块"` | 2 | 7 | 核心用法，简洁 |

### 2.3 当前架构优势

1. **零配置启动**：`zmai` 无参数直接进入 REPL，自动检测项目、自动选 backend
2. **多模式感知**：TTY → REPL、管道 → 一次性、`--json` → 机器输出
3. **Backend 抽象**：切换 backend 只需 `zmai auth switch deepseek`
4. **Session 持久化**：任务历史保存到 `~/.zmai/sessions/latest.json`
5. **设计驱动**：10+ 份设计文档提供了清晰的演进路线

---

## 三、参考 CLI 设计原则

### 3.1 Git 模式

Git 的核心设计原则：

| 原则 | Git 实现 | 对 ZMAI 的启示 |
|------|---------|---------------|
| **Verb-first** | `git commit`, `git push` | ZMAI 已遵循：`zmai config`, `zmai auth` |
| **Layered config** | system → global → local | ZMAI 有 CLI→Env→File 三层，缺 `--global` |
| **Exit codes** | 0=OK, 1=error, 128+=signal | ZMAI 用 0/1/3/130，需文档化 |
| **Interactive/batch** | `git log` TTY→less, pipe→text | ZMAI 已实现（TTY→REPL, pipe→oneshot） |
| **Abbreviation** | `git com` → `commit` | 不适用（ZMAI 目前只有 3 个子命令） |
| **Alias** | `git config --global alias.co checkout` | 值得借鉴 |

**关键启示**: ZMAI 的 verb-first 方向正确。重点学 Git 的 **layered config** 和 **exit code 契约**。

### 3.2 Docker 模式

Docker 的核心设计原则：

| 原则 | Docker 实现 | 对 ZMAI 的启示 |
|------|-----------|---------------|
| **JSON output** | `docker ps --format json` | ZMAI 的 `--json` 应扩展到所有子命令 |
| **Context system** | `docker context use <name>` | ZMAI 的 `zmai auth switch` 是雏形，可扩展 |
| **Prune** | `docker system prune` | 缺失：`zmai prune` 清理 workspace/memory |
| **Noun-first** | `docker container run` | 不采用，ZMAI 保持 verb-first |
| **Short flags** | `-d`, `-v`, `-p` | 当前 5 个 flag，短别名收益有限 |

**关键启示**: 重点学 Docker 的 **JSON output 一致性**和 **prune 模式**。

### 3.3 Claude Code 模式

Claude Code 的核心设计原则：

| 原则 | Claude Code 实现 | 对 ZMAI 的启示 |
|------|-----------------|---------------|
| **REPL-first** | 默认交互模式 | ZMAI 已实现，匹配 |
| **Slash commands** | `/help`, `/clear`, `/fast` | ZMAI 只有 `/help` `/status`，需扩展 |
| **Auto-compact** | 会话上下文自动压缩 | ZMAI 用滑动窗口，待添加 `/compact` |
| **Model switching** | `/fast` ↔ `/slow` | 缺失：`/backend` 或 `/model` |
| **Hook system** | pre/post tool hooks | 长期规划，当前不急需 |

**关键启示**: ZMAI 的 REPL 设计最接近 Claude Code。重点学 **slash commands 扩展**和 **auto-compact**。

---

## 四、六大优化方向

### 4.1 命令尽量短

#### 现状

| 命令 | 字符数 | 频率 | 评价 |
|------|-------|------|------|
| `zmai` | 4 | 高频 | ✅ 最短，无法更短 |
| `zmai "task"` | ~10 | 高频 | ✅ 核心用法，合理 |
| `zmai config get key` | 20 | 中频 | ⚠️ `config` 可缩写为 `c` |
| `zmai auth list` | 14 | 中频 | ⚠️ `auth` 可缩写为 `a` |
| `zmai doctor` | 11 | 低频 | ⚠️ 可缩写为 `dr` |
| `zmai doctor --json` | 19 | 低频 | ⚠️ 可缩短 |

#### 建议

**1. 子命令别名（高优先级）**

引入最短别名，参考 Docker `docker → d` 的做法：

```python
# main.py 子命令分发处
SUBCOMMAND_ALIASES = {
    "c": "config", "cfg": "config",
    "a": "auth",   "auth": "auth",
    "d": "doctor", "dr": "doctor", "doc": "doctor",
}
```

实现方式：在手动 dispatch 前做别名解析：

```python
cmd = aliases.get(argv[0], argv[0])
```

效果：

```
zmai c list         → zmai config list
zmic a switch deepseek → zmai auth switch deepseek
zmai d              → zmai doctor
zmai d --json       → zmai doctor --json
```

别名表：

| 完整名 | 最短 | 推荐别名 |
|--------|------|---------|
| `config` | `c` | `c`, `cfg` |
| `auth` | `a` | `a` |
| `doctor` | `d` | `d`, `dr`, `doc` |
| （未来）`prune` | `p` | `p` |
| （未来）`serve` | `s` | `s` |

**2. 短标志（中优先级）**

当前 5 个长标志，添加短别名：

| 长标志 | 短标志 | 说明 |
|--------|--------|------|
| `--json` | `-j` | 通用 JSON 输出 |
| `--no-color` | `-C` | 禁用颜色 |
| `--backend` | `-b` | 指定 backend |
| `--help` | `-h` | 已支持 |
| `--version` | `-v` | 显示版本 |

**3. Shell 补全（低优先级）**

生成 bash/zsh/fish 补全脚本，支持 `zmai c[TAB]` → `zmai config`。参考 `click` 的 `shell_completion` 或手写补全函数。

**4. 选项补全（中优先级）**

REPL 内 Tab 补全命令和路径。当前 `_setup_readline()` 只启用了历史，未注册 completer。

---

### 4.2 进入 (Entry)

#### 现状

`main()` 函数包含 10 个启动阶段（629 行中的行 539-628）：

```
1. UTF-8 编码设置
2. Auth 凭证注入
3. 子命令分发
4. Argparse 构建
5. 初始化向导检查
6. 项目根检测
7. 项目上下文构建
8. Runtime 初始化
9. Workspace 清理
10. 任务分发 (REPL / oneshot / pipe)
```

#### 问题

1. **全同步启动**：所有阶段串行执行。项目检测依赖文件系统 I/O，Runtime 初始化依赖 import 所有模块
2. **没有进度指示**：启动耗时 ~1-2s 但无任何提示
3. **阶段刚性**：一个阶段失败（如 workspace 清理抛异常）不会影响后续阶段，但代码结构是嵌套的 try/except，不易读

#### 建议

**1. 启动阶段并行化（中优先级）**

将启动拆分为独立阶段，用 `concurrent.futures` 并行执行 I/O 密集阶段：

```
并行组 1:
  ├── 项目检测       (I/O)
  ├── Auth 凭证注入   (文件 I/O)
  └── Session 加载    (文件 I/O)

并行组 2:
  ├── Runtime 初始化   (import + 注册)
  ├── Workspace 清理   (I/O)
  └── Context 构建     (内存)
```

**2. 启动优化顺序（低优先级）**

实现 CLI_UX_DESIGN.md v2.0 §3.1 的 "零等待启动"：先显示 prompt，后台加载：

```
zmai> _                          ← prompt 立即可见
  (loading project...)           ← dim 状态行
  (loading runtime...)           ← 异步更新
zmai> 帮我重构 auth 模块          ← 用户输入时后台已完成
```

**3. 命令行一键执行（高优先级）**

当前最短任务入口：`zmai "task"`。建议支持更短形式：

```bash
# 当前
zmai "列出文件"

# 建议增加 -e / --exec 短标志，但更重要的是一致性
```

**4. 单子命令自动进入子命令（低优先级）**

当只有一个参数且匹配子命令时，自动假设为操作意图：

```bash
zmai config     → zmai config list  (当前 show usage)
zmai auth       → zmai auth list    (当前 show usage)
```

---

### 4.3 REPL

#### 现状

`_cmd_interactive()`（行 368-434，67 行）实现了一个最小功能 REPL：

```
✅  readline 历史记录（~/.zmai/history，2000 行）
✅  /help 和 /status 斜杠命令
✅  exit / quit / Ctrl+D / Ctrl+C
✅  上次任务提示
✅  每任务唯一 agent_id
❌  无 /clear 命令
❌  无 /retry 命令
❌  无 /model 命令
❌  无 Tab 补全
❌  无多行输入
❌  无任务耗时显示
```

#### 问题

1. **缺少关键斜杠命令**：只有 `/help` 和 `/status`
2. **无上下文管理**：每个任务用新 agent_id (`repl_pid_counter`)，任务间不共享上下文
3. **无进度显示**：只打印 `> msg` 和 `ok/fail`，无 spinner 或进度条
4. **无任务统计**：完成一个任务后不显示耗时、token 消耗

#### 建议

**1. 扩展斜杠命令（高优先级）**

在 `_handle_builtin()` 中添加：

| 命令 | 实现 | 说明 |
|------|------|------|
| `/clear` | 清空屏幕/上下文 | `print("\033[2J\033[H")` + 重置 agent |
| `/retry` | 重新执行上一条任务 | 从 session 读取最后 task 并重跑 |
| `/model <name>` | 切换 backend | `runtime._gateway.set_default(name)` |
| `/compact` | 压缩上下文 | 保留最近 messages + 一份摘要 |
| `/help [cmd]` | 显示指定命令帮助 | `zmai> /help model` |

**2. 添加任务耗时和 Token 统计（低优先级）**

每个任务完成后显示：

```
zmai> 重构 auth 模块
  ✓ Task completed in 12.3s (2,341 tokens in / 489 tokens out)
zmai> _
```

从 `BackendResponse.usage` 获取 token 数据，从 `time.monotonic()` 获取时间。

**3. 共享 Runtime 上下文（中优先级）**

当前每个 REPL 任务用新 agent_id。改为共享一个 ConversationManager，让后续任务可以引用前序任务的结果：

```
Task 1: "列出文件"  → agent_id = "repl_session"
Task 2: "修改第一个文件" → agent_id = "repl_session" (累计上下文)
```

需要修改 Runtime 的 LifecycleManager 以支持 "completed" 状态的 agent 继续使用（或新建一个会话模式）。

**4. Tab 补全（中优先级）**

注册 `readline.set_completer()` 函数，支持：

- 命令补全：`/h[TAB]` → `/help`
- 参数补全：`zmai c[TAB]` → `config`
- 文件路径补全（在 task 输入时）

---

### 4.4 自动恢复 (Auto-Recovery)

#### 现状

当前最小实现：

```python
# _load_latest_session() — 仅加载任务文本
def _load_latest_session() -> str | None:
    try:
        data = json.loads(Path(HOME / "sessions" / "latest.json").read_text())
        return data["task"]
    except Exception:
        return None

# 使用：在 REPL 入口显示 "last task: {task[:60]}"
# _save_session(task) — 仅保存任务文本
```

#### 问题

1. **只保存任务文本**：不保存 workspace 状态、memory 状态、agent 运行状态
2. **只显示不执行**：只显示 "last task: xxx"，不提供 "resume" 能力
3. **无断点恢复**：如果 agent 在执行过程中被中断（Ctrl+C、崩溃），无法从中断点恢复
4. **无多会话管理**：只保存 `latest.json`，无法回溯历史

#### 建议

**1. 三段恢复链（中优先级）**

按 CLI_UX_DESIGN.md §5.1 实现 RecoveryManager：

```python
class RecoveryManager:
    def restore_session() -> Session | None   # 读取 ~/.zmai/sessions/latest.json
    def restore_workspace() -> str | None      # 恢复 agent workspace
    def restore_memory() -> int               # 恢复 MemoryManager 状态
    def summary() -> str                      # "上次任务: xxx (完成/中断)"
```

启动时自动执行三段恢复：

```
zmai> _
  (恢复 session: "重构 auth 模块")   ← dim 提示
  (恢复 workspace: agent_abc)        ← dim 提示
  (恢复 memory: 12 entries)           ← dim 提示
  [上次任务未完成，输入 /resume 继续]  ← 醒目提示
```

**2. `/resume` 命令（高优先级）**

REPL 中通过 `/resume` 重新执行或继续上次未完成的任务：

- 如果上次任务成功完成 → 重新执行
- 如果上次任务被中断 → 继续执行（需 agent 支持上下文注入）
- 如果上次任务出错 → 重新执行并提供错误上下文

**3. Session 历史管理（低优先级）**

从单个 `latest.json` 升级为带时间戳的 session 文件：

```
~/.zmai/sessions/
├── latest.json              ← 当前最新
├── 2026-07-17T10:00:00Z.json
├── 2026-07-16T14:30:00Z.json
└── ... (保留最近 30 天)
```

保留 `latest.json` 作为 symlink 或副本以保证向前兼容。

---

### 4.5 自动退出 (Auto-Exit)

#### 现状

| 场景 | 当前行为 | 退出码 |
|------|---------|--------|
| 任务成功完成 | `sys.exit(0)` | 0 |
| 任务执行错误 | `sys.exit(3)` | 3 |
| REPL Ctrl+C | 打印 "bye" + return | 无 |
| REPL Ctrl+D | 打印 newline + return | 无 |
| REPL exit/quit | break + return | 无 |
| One-shot Ctrl+C | `sys.exit(130)` | 130 |
| Doctor 检查失败 | `sys.exit(1)` | 1 |
| Config/Auth 用法错误 | `sys.exit(1)` | 1 |
| 未捕获异常 | `sys.exit(1)` | 1 |

#### 问题

1. **退出码不统一**：不同错误场景混用同一个码（1），无法区分错误类型
2. **无清理 Hook**：退出时不主动清理 workspace、保存 memory、发送通知
3. **REPL 退出无声**：不显示统计信息（任务数、总耗时、总 token）
4. **One-shot 和 REPL 行为不一致**：oneshot 有 sys.exit()，REPL 没有

#### 建议

**1. 规范退出码契约（高优先级）**

| 退出码 | 含义 | 场景 | Git/Docker 对应 |
|--------|------|------|----------------|
| 0 | 成功 | 任务完成、操作成功 | 0 (success) |
| 1 | 一般错误 | 参数错误、配置错误 | 1 (general) |
| 2 | 任务执行错误 | Agent 完成了但带错误 | 不同于 1 的 exec 错误 |
| 3 | 中断 | Ctrl+C | 128+SIGINT(2)=130 |
| 4 | 诊断失败 | doctor 检查不通过 | 自定义 |

统一行为：

```python
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_TASK_FAILED = 2
EXIT_INTERRUPTED = 130
EXIT_DOCTOR_FAILED = 4
```

**2. 注册 atexit 清理 Hook（中优先级）**

```python
import atexit

def _cleanup():
    """统一清理：保存未完成的任务、清理临时文件。"""
    save_running_state()
    sys.stderr.write("\n  cleaning up...\n")

atexit.register(_cleanup)
```

**3. REPL 退出统计（低优先级）**

退出 REPL 时显示会话统计：

```
zmai> exit
  Bye! Session stats:
  • Tasks: 12
  • Time: 3m 42s
  • Tokens: 23,401 in / 4,892 out
```

**4. 一键退出（低优先级）**

CLI_UX_DESIGN.md §8.1 的 Ctrl+C 三层处理：

| 状态 | 按 1 次 | 按 2 次（5s 内） |
|------|---------|-----------------|
| 空闲（REPL 等待输入） | 换行 | exit |
| 任务执行中 | 暂停任务，回到 REPL | 强制退出（`os._exit(1)`）|
| 暂停中 | 回到 REPL | exit |

---

### 4.6 自动帮助 (Auto-Help)

#### 现状

`_print_help()`（行 210-229）提供单一文本帮助：

```
ZMAI - Model-Agnostic Agent Runtime

Usage:
  zmai                        Enter interactive REPL mode
  zmai <task description>     Run a single task
  zmai --json <task>          Run task, output JSON
  ...
```

调用时机：
- `zmai --help` 或 `zmai -h`
- 管道输入为空时的 fallback
- 没有其他子命令匹配时

#### 问题

1. **无分层帮助**：`zmai config --help` 不会显示 config 子命令的帮助（当前会走 `_print_help()`）
2. **帮助与 argparse 分离**：`add_help=False`，argparse 的 `-h` 不工作（已被手动接管）
3. **无错误提示帮助**：命令错误时不提示 "Did you mean?"
4. **无示例**：帮助中没有使用示例

#### 建议

**1. 子命令级别 `--help`（高优先级）**

让每个子命令支持 `--help`：

```python
if cmd == "config":
    if "--help" in rest or "-h" in rest:
        _print_config_help()
        return
    _run_config(rest)
```

帮助内容：

```
zmai config --help

Usage: zmai config <command> [key] [value]

Commands:
  list              Show all config keys and values
  get <key>         Get a config value
  set <key> <value> Set a config value

Examples:
  zmai config list
  zmai config get runtime.max_iterations
  zmai config set cli.theme light
```

**2. "Did you mean?" 错误提示（中优先级）**

当用户输入错误子命令时，自动建议最接近的匹配：

```
$ zmai configure
Unknown subcommand 'configure'. Did you mean 'config'?
```

实现：用 `difflib.get_close_matches()` 或 Levenshtein 距离计算。

**3. 上下文帮助（低优先级）**

HELP_DESIGN.md v1.0 设计的层次帮助系统：

| 级别 | 触发方式 | 输出 |
|------|---------|------|
| 0 - 无 | 正常使用 | 无输出 |
| 1 - 精简 | `zmai --help` | 一屏命令列表 |
| 2 - 详细 | `zmai help config` | 带示例的详细文档 |
| 3 - 完整 | `zmai help --all` | 完整手册 |

**4. 子命令注册表（中优先级）**

将子命令定义和帮助文本统一到注册表中，消除 magic string：

```python
SUBCOMMANDS = {
    "config": {
        "help": "Manage configuration",
        "handler": _run_config,
        "aliases": ["c", "cfg"],
        "args": "<get|set|list> [key] [value]",
    },
    "auth": {
        "help": "Manage authentication",
        "handler": _run_auth,
        "aliases": ["a"],
        "args": "<list|switch|update|remove> [name]",
    },
    "doctor": {
        "help": "Diagnose installation",
        "handler": _run_doctor,
        "aliases": ["d", "dr", "doc"],
        "args": "[--json]",
    },
}
```

自动生成：

- `zmai --help` 从注册表渲染所有子命令
- `zmai help config` 渲染 config 的详细帮助
- 子命令 dispatch 从注册表查询

---

## 五、实施优先级

### P0 — 立即实施（低 effort，高 value）

| # | 优化项 | 方向 | Effort | 预期效果 |
|---|--------|------|--------|---------|
| 1 | 子命令别名 (`c`/`a`/`d`) | 命令短 | 1 小时 | 减少 40% 打字量 |
| 2 | `/clear` 斜杠命令 | REPL | 0.5 小时 | 基本 UX 完整 |
| 3 | 子命令 `--help` | 自动帮助 | 2 小时 | 解决最常见的困惑 |
| 4 | `/resume` 命令 | 自动恢复 | 2 小时 | 中断后快速恢复 |
| 5 | 短标志 (`-j`/`-b`) | 命令短 | 1 小时 | 脚本友好 |

### P1 — 短期实施（中 effort，高 value）

| # | 优化项 | 方向 | Effort | 预期效果 |
|---|--------|------|--------|---------|
| 6 | `--json` 扩展到所有子命令 | 命令短 | 2 小时 | 脚本一致性 |
| 7 | 退出码契约文档化 | 自动退出 | 1 小时 | 脚本可靠性 |
| 8 | Tab 补全（REPL 内） | REPL | 4 小时 | 提升体验 |
| 9 | `/model <name>` 斜杠命令 | REPL | 3 小时 | 减少上下文切换 |
| 10 | "Did you mean?" 错误提示 | 自动帮助 | 2 小时 | 降低新手学习成本 |

### P2 — 中期实施（中 effort，中 value）

| # | 优化项 | 方向 | Effort | 预期效果 |
|---|--------|------|--------|---------|
| 11 | 三段恢复链 (RecoveryManager) | 自动恢复 | 8 小时 | 完整断点恢复 |
| 12 | REPL 共享 Runtime 上下文 | REPL | 6 小时 | 多轮对话上下文 |
| 13 | 子命令注册表 | 自动帮助 | 4 小时 | 消除重复代码 |
| 14 | `zmai prune` 清理命令 | 系统 | 3 小时 | 磁盘空间管理 |
| 15 | REPL 任务统计显示 | REPL | 2 小时 | 进度感知 |

### P3 — 长期规划（高 effort）

| # | 优化项 | 方向 | Effort | 预期效果 |
|---|--------|------|--------|---------|
| 16 | 异步启动 | 进入 | 10 小时 | 启动提速 |
| 17 | Shell 补全脚本 | 命令短 | 8 小时 | 用户效率 |
| 18 | `--global` config 层 | 进入 | 6 小时 | 配置灵活性 |
| 19 | atexit 清理 Hook | 自动退出 | 4 小时 | 资源泄漏防护 |
| 20 | Ctrl+C 三层处理 | 自动退出 | 6 小时 | 防误退出 |

---

## 六、结论

### 6.1 当前 CLI 评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 命令结构 | ★★★★☆ | Git 风格 verb-first，合理清晰 |
| 命令长度 | ★★★☆☆ | 子命令不可缩写，缺短标志 |
| 进入体验 | ★★★☆☆ | 功能完整但串行启动，无进度 |
| REPL | ★★★☆☆ | 基础完整但缺关键命令 |
| 自动恢复 | ★★☆☆☆ | 只保存任务文本，无恢复能力 |
| 自动退出 | ★★★☆☆ | 退出码多样但未规范化 |
| 自动帮助 | ★★★☆☆ | 有基础帮助但无分层无错误提示 |

**平均: 3.0/5 — 功能完整但体验粗糙，设计已规划但未落地。**

### 6.2 关键建议

1. **最短子命令别名** (`c`/`a`/`d`): 零成本、立竿见影
2. **补充斜杠命令** (`/clear` `/resume` `/model`): REPL 从 "够用" 升级到 "好用"
3. **子命令 `--help`**: 消除 "输入了什么" 的困惑
4. **退出码契约**: 脚本/CI 集成的基础设施
5. **子命令注册表**: 为未来扩展提供统一框架

### 6.3 与设计文档的关系

10+ 份设计文档（约 9000 行）为 ZMAI CLI 提供了极其详尽的演进路线。本报告聚焦于 **当前可以低成本快速实施的优化**，而非全面实现 v2.0 设计。建议按以下节奏推进：

- **Phase 1（1-2天）**: P0 全部实施（子命令别名、斜杠命令补充、子命令 --help）
- **Phase 2（1周）**: P1 全部实施（JSON 一致性、退出码、Tab 补全、/model、错误提示）
- **Phase 3（2周）**: P2 部分实施（RecoveryManager、共享 Runtime 上下文、prune）
- **Phase 4（长期）**: 逐渐向 CLI_UX_DESIGN.md v2.0 靠近

---

*Report generated by `claude` — 基于代码分析 + 设计文档 + 参考 CLI 设计模式*
