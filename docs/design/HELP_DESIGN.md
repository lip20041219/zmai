# ZMAI Help System Design v1.0

Version: 1.0
Date: 2026-07-16

> **短帮助、上下文感知、不刷屏。** 7 个内建命令全部支持。
>
> 不修改 Runtime / Agent / Gateway / Memory / Workspace / Workflow。

---

## 目录

1. [设计原则](#1-设计原则)
2. [命令总览](#2-命令总览)
3. [/help](#3-help)
4. [/status](#4-status)
5. [/memory](#5-memory)
6. [/backend](#6-backend)
7. [/plugin](#7-plugin)
8. [/config](#8-config)
9. [/report](#9-report)
10. [文件清单与实现计划](#10-文件清单与实现计划)

---

## 1. 设计原则

### 1.1 短帮助原则

```
每个帮助输出不超过 20 行。
只显示用户当前上下文相关的信息。
需要详细文档 → 提示 URL，不内联打印。
```

### 1.2 上下文感知

```
/help → 在项目目录中显示项目相关命令
        在聊天模式中显示通用命令
        首次使用时显示入门指南

/status → 在项目目录中显示项目上下文信息
          在聊天模式中显示 Backend 和会话状态
```

### 1.3 不刷屏

```
所有命令输出 ≤ 15 行。
没有长篇列表。
没有 ASCII 艺术大标题。
数据多 → 用折叠/摘要，不一次性打印全部。
```

### 1.4 不修改下游

```
仅修改:  src/zmai/cli/commands.py   ← 🔴 新增
         src/zmai/cli/main.py       ← 🔧 集成
```

---

## 2. 命令总览

### 2.1 命令表

| 命令 | 用途 | 输出行数 | 是否需要参数 |
|------|------|---------|-------------|
| `/help` | 显示帮助 | ≤ 15 | `[topic]` 可选 |
| `/status` | 显示当前状态 | ≤ 12 | 无 |
| `/memory` | 显示记忆摘要 | ≤ 10 | 无 |
| `/backend` | 显示/切换 Backend | ≤ 8 | `[name]` 可选 |
| `/plugin` | 显示已加载插件 | ≤ 8 | 无 |
| `/config` | 查看/修改配置 | ≤ 6 | `[key] [val]` |
| `/report` | 显示上次任务报告 | ≤ 12 | 无 |

### 2.2 命令注册

```python
# cli/commands.py — 命令注册

@dataclass
class Command:
    name: str                    # 命令名（不含 /）
    aliases: list[str]           # 别名
    description: str             # 一行描述
    usage: str                   # 用法
    handler: Callable            # 处理函数
    min_role: str = "user"       # 最低权限

COMMANDS: list[Command] = [
    Command("help",    ["?"],       "显示帮助信息",              "/help [topic]",        cmd_help),
    Command("status",  ["st"],      "显示当前会话状态",           "/status",              cmd_status),
    Command("memory",  ["mem"],     "显示记忆摘要",              "/memory",              cmd_memory),
    Command("backend", ["back"],    "显示或切换 Backend",         "/backend [name]",      cmd_backend),
    Command("plugin",  ["plugins"], "显示已加载的插件",           "/plugin",              cmd_plugin),
    Command("config",  ["cfg"],     "查看或修改配置",             "/config [key] [val]",  cmd_config),
    Command("report",  ["r"],       "显示上次任务执行报告",       "/report",              cmd_report),
]
```

### 2.3 路由

```python
def handle_command(line: str) -> bool:
    """REPL 中处理 / 开头的行。"""
    if not line.startswith("/"):
        return False

    parts = line[1:].split()
    name = parts[0].lower()
    args = parts[1:]

    for cmd in COMMANDS:
        if name == cmd.name or name in cmd.aliases:
            try:
                cmd.handler(args)
            except Exception as e:
                print(f"  ❌ 命令执行失败: {e}")
            return True

    # 未知命令
    print(f"  ⚠ 未知命令: /{name}")
    print(f"  输入 /help 查看可用命令")
    return True
```

---

## 3. /help

### 3.1 默认（无参数）

```
zmai> /help

  ─── 内建命令 ─────────────────────────
  /help           显示帮助
  /status         显示当前状态
  /memory         显示记忆摘要
  /backend        显示或切换 Backend
  /plugin         显示已加载的插件
  /config         查看或修改配置
  /report         显示上次任务报告
  ──────────────────────────────────────
  详细文档: https://zmai.dev/docs/cli
```

**总是 15 行以内。** 不输出长篇说明。

### 3.2 指定主题

```
zmai> /help backend

  /backend [name]
  ──────────────────────────────────────
  显示当前 Backend 信息，或切换到指定 Backend。

  示例:
    /backend          显示当前 Backend
    /backend claude   切换到 Claude
    /backend deepseek 切换到 DeepSeek

  相关命令:
    /config gateway.default_backend
    zmai auth list
```

```
zmai> /help config

  /config [key] [value]
  ──────────────────────────────────────
  查看或修改运行时配置。

  常用配置项:
    cli.theme        dark | light | plain
    cli.confirm      true | false
    gateway.default_backend  claude | deepseek | openai

  示例:
    /config                   列出所有配置
    /config cli.theme        查看当前主题
    /config cli.theme light  切换为亮色主题
```

### 3.3 主题列表

| 主题 | 输出 |
|------|------|
| `help` | 命令列表 |
| `backend` | Backend 用法 |
| `config` | 配置项 + 示例 |
| `memory` | 记忆系统说明 |
| `plugin` | 插件管理 |
| `status` | 状态字段说明 |
| `report` | 报告说明 |

### 3.4 最短形式

```
zmai> /?

  /help  /status  /memory  /backend  /plugin  /config  /report
```

**仅一行。** 适合想快速确认有哪些命令。

---

## 4. /status

### 4.1 默认输出

```
zmai> /status

  ● 项目     my-project · python 3.13
  ● Backend  DeepSeek (deepseek-chat) · 已验证
  ● Git      master · 0 未提交
  ◉ 会话     task#3 · "重构 auth 模块" · [5/8]
  ● Memory   3 条目
  ● Workspace ./workspace/ · 1 活跃 Agent
```

**总是 8 行。** 每行一个状态项，符号 + 标签 + 值。

### 4.2 符号含义

| 符号 | 含义 |
|------|------|
| ● | 正常/活跃/已配置 |
| ○ | 未配置/无数据 |
| ◉ | 需要注意/有未完成任务 |
| ❌ | 错误/不可用 |

### 4.3 状态数据源

```python
def cmd_status(args: list[str]) -> None:
    """/status — 显示当前状态。"""
    t = Theme.dark()

    # 从当前会话上下文读取（非实时检测，避免延迟）
    ctx = get_current_context()

    lines = []

    # 项目
    if ctx.project:
        lines.append(f"  {t.success('●')} {t.dim('项目')}     {ctx.project.name} · {ctx.project.type} {ctx.project.version}")
    else:
        lines.append(f"  {t.dim('○')} {t.dim('项目')}     {t.dim('聊天模式')}")

    # Backend
    if ctx.backend:
        verified = " · 已验证" if ctx.backend_verified else ""
        lines.append(f"  {t.success('●')} {t.dim('Backend')}  {ctx.backend}{verified}")
    else:
        lines.append(f"  {t.error('○')} {t.dim('Backend')}  {t.warning('未配置')}")

    # Git
    if ctx.git_branch:
        modified = f" · {ctx.git_uncommitted} 未提交" if ctx.git_uncommitted else " · 0 未提交"
        lines.append(f"  {t.success('●')} {t.dim('Git')}      {ctx.git_branch}{modified}")
    else:
        lines.append(f"  {t.dim('○')} {t.dim('Git')}      {t.dim('无')}")

    # 会话
    if ctx.session_task:
        status_icon = t.warning("◉") if ctx.session_status in ("running", "paused") else t.success("●")
        progress = f" · [{ctx.session_progress}]" if ctx.session_progress else ""
        lines.append(f"  {status_icon} {t.dim('会话')}     {ctx.session_task}{progress}")
    else:
        lines.append(f"  {t.dim('○')} {t.dim('会话')}     {t.dim('无')}")

    # Memory
    if ctx.memory_entries > 0:
        lines.append(f"  {t.success('●')} {t.dim('Memory')}   {ctx.memory_entries} 条目")
    else:
        lines.append(f"  {t.dim('○')} {t.dim('Memory')}   {t.dim('无')}")

    # Workspace
    if ctx.workspace_root:
        ws = ctx.workspace_root
        active = f" · {ctx.workspace_active} 活跃" if ctx.workspace_active else ""
        lines.append(f"  {t.success('●')} {t.dim('工作区')}  {ws}{active}")
    else:
        lines.append(f"  {t.dim('○')} {t.dim('工作区')}  {t.dim('无')}")

    print("\n".join(lines))
```

---

## 5. /memory

### 5.1 默认输出

```
zmai> /memory

  工作记忆   ○ 当前无活动会话

  项目记忆   ● 3 条目
             上次会话: "重构 auth 模块" (昨天 17:30)
             关键词: auth · jwt · login · middleware

  Agent 记忆 ○ 无持久化记录
```

### 5.2 有活跃记忆时

```
zmai> /memory

  工作记忆   ● 2 条目
             task.focus → "修复 token 刷新"
             last.result → "测试通过 42/45"

  项目记忆   ● 3 条目
             上次会话: "重构 auth 模块" (昨天 17:30)
             关键词: auth · jwt · login · middleware

  Agent 记忆 ● 5 条目
             files.modified: src/auth/login.py
             files.modified: src/auth/middleware.py
             decisions: "使用 JWT 而非 session"
```

### 5.3 数据源

```python
def cmd_memory(args: list[str]) -> None:
    """/memory — 显示记忆摘要。"""
    t = Theme.dark()
    ctx = get_current_context()

    # 工作记忆
    if ctx.working_entries:
        print(f"  {t.success('●')} {t.dim('工作记忆')}   {len(ctx.working_entries)} 条目")
        for ns, entries in ctx.working_entries.items():
            for k, v in list(entries.items())[:3]:  # 最多 3 条
                print(f"             {ns}.{k} → {str(v)[:60]}")
    else:
        print(f"  {t.dim('○')} {t.dim('工作记忆')}   {t.dim('当前无活动会话')}")

    # 项目记忆
    if ctx.project_memory:
        print(f"  {t.success('●')} {t.dim('项目记忆')}   {ctx.project_memory_count} 条目")
        if ctx.last_session:
            print(f"             {t.dim('上次会话:')} {ctx.last_session}")
        if ctx.keywords:
            print(f"             {t.dim('关键词:')} {' · '.join(ctx.keywords)}")
    else:
        print(f"  {t.dim('○')} {t.dim('项目记忆')}   {t.dim('无持久化记录')}")
```

---

## 6. /backend

### 6.1 显示当前 Backend

```
zmai> /backend

  当前: ● DeepSeek (deepseek-chat)

  可用:
    anthropic  ✅ 已验证
    deepseek   ✅ 已验证 (当前)
    openai     ❌ 未配置
    gemini     ❌ 未配置

  切换:
    /backend claude
    /backend deepseek
```

### 6.2 切换 Backend

```
zmai> /backend claude

  ✅ 已切换到 claude (claude-sonnet-4-6)
```

```
zmai> /backend invalid

  ❌ Backend 'invalid' 未配置
  可用 Backend: claude, deepseek
```

### 6.3 数据源

```python
def cmd_backend(args: list[str]) -> None:
    """/backend [name] — 显示或切换 Backend。"""
    t = Theme.dark()
    store = AuthStore()

    if args:
        # 切换
        name = args[0]
        backends = store.list_backends()
        if not any(b["name"] == name for b in backends):
            available = ", ".join(b["name"] for b in backends)
            print(f"  {t.error('❌')} Backend '{name}' 未配置")
            print(f"  {t.dim('可用:')} {available}")
            return

        store.set_active_backend(name)
        info = store.get_backend(name)
        model = f" ({info.get('model', '')})" if info and info.get('model') else ""
        print(f"  {t.success('✅')} 已切换到 {name}{model}")
        return

    # 显示
    backends = store.list_backends()
    active = store.get_active_backend()

    for b in backends:
        is_active = b["name"] == active
        icon = t.success("●") if is_active else t.dim("○")
        model = f"({b.get('model', '')})" if b.get('model') else ""
        verified = t.success("✅ 已验证") if b.get("verified") else t.error("❌ 未配置")
        current = t.dim("(当前)") if is_active else ""
        print(f"  {icon} {t.dim(b['name'])} {model} {verified} {current}")
```

---

## 7. /plugin

### 7.1 输出

```
zmai> /plugin

  已加载: 2 插件

  swe            ● 已加载   软件工程 Agent
  auth           ● 已加载   认证管理
```

```
zmai> /plugin

  已加载: 0 插件

  当前运行时没有加载插件。
  插件在运行时启动时由 Runtime 自动加载。
```

### 7.2 数据源

```python
def cmd_plugin(args: list[str]) -> None:
    """/plugin — 显示已加载的插件。"""
    t = Theme.dark()
    runtime = get_current_runtime()

    plugins = runtime.list_plugins() if hasattr(runtime, "list_plugins") else []

    if not plugins:
        print(f"  {t.dim('已加载: 0 插件')}")
        print()
        print(f"  {t.dim('当前运行时没有加载插件。')}")
        return

    print(f"  {t.info('●')} {t.dim('已加载:')} {len(plugins)} 插件")
    print()

    for p in plugins:
        name = p.get("name", "?")
        status = t.success("● 已加载") if p.get("loaded") else t.dim("○ 未加载")
        desc = p.get("description", "")
        print(f"  {t.dim(name):<16} {status}  {desc}")
```

---

## 8. /config

### 8.1 查看配置

```
zmai> /config

  cli.theme                  dark
  cli.confirm                false
  gateway.default_backend    deepseek

  修改: /config <key> <value>
```

### 8.2 查看单项

```
zmai> /config cli.theme

  cli.theme = dark
```

### 8.3 修改配置

```
zmai> /config cli.theme light

  ✅ cli.theme = light
```

### 8.4 错误

```
zmai> /config nonexistent.key

  ⚠ 未知配置项: nonexistent.key
  可用配置:
    cli.theme
    cli.confirm
    gateway.default_backend
```

### 8.5 数据源

```python
def cmd_config(args: list[str]) -> None:
    """/config [key] [value] — 查看或修改配置。"""
    t = Theme.dark()
    config = get_current_config()

    if not args:
        # 列出所有配置
        data = config.export()
        for k, v in sorted(data.items()):
            print(f"  {t.dim(k):<30} {v}")
        print()
        print(f"  {t.dim('修改: /config <key> <value>')}")
        return

    if len(args) == 1:
        # 查看单项
        key = args[0]
        val = config.get(key)
        if val is not None:
            print(f"  {t.dim(key)} = {val}")
        else:
            print(f"  {t.warning('⚠')} 未知配置项: {key}")
        return

    # 修改
    key, val = args[0], " ".join(args[1:])
    config.set(key, val)
    print(f"  {t.success('✅')} {t.dim(key)} = {val}")
```

---

## 9. /report

### 9.1 有上次任务

```
zmai> /report

  ─── 上次执行报告 ─────────────────────
  任务   "重构 auth 模块"
  状态   ✅ 完成
  步骤   7 步 · 128s
  模型   deepseek-chat · 15.2k tokens

  步骤:
    [1] 📂 读取文件  src/auth/login.py     0.2s
    [2] 🔍 搜索代码  "def login"           0.3s
    [3] ✏️ 写入文件  src/auth/login.py     0.1s
    [4] 🖥️ 执行命令  pytest tests/ -x      45.2s
    [5] 🔍 搜索代码  "token refresh"       0.3s
    [6] ✏️ 写入文件  src/auth/token.py     0.1s
    [7] 🖥️ 执行命令  pytest tests/ -x      12.5s
```

### 9.2 无上次任务

```
zmai> /report

  ⚠ 当前会话中还没有执行过任务。
```

### 9.3 任务失败

```
zmai> /report

  ─── 上次执行报告 ─────────────────────
  任务   "修复数据库连接"
  状态   ❌ 失败
  步骤   3 步 · 15.2s
  错误   Backend API 返回 500

  步骤:
    [1] 📂 读取文件  src/db/config.py      0.1s
    [2] 🔍 搜索代码  "connection"          0.3s
    [3] 🖥️ 执行命令  python db/test.py     14.8s ❌
```

### 9.4 数据源

```python
def cmd_report(args: list[str]) -> None:
    """/report — 显示上次任务报告。"""
    t = Theme.dark()
    ctx = get_current_context()

    report = ctx.get_last_report()
    if not report:
        print(f"  {t.warning('⚠')} 当前会话中还没有执行过任务。")
        return

    status_icon = t.success("✅ 完成") if report.status == "completed" else t.error("❌ 失败")
    print(f"  {t.dim('─── 上次执行报告 ─────────────────────')}")
    print(f"  {t.dim('任务')}    \"{report.task}\"")
    print(f"  {t.dim('状态')}    {status_icon}")
    print(f"  {t.dim('步骤')}    {report.total_steps} 步 · {report.total_time:.0f}s")
    print(f"  {t.dim('模型')}    {report.model or '?'} · {report.total_tokens} tokens")

    if report.error:
        print(f"  {t.dim('错误')}    {report.error}")

    if report.steps:
        print()
        print(f"  {t.dim('步骤:')}")
        for s in report.steps:
            icon = _tool_icon(s.tool)
            elapsed = f"{s.elapsed:.1f}s" if s.elapsed < 60 else f"{s.elapsed/60:.0f}m"
            status = "" if s.success else t.error(" ❌")
            print(f"    [{s.step}] {icon} {s.tool}  {s.detail or ''}  {t.dim(elapsed)}{status}")
```

---

## 10. 文件清单与实现计划

### 10.1 新增文件

```
src/zmai/cli/
└── commands.py             # 🔴 新增 — 7 个命令的实现（~250 行）
    ├── Command             # 命令数据类
    ├── COMMANDS            # 命令注册表
    ├── handle_command()    # REPL 命令路由
    ├── cmd_help()          # /help
    ├── cmd_status()        # /status
    ├── cmd_memory()        # /memory
    ├── cmd_backend()       # /backend
    ├── cmd_plugin()        # /plugin
    ├── cmd_config()        # /config
    └── cmd_report()        # /report
```

### 10.2 修改文件

```
src/zmai/cli/main.py        # 🔧 集成命令路由到 REPL
```

### 10.3 不变文件

```
src/zmai/runtime/*          ✅
src/zmai/gateway/*          ✅
src/zmai/agent/*            ✅
src/zmai/workspace/*        ✅
src/zmai/memory/*           ✅
src/zmai/workflow/*         ✅
src/zmai/swe/*              ✅
src/zmai/cli/formatters.py  ✅
src/zmai/cli/detector.py    ✅
src/zmai/cli/context.py     ✅
```

### 10.4 输出行数保证

```
命令        最大行数    典型行数
─────────────────────────────
/help       15          12 (无参数) / 8 (有主题)
/status     12          8
/memory     10          6 (无记忆) / 10 (有记忆)
/backend    8           6 (显示) / 2 (切换成功) / 3 (切换失败)
/plugin     8           4 (无插件) / 6 (有插件)
/config     6           3 (显示) / 2 (修改) / 4 (错误)
/report     12          10 (有报告) / 2 (无报告)
```

**所有命令 ≤ 15 行。** 没有例外。

### 10.5 实现优先级

```
P0 — 核心命令（1 天）
├── /help          — 命令列表 + 主题帮助
├── /status        — 6 行状态面板
├── /config        — 查看和修改配置
└── /backend       — 显示和切换 Backend

P1 — 扩展命令（0.5 天）
├── /memory        — 三层记忆摘要
├── /report        — 上次任务报告
└── /plugin        — 插件列表
```

---

> **总结：**
>
> ZMAI Help System v1.0 — 7 个短命令，全都不超过 15 行输出：
>
> | 命令 | 最典型输出 |
> |------|-----------|
> | `/help` | 8 行命令列表 + 文档 URL |
> | `/status` | 6 行状态面板 |
> | `/memory` | 6-10 行三层记忆摘要 |
> | `/backend` | 5 行 Backend 列表 |
> | `/plugin` | 3-6 行插件列表 |
> | `/config` | 3 行配置列表 |
> | `/report` | 8 行任务报告 |
>
> **核心约束：每个命令 ≤ 15 行。** 不打印长篇帮助、不刷屏、不展示 ASCII 艺术。
>
> **命名：`commands.py`**（不是 `help.py`），容纳所有 7 个命令的处理逻辑。
