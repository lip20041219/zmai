# ZMAI Startup Design v1.0

Version: 1.0
Date: 2026-07-16

> **`zmai` → 自动恢复一切 → 进入 Agent。** 用户不指定 Workspace。
>
> 不修改 Runtime / Agent / Gateway / Memory / Workspace / Workflow 模块。
>
> 仅修改 CLI 层的启动编排。

---

## 目录

1. [现状审查](#1-现状审查)
2. [设计原则](#2-设计原则)
3. [启动流程全景](#3-启动流程全景)
4. [启动编排器](#4-启动编排器)
5. [阶段 1：环境初始化](#5-阶段-1环境初始化)
6. [阶段 2：认证检测](#6-阶段-2认证检测)
7. [阶段 3：项目检测](#7-阶段-3项目检测)
8. [阶段 4：Workspace 恢复](#8-阶段-4workspace-恢复)
9. [阶段 5：Memory 恢复](#9-阶段-5memory-恢复)
10. [阶段 6：Session 恢复](#10-阶段-6session-恢复)
11. [阶段 7：Backend 选择](#11-阶段-7backend-选择)
12. [阶段 8：Git 状态读取](#12-阶段-8git-状态读取)
13. [阶段 9：Dashboard 展示](#13-阶段-9dashboard-展示)
14. [阶段 10：进入 Agent](#14-阶段-10进入-agent)
15. [文件清单与实现计划](#15-文件清单与实现计划)

---

## 1. 现状审查

### 1.1 当前启动流程

```python
# main.py:354-415 — 当前启动序列

def main(argv):
    _ensure_utf8()
    _inject_auth_credentials()
    args = parse_args()

    if subcommand in ("config", "auth"):
        handle_subcommand(); return

    # 初始化向导
    if no_api_key:
        _run_init_wizard()

    # 项目检测
    root = find_project_root()
    if root:
        os.chdir(root)
        project_ctx = build_context(root)

    # Runtime
    config = Config()
    runtime = Runtime(config)

    # 显示状态
    sys.stderr.write(f"zmai  {project_ctx.summary()}  [{backend}]\n")

    # 执行或 REPL
    if task: _cmd_run(); sys.exit()
    elif tty: _cmd_interactive()
    else: pipe_read()
```

### 1.2 缺失的恢复步骤

| 恢复项 | 当前状态 | 缺失 |
|--------|---------|------|
| **Workspace** | `Runtime.__init__` 中创建新 workspace，不检查已有状态 | ❌ |
| **Memory** | MemoryManager 未在启动时使用 | ❌ |
| **Session** | 仅从 latest.json 读取 task 字符串 | ⚠️ 基础版 |
| **Backend** | `_inject_auth_credentials()` 注入环境变量 | ✅ |
| **Git** | `build_context()` 中包含 git 检测 | ✅ |
| **Init Wizard** | main.py 中内联实现 | ⚠️ 但 UI 简陋 |

### 1.3 根因

当前启动是**串行脚本**而不是**编排系统**。6 件需要做的事（auth/detect/workspace/memory/session/backend）全部混在 `main()` 中，没有分层，没有顺序保证，没有恢复摘要。

---

## 2. 设计原则

### 2.1 自动恢复一切

```
启动 `zmai` 后，自动执行：

  1. 环境初始化    → 检查 Python 版本、~/.zmai 目录
  2. 认证检测      → 环境变量 → OS Keychain → 旧文件 → 初始化向导
  3. 项目检测      → 项目根 → 语言类型 → 工具链
  4. Workspace     → 自动发现已有 workspace → 读取状态
  5. Memory        → 加载项目记忆 → 加载 Agent 长期记忆
  6. Session       → 检测未完成任务 → 准备恢复
  7. Backend       → 选择可用 Backend → 注入凭证
  8. Git           → 读取当前分支和状态
  9. Dashboard     → 展示所有恢复结果
  10. Agent        → 进入 REPL 或执行任务
```

用户不需要指定 Workspace 位置。不需要指定恢复哪个 Session。不需要手动注入任何配置。

### 2.2 失败不阻断

```
每个阶段独立：一个阶段失败不影响后续。

Workspace 损坏？→ 跳过，创建新的
Memory 加载失败？→ 跳过，从零开始
Session 恢复失败？→ 跳过，进入干净 REPL
认证检测失败？→ 进入初始化向导（交互式）
```

### 2.3 恢复可观察

```
启动完成后，Dashboard 展示每个阶段的恢复结果：

  ● 项目     my-project · python 3.13 · pytest
  ● Backend  DeepSeek (deepseek-chat) · 已验证
  ● Git      master · 0 未提交
  ● 会话     "重构 auth 模块" · 暂停 · 可恢复
  ○ Memory   3 条目 · 上次: 昨天 17:30
  ● Workspace ./workspace/ · 1 活跃 Agent
```

`●` = 恢复成功，`○` = 无数据（不影响使用）

### 2.4 不修改下游

```
仅修改:  src/zmai/cli/startup.py  ← 🔴 新增 — 启动编排
         src/zmai/cli/main.py      ← 🔧 重写 — 调用启动编排

不修改:  src/zmai/runtime/*     ✗
         src/zmai/gateway/*     ✗
         src/zmai/agent/*       ✗
         src/zmai/workspace/*   ✗  （仅读取已有 API）
         src/zmai/memory/*      ✗  （仅读取已有 API）
         src/zmai/workflow/*    ✗
         src/zmai/swe/*         ✗
```

---

## 3. 启动流程全景

### 3.1 时序图

```
用户: $ zmai
        │
        ▼
    main()
        │
        ┌── 阶段 1: 环境初始化 ──────────────────────────┐
        │  ● 检查 Python 版本 >= 3.10                    │
        │  ● 确保 ~/.zmai/ 目录结构存在                    │
        │  ● 设置 UTF-8 编码                              │
        │  ● 注册信号处理 (SIGINT/SIGTERM)                │
        └─────────────────────────────────────────────────┘
        │
        ┌── 阶段 2: 认证检测 ────────────────────────────┐
        │  1. 环境变量 → 有? → 使用（跳过向导）            │
        │  2. OS Keychain → 有? → 注入环境变量             │
        │  3. 旧凭证文件 → 有? → 迁移到 OS Keychain        │
        │  4. 都没有 → 初始化向导                          │
        │     ├─ 用户完成向导 → 继续                       │
        │     └─ 用户跳过   → 进入有限功能模式              │
        └─────────────────────────────────────────────────┘
        │
        ┌── 阶段 3: 项目检测 ────────────────────────────┐
        │  ● find_project_root() → 找到项目根              │
        │  ● build_context()     → 语言/工具链/结构        │
        │  ● 未找到 → chat 模式                           │
        └─────────────────────────────────────────────────┘
        │
        ┌── 阶段 4: Workspace 恢复 ──────────────────────┐
        │  ● 自动发现 workspace 目录                       │
        │  ● 读取 state.json → 活跃 Agent                 │
        │  ● 读取 manifest.json → 文件清单                │
        └─────────────────────────────────────────────────┘
        │
        ┌── 阶段 5: Memory 恢复 ─────────────────────────┐
        │  ● 加载 Project Memory（项目级上下文）            │
        │  ● 加载 Long-term Memory（Agent 知识）          │
        └─────────────────────────────────────────────────┘
        │
        ┌── 阶段 6: Session 恢复 ────────────────────────┐
        │  ● 检查 latest.json → 未完成任务?                │
        │  ● 读取会话状态 → 步骤/焦点/修改的文件           │
        └─────────────────────────────────────────────────┘
        │
        ┌── 阶段 7: Backend 选择 ────────────────────────┐
        │  ● 选择默认 Backend                             │
        │  ● 验证连接（后台，不阻塞）                       │
        └─────────────────────────────────────────────────┘
        │
        ┌── 阶段 8: Git 状态 ────────────────────────────┐
        │  ● 读取当前分支                                 │
        │  ● 读取未提交修改数量                            │
        └─────────────────────────────────────────────────┘
        │
        ┌── 阶段 9: Dashboard 展示 ──────────────────────┐
        │  ● 所有阶段结果汇总到 StartupResult             │
        │  ● 渲染 Dashboard（6 行状态面板）                │
        └─────────────────────────────────────────────────┘
        │
        ┌── 阶段 10: 进入 Agent ─────────────────────────┐
        │  ├─ 有未完成任务 → 询问是否恢复                  │
        │  │   恢复 → 构建恢复 Prompt → Runtime.run()     │
        │  │   跳过 → 进入 REPL                          │
        │  ├─ 有单次任务 → Runtime.run() → exit          │
        │  └─ 无 → REPL                                 │
        └─────────────────────────────────────────────────┘
```

### 3.2 启动结果

```python
@dataclass
class StartupResult:
    """10 个阶段启动的完整结果。每个阶段独立。"""

    # 阶段 1: 环境
    env_ready: bool = False
    zmai_dir: Path | None = None

    # 阶段 2: 认证
    auth_status: str = ""        # "ok" | "wizard_done" | "skipped"
    backend_name: str = ""
    backend_model: str = ""

    # 阶段 3: 项目
    project_ctx: ProjectContext | None = None

    # 阶段 4: Workspace
    workspace_root: Path | None = None
    workspace_active_agents: int = 0
    workspace_files: int = 0

    # 阶段 5: Memory
    memory_entries: int = 0
    memory_agent_id: str = ""

    # 阶段 6: Session
    session_id: str = ""
    session_task: str = ""
    session_status: str = ""     # "running" | "paused" | "completed"
    session_progress: str = ""   # "5/8 步"

    # 阶段 7: Backend
    backend_ready: bool = False

    # 阶段 8: Git
    git_branch: str = ""
    git_uncommitted: int = 0

    # 阶段 9: Dashboard 已展示
    dashboard_shown: bool = False

    # 诊断
    startup_time_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def has_unfinished_session(self) -> bool:
        return bool(self.session_id) and self.session_status in ("running", "paused")
```

---

## 4. 启动编排器

### 4.1 核心编排器

```python
# cli/startup.py — 启动编排器

class StartupOrchestrator:
    """10 阶段启动编排器。每个阶段独立执行。"""

    def __init__(self):
        self._theme = Theme.dark()
        self._result = StartupResult()
        self._start_time = time.time()

    async def run(self, args: argparse.Namespace) -> StartupResult:
        """执行全部 10 个启动阶段。"""

        with self._phase("Environment"):
            self._result.env_ready = self._init_environment()

        with self._phase("Auth"):
            auth_result = self._resolve_auth()
            self._result.auth_status = auth_result["status"]
            self._result.backend_name = auth_result.get("name", "")
            self._result.backend_model = auth_result.get("model", "")

        with self._phase("Project"):
            self._result.project_ctx = self._detect_project()

        with self._phase("Workspace"):
            ws = self._restore_workspace(self._result.project_ctx)
            if ws:
                self._result.workspace_root = ws["root"]
                self._result.workspace_active_agents = ws["active_agents"]
                self._result.workspace_files = ws["files"]

        with self._phase("Memory"):
            mem = self._restore_memory(self._result.project_ctx)
            if mem:
                self._result.memory_entries = mem["entries"]
                self._result.memory_agent_id = mem.get("agent_id", "")

        with self._phase("Session"):
            ses = self._restore_session()
            if ses:
                self._result.session_id = ses.get("id", "")
                self._result.session_task = ses.get("task", "")
                self._result.session_status = ses.get("status", "")
                self._result.session_progress = ses.get("progress", "")

        with self._phase("Backend"):
            self._result.backend_ready = self._select_backend(
                self._result.backend_name)

        with self._phase("Git"):
            if self._result.project_ctx:
                self._result.git_branch = self._result.project_ctx.git_branch
                self._result.git_uncommitted = (
                    1 if self._result.project_ctx.git_has_uncommitted else 0)

        # 阶段 9: Dashboard
        self._result.startup_time_ms = (time.time() - self._start_time) * 1000
        self._show_dashboard(self._result)

        return self._result

    def _phase(self, name: str):
        """阶段上下文管理器。捕获异常不阻断后续。"""
        class PhaseGuard:
            def __enter__(self2):
                return self2
            def __exit__(self2, exc_type, exc_val, exc_tb):
                if exc_val:
                    self._result.errors.append(f"{name}: {exc_val}")
                    # 不阻断 —— 打印 error 后继续
                    sys.stderr.write(
                        f"  ⚠ {name} 失败: {exc_val}\n")
                    return True  # 抑制异常
        return PhaseGuard()
```

### 4.2 与 main() 的集成

```python
# main.py — 重写

def main(argv=None):
    argv = argv or sys.argv[1:]

    # 子命令 — 直接路由
    if argv and argv[0] in ("config", "auth", "doctor", "init"):
        _run_subcommand(argv[0], argv[1:])
        return

    # 单次执行
    if argv and not argv[0].startswith("-"):
        task = " ".join(argv)
        _run_with_startup(task)
        return

    # 标志
    if argv:
        args = _parse_flags(argv)
        if args.version:
            print_version(); return
        if args.help:
            print_help(); return
        if args.task:
            _run_with_startup(args.task); return
        return

    # 使用标志后进入 REPL
    args = _parse_flags(argv) if argv else argparse.Namespace()
    _run_repl_with_startup(args)


def _run_with_startup(task: str) -> None:
    """带完整启动序列的单次执行。"""
    asyncio.run(_startup_and_run(task=task, is_repl=False))


def _run_repl_with_startup(args) -> None:
    """带完整启动序列的 REPL。"""
    asyncio.run(_startup_and_run(task=None, is_repl=True, args=args))


async def _startup_and_run(task=None, is_repl=False, args=None):
    """启动 + 执行 完整流程。"""

    # 启动编排器
    orchestrator = StartupOrchestrator()
    result = await orchestrator.run(args or argparse.Namespace())

    # 获取 Runtime
    runtime = Runtime(config=Config())
    _register_swe_tools(runtime)

    # 阶段 10: 进入 Agent
    if result.has_unfinished_session() and is_repl:
        # 询问是否恢复
        should_resume = _prompt_resume(result)
        if should_resume:
            # 带恢复上下文的单次执行
            task = _build_resume_prompt(result)
            _cmd_run(task, runtime, Config(), args or {})
            # 执行完后进入 REPL
            _cmd_interactive(runtime, Config(), args or {})
        else:
            _cmd_interactive(runtime, Config(), args or {})
    elif task:
        _cmd_run(task, runtime, Config(), args or {})
    elif is_repl:
        _cmd_interactive(runtime, Config(), args or {})
```

---

## 5. 阶段 1：环境初始化

### 5.1 检查清单

```python
def _init_environment(self) -> bool:
    """阶段 1: 环境初始化。"""
    # 1. 编码
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # 2. ~/.zmai/ 目录结构
    zmai_dir = Path.home() / ".zmai"
    for subdir in ("", "sessions", "memory/projects", "memory/agents",
                   "cache", "error_reports"):
        (zmai_dir / subdir).mkdir(parents=True, exist_ok=True)

    self._result.zmai_dir = zmai_dir

    # 3. 信号处理
    _setup_signal_handlers()

    # 4. 崩溃保护
    import zmai.cli.error_handler as eh
    eh.crash_guard()

    return True


def _setup_signal_handlers():
    """注册信号处理器。"""
    import signal

    def _on_sigint(signum, frame):
        """Ctrl+C: 由 REPL 处理。"""
        raise KeyboardInterrupt()

    def _on_sigterm(signum, frame):
        """SIGTERM: 安全退出。"""
        sys.stderr.write("\n  ⚠ 收到终止信号，正在保存...\n")
        # 这里由 checkpoint manager 处理持久化
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_sigint)
    signal.signal(signal.SIGTERM, _on_sigterm)
```

### 5.2 输出

```
无用户可见输出。静默完成。
```

---

## 6. 阶段 2：认证检测

### 6.1 检测链

```python
def _resolve_auth(self) -> dict:
    """阶段 2: 认证检测。四层检测链。"""

    # 第 1 层: 环境变量（最高优先级）
    for name, var, model_var in [
        ("anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"),
        ("deepseek",  "DEEPSEEK_API_KEY",  "DEEPSEEK_MODEL"),
        ("openai",    "OPENAI_API_KEY",    "OPENAI_MODEL"),
        ("gemini",    "GEMINI_API_KEY",    "GEMINI_MODEL"),
    ]:
        if os.environ.get(var):
            return {
                "status": "ok",
                "name": name,
                "model": os.environ.get(model_var, ""),
                "source": "env",
            }

    # 第 2 层: OS Keychain
    try:
        from zmai.auth import AuthStore
        store = AuthStore()
        backends = store.list_backends()
        if backends:
            active = store.get_active_backend()
            info = store.get_backend(active) if active else None
            if info and info.get("api_key"):
                # 注入到环境变量（Gateway 层读取环境变量）
                os.environ[f"{active.upper()}_API_KEY"] = info["api_key"]
                if info.get("model"):
                    os.environ[f"{active.upper()}_MODEL"] = info["model"]
                return {
                    "status": "ok",
                    "name": active,
                    "model": info.get("model", ""),
                    "source": "keychain",
                }
    except Exception:
        pass

    # 第 3 层: 旧凭证文件 → 迁移
    # (由 AuthStore 自动处理)

    # 第 4 层: 都没有 → 初始化向导
    if sys.stdin.isatty() and sys.stderr.isatty():
        return self._run_init_wizard()

    return {"status": "skipped", "source": "none"}
```

### 6.2 初始化向导

```
  ╭─ ⚡ ZMAI v0.1.0 — 首次启动 ───────────────────────╮
  │                                                      │
  │  未检测到 API Key。需要至少一个 Backend 才能使用。      │
  │                                                      │
  │  选择一个 Backend 配置:                               │
  │    [1] Claude     (claude-sonnet-4-6)                │
  │    [2] DeepSeek   (deepseek-chat)                    │
  │    [3] OpenAI     (gpt-4o)                           │
  │    [4] Google Gemini (gemini-2.0-flash)              │
  │    [s] 跳过 (有限功能模式)                             │
  │                                                      │
  │  选择 [1-4, 默认 2]:                                 │
  ╰──────────────────────────────────────────────────────╯
```

### 6.3 有限功能模式

用户选择跳过时：

```
  ╭─ ⚠ 有限功能模式 ──────────────────────────────────╮
  │                                                      │
  │  未配置 Backend。以下功能不可用:                       │
  │  ❌ Agent 任务执行                                    │
  │  ✅ 项目检测                                          │
  │  ✅ REPL 交互                                         │
  │  ✅ 查看状态                                          │
  │                                                      │
  │  随时可以通过以下命令配置:                              │
  │  $ zmai auth                                         │
  │                                                      │
  ╰──────────────────────────────────────────────────────╯
```

---

## 7. 阶段 3：项目检测

### 7.1 检测

```python
def _detect_project(self) -> ProjectContext | None:
    """阶段 3: 项目检测。"""

    root = find_project_root()
    if not root:
        return None

    try:
        os.chdir(str(root))
    except OSError:
        return None

    ctx = build_context(root)
    return ctx
```

### 7.2 输出

```python
# 检测完成。结果在 StartupResult.project_ctx 中。
# 无用户可见输出（在 Dashboard 中展示）。
```

---

## 8. 阶段 4：Workspace 恢复

### 8.1 恢复逻辑

```python
def _restore_workspace(self, ctx: ProjectContext | None) -> dict | None:
    """阶段 4: Workspace 自动发现和恢复。

    不要求用户指定 Workspace 路径。
    自动扫描候选目录，读取已有状态。
    """
    if not ctx or not ctx.root:
        return None

    # 候选路径（按优先级）
    candidates = [
        ctx.root / "workspace",
        ctx.root / ".zmai" / "workspace",
        ctx.root / "agent_workspace",
    ]

    for ws_path in candidates:
        if not ws_path.exists():
            continue

        # 检查 workspace 状态
        state_file = ws_path / "state.json"
        if not state_file.exists():
            continue

        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        # 读取活跃 Agent 数量
        agents = state.get("agents", {})
        active_count = sum(
            1 for a in agents.values()
            if isinstance(a, dict) and a.get("status") == "active"
        )

        # 读取文件总数（从 manifest 或估算）
        file_count = 0
        manifest_file = ws_path / "manifest.json"
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                file_count = manifest.get("total_files", 0)
            except Exception:
                pass

        self._result.workspace_root = ws_path

        return {
            "root": ws_path,
            "active_agents": active_count,
            "files": file_count,
        }

    # 未找到现有 workspace → 使用默认路径（不创建）
    default_ws = ctx.root / "workspace"
    self._result.workspace_root = default_ws

    return {
        "root": default_ws,
        "active_agents": 0,
        "files": 0,
    }
```

### 8.2 不要求用户指定

```
当前：
  zmai.json 中的 workspace.root 是可选配置，不是必要条件。

没有 zmai.json？
  → 自动扫描 ./workspace/ / .zmai/workspace/ / agent_workspace/
  → 找到就用，找不到用默认

有 workspace 但无 state.json？
  → 视为空 workspace，启动时由 Runtime 创建

用户始终不需要指定 Workspace。
```

---

## 9. 阶段 5：Memory 恢复

### 9.1 恢复逻辑

```python
def _restore_memory(self, ctx: ProjectContext | None) -> dict | None:
    """阶段 5: Memory 恢复。

    使用 MemoryManager 的已有 API 恢复项目级和 Agent 级记忆。
    """
    if not ctx:
        return None

    try:
        from zmai.memory import MemoryManager

        mm = MemoryManager(
            long_term_root=str(Path.home() / ".zmai" / "memory"),
        )

        # 恢复项目记忆
        project_name = ctx.name
        if mm.exists(project_name):
            mm.restore(project_name)
            wm = mm.working(project_name)

            # 统计条目数
            entry_count = sum(
                len(ns) for ns in wm._data.values()
            )

            self._result.memory_entries = entry_count
            self._result.memory_agent_id = project_name

            return {
                "entries": entry_count,
                "agent_id": project_name,
            }
    except Exception:
        pass

    return None
```

### 9.2 输出

```
无直接输出。结果将在 Dashboard 中展示:

  ● Memory   3 条目 · 上次: "重构 auth 模块"
  ○ Memory   无历史记录
```

---

## 10. 阶段 6：Session 恢复

### 10.1 恢复逻辑

```python
def _restore_session(self) -> dict | None:
    """阶段 6: Session 恢复。

    读取 ~/.zmai/sessions/latest.json，检测未完成任务。
    """
    session_file = Path.home() / ".zmai" / "sessions" / "latest.json"
    if not session_file.exists():
        return None

    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
    except Exception:
        return None

    session_id = data.get("session_id", "")

    # 读取完整会话数据
    session_path = Path.home() / ".zmai" / "sessions" / f"{session_id}.json"
    if not session_path.exists():
        # 向后兼容：旧格式只有 task 字段
        task = data.get("task", "")
        if task:
            return {
                "id": "",
                "task": task,
                "status": "paused",
                "progress": "",
            }
        return None

    try:
        session = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    status = session.get("status", "")
    if status not in ("running", "paused"):
        return None  # 已完成的任务不恢复

    steps = session.get("steps_completed", 0)
    total = session.get("total_steps", 0)
    progress = f"{steps}/{total}" if total > 0 else ""

    return {
        "id": session_id,
        "task": session.get("task", ""),
        "status": status,
        "progress": progress,
        "files_modified": session.get("files_modified", []),
        "current_focus": session.get("current_focus", ""),
    }
```

### 10.2 输出

```
  ╭─ 📋 检测到未完成任务 ─────────────────────────────╮
  │                                                      │
  │  任务: 重构 auth 模块                                 │
  │  进度: 5/8 步                                        │
  │  焦点: 正在修复 token 刷新逻辑                         │
  │  文件: src/auth/login.py · src/auth/middleware.py     │
  │                                                      │
  │  恢复并继续？[Y/n]:                                   │
  │                                                      │
  ╰──────────────────────────────────────────────────────╯
```

---

## 11. 阶段 7：Backend 选择

### 11.1 选择逻辑

```python
def _select_backend(self, preferred: str) -> bool:
    """阶段 7: Backend 选择。

    按优先级选择可用 Backend：
      1. 环境变量显式指定（ANTHROPIC_API_KEY 等）
      2. 当前已激活的 Backend
      3. 任何已配置的 Backend
    """
    from zmai.gateway import BackendRegistry

    registry = BackendRegistry()

    # 尝试注册环境变量可用的 Backend
    for name, var, cls_path, cls_name in [
        ("claude",   "ANTHROPIC_API_KEY",
         "zmai.gateway.backends.claude",   "ClaudeBackend"),
        ("deepseek", "DEEPSEEK_API_KEY",
         "zmai.gateway.backends.deepseek", "DeepSeekBackend"),
    ]:
        if os.environ.get(var):
            try:
                mod = __import__(cls_path, fromlist=[cls_name])
                cls = getattr(mod, cls_name)
                registry.register(name, cls)
            except Exception:
                continue

    # 选择默认
    if preferred and preferred in registry.list():
        registry.set_default(preferred)
        return True

    if registry.list():
        registry.set_default(registry.list()[0])
        return True

    return False
```

---

## 12. 阶段 8：Git 状态读取

### 12.1 读取逻辑

```python
# Git 状态已经在 ProjectContext 中由 GitDetector 读取。
# 阶段 8 只是将其提取到 StartupResult。

# 在编排器中：
self._result.git_branch = ctx.git_branch if ctx else ""
self._result.git_uncommitted = 1 if (ctx and ctx.git_has_uncommitted) else 0
```

---

## 13. 阶段 9：Dashboard 展示

### 13.1 Dashboard 渲染

```python
def _show_dashboard(self, result: StartupResult) -> None:
    """阶段 9: Dashboard 展示。"""
    t = self._theme
    w = _terminal_width() - 4

    lines = [
        f"  ╭{'─' * w}╮",
        f"  │  {t.highlight('⚡ zmai')} {t.dim(f'v0.1.0 · {result.startup_time_ms:.0f}ms')}"
        f"{' ' * (w - len(f'v0.1.0 · {result.startup_time_ms:.0f}ms') - 10)}│",
        f"  │  {t.dim('─' * (w - 2))}  │",
        "",
    ]

    # 项目
    if result.project_ctx:
        status = t.success("●")
        text = f"{result.project_ctx.name}"
        if result.project_ctx.type != "unknown":
            text += f" · {result.project_ctx.type}"
            if result.project_ctx.language_version:
                text += f" {result.project_ctx.language_version}"
        if result.project_ctx.test_framework:
            text += f" · {result.project_ctx.test_framework}"
    else:
        status = t.dim("○")
        text = "未检测到项目（聊天模式）"

    lines.append(
        f"  │  {t.dim('项目')}  {status} {text}"
        f"{' ' * (w - len(text) - 8)}│"
    )

    # Backend
    if result.backend_ready:
        label = t.success("●")
        text = f"{result.backend_name or '已配置'}"
        if result.backend_model:
            text += f" ({result.backend_model})"
    elif result.auth_status == "skipped":
        label = t.warning("○")
        text = "未配置"
    else:
        label = t.dim("○")
        text = "检测中..."
    lines.append(
        f"  │  {t.dim('Backend')}{label} {text}"
        f"{' ' * (w - len(text) - 10)}│"
    )

    # Git
    if result.git_branch:
        status = t.success("●")
        text = f"{result.git_branch}"
        if result.git_uncommitted > 0:
            text += f" · {t.warning(f'{result.git_uncommitted} 未提交')}"
        else:
            text += " · 0 未提交"
    else:
        status = t.dim("○")
        text = "-"
    lines.append(
        f"  │  {t.dim('Git')}   {status} {text}"
        f"{' ' * (w - len(text) - 8)}│"
    )

    # 会话
    if result.has_unfinished_session():
        status = t.warning("◉")
        text = f"{result.session_task}"
        if result.session_progress:
            text += f" · [{result.session_progress}]"
    else:
        status = t.dim("○")
        text = "无未完成会话"
    lines.append(
        f"  │  {t.dim('会话')}  {status} {text}"
        f"{' ' * (w - len(text) - 8)}│"
    )

    # Memory
    if result.memory_entries > 0:
        status = t.info("●")
        text = f"{result.memory_entries} 条目"
    else:
        status = t.dim("○")
        text = "无历史记录"
    lines.append(
        f"  │  {t.dim('记忆')}  {status} {text}"
        f"{' ' * (w - len(text) - 8)}│"
    )

    # Workspace
    if result.workspace_root:
        status = t.success("●") if result.workspace_active_agents > 0 else t.dim("○")
        text = f"{result.workspace_root.name}"
        if result.workspace_active_agents > 0:
            text += f" · {result.workspace_active_agents} 活跃"
    else:
        status = t.dim("○")
        text = "-"
    lines.append(
        f"  │  {t.dim('工作区')} {status} {text}"
        f"{' ' * (w - len(text) - 9)}│"
    )

    # 关闭面板
    lines += [
        "",
        f"  │  {t.dim('─' * (w - 2))}  │",
        f"  │  {t.dim('输入自然语言描述任务 · /help 查看命令 · exit 退出')}"
        f"{' ' * (w - 52)}│",
        f"  ╰{'─' * w}╯",
        "",
    ]

    # 错误摘要
    if result.errors:
        lines.append(f"  {t.warning(f'⚠ {len(result.errors)} 个阶段有警告')}")

    sys.stderr.write("\n".join(lines))
    result.dashboard_shown = True
```

### 13.2 展示效果

```
  ╭──────────────────────────────────────────────────────────╮
  │  ⚡ zmai v0.1.0 · 87ms                                    │
  │  ──────────────────────────────────────────────────────── │
  │                                                           │
  │  项目     ● my-project · python 3.13 · pytest             │
  │  Backend  ● DeepSeek (deepseek-chat)                      │
  │  Git      ● master · 0 未提交                             │
  │  会话     ◉ 重构 auth 模块 · [5/8]                        │
  │  记忆     ○ 无历史记录                                     │
  │  工作区   ○ workspace                                     │
  │                                                           │
  │  ──────────────────────────────────────────────────────── │
  │  输入自然语言描述任务 · /help 查看命令 · exit 退出          │
  ╰──────────────────────────────────────────────────────────╯
```

---

## 14. 阶段 10：进入 Agent

### 14.1 进入决策

```python
async def _enter_agent(result: StartupResult) -> None:
    """阶段 10: 进入 Agent。"""

    runtime = Runtime(config=Config())
    _register_swe_tools(runtime)

    if result.has_unfinished_session():
        # 有未完成任务 → 询问用户
        resume = _prompt_resume(result)
        if resume:
            _cmd_run_with_context(runtime, result)
        else:
            _clear_session_marker()

    # REPL
    _cmd_interactive(runtime, Config(), args={})
```

### 14.2 恢复确认

```python
def _prompt_resume(result: StartupResult) -> bool:
    """询问用户是否恢复上次任务。"""
    t = Theme.dark()
    sys.stderr.write(
        f"\n  {t.warning('◉')} 检测到上次未完成的任务:\n"
        f"    {t.highlight(result.session_task)}\n"
        f"    进度: {result.session_progress}\n"
    )

    if result.session_focus:
        sys.stderr.write(f"    焦点: {result.session_focus}\n")

    try:
        ans = input(f"\n  {t.info('恢复并继续？')} [Y/n]: ").strip().lower()
        return ans in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False
```

### 14.3 恢复 Prompt

```python
def _build_resume_prompt(result: StartupResult) -> str:
    """构建带恢复上下文的 Prompt。"""
    lines = ["继续之前的任务。"]

    if result.session_task:
        lines.append(f"任务: {result.session_task}")

    if result.session_progress:
        lines.append(f"进度: {result.session_progress}")

    if result.session_focus:
        lines.append(f"当前焦点: {result.session_focus}")

    if result.session_files_modified:
        lines.append("已修改的文件:")
        for f in result.session_files_modified:
            lines.append(f"  - {f}")

    return "\n".join(lines)
```

---

## 15. 文件清单与实现计划

### 15.1 新增文件

```
src/zmai/cli/
└── startup.py              # 🔴 新增 — 启动编排器（~300 行）
    ├── StartupResult        # 启动结果数据结构
    ├── StartupOrchestrator  # 10 阶段启动编排器
    ├── _init_environment()  # 阶段 1
    ├── _resolve_auth()      # 阶段 2
    ├── _detect_project()    # 阶段 3
    ├── _restore_workspace() # 阶段 4
    ├── _restore_memory()    # 阶段 5
    ├── _restore_session()   # 阶段 6
    ├── _select_backend()    # 阶段 7
    ├── _show_dashboard()    # 阶段 9
    └── _enter_agent()       # 阶段 10
```

### 15.2 修改文件

```
src/zmai/cli/main.py         # 🔧 重写 — 调用 StartupOrchestrator
                              # 从 420 行精简为 ~150 行
```

### 15.3 不变文件

```
src/zmai/runtime/*             ✅ 不变
src/zmai/gateway/*             ✅ 不变
src/zmai/agent/*               ✅ 不变
src/zmai/workspace/*           ✅ 不变（仅读取已有状态文件）
src/zmai/memory/*              ✅ 不变（仅调用已有 Manager API）
src/zmai/workflow/*            ✅ 不变
src/zmai/swe/*                 ✅ 不变
src/zmai/cli/progress.py       ✅ 不变
src/zmai/cli/detector.py       ✅ 不变
src/zmai/cli/context.py        ✅ 不变
src/zmai/cli/formatters.py     ✅ 不变
src/zmai/cli/detectors/*       ✅ 不变
src/zmai/auth/*                ✅ 不变
src/zmai/errors/*              ✅ 不变
```

### 15.4 代码量变化

```
新增:
  cli/startup.py             ~300 行

修改:
  cli/main.py                ~270 行精简（420 → 150）

净变化:    +180 行
```

### 15.5 实现优先级

```
P0 — 启动编排框架（1 天）
├── StartupResult 数据结构
├── StartupOrchestrator 框架（阶段管理 + 错误隔离）
├── main.py 重写（调用编排器）
└── 阶段 1: 环境初始化

P1 — 恢复阶段（1 天）
├── 阶段 2: 认证检测链 + 初始化向导
├── 阶段 3: 项目检测（已有，编排集成）
├── 阶段 4: Workspace 恢复
├── 阶段 5: Memory 恢复
├── 阶段 6: Session 恢复
└── 阶段 7: Backend 选择

P2 — 完成体验（0.5 天）
├── 阶段 8: Git 状态
├── 阶段 9: Dashboard 展示
├── 阶段 10: 进入 Agent + 恢复确认
└── 恢复 Prompt 构建
```

### 15.6 依赖关系

```
阶段             依赖上游              阻塞启动？
─────────────────────────────────────────────────
1. 环境初始化    无                     ❌ 不阻塞
2. 认证检测      无                     ✅ 阻塞（无 Backend 则无 Agent）
3. 项目检测      无                     ❌ 不阻塞
4. Workspace     3 (项目根)             ❌ 不阻塞
5. Memory        3 (项目名)             ❌ 不阻塞
6. Session       无                     ❌ 不阻塞
7. Backend       2 (凭证)               ✅ 阻塞（必须有 Backend 才能执行任务）
8. Git           3 (项目根)             ❌ 不阻塞
9. Dashboard     1-8 全部               ❌ 最后展示
10. 进入 Agent   7 (Backend)            ✅ 阻塞（无 Backend 则进入有限模式）
```

---

> **总结：**
>
> ZMAI Startup v1.0 将当前 420 行的串行 `main()` 重构为 10 阶段的编排系统：
>
> **10 个阶段，每个阶段独立、错误隔离、失败不阻断后续：**
>
> | 阶段 | 功能 | 阻塞？ | 失败时 |
> |------|------|--------|--------|
> | 1 | 环境初始化 | ❌ | 警告，继续 |
> | 2 | 认证检测 | ✅ | 进入向导或有限模式 |
> | 3 | 项目检测 | ❌ | 聊天模式 |
> | 4 | Workspace 恢复 | ❌ | 创建新 workspace |
> | 5 | Memory 恢复 | ❌ | 从零开始 |
> | 6 | Session 恢复 | ❌ | 无恢复提示 |
> | 7 | Backend 选择 | ✅ | 有限功能模式 |
> | 8 | Git 状态 | ❌ | 不显示 |
> | 9 | Dashboard | ❌ | 单行回退 |
> | 10 | 进入 Agent | ✅ | 退出 |
>
> **用户不需要指定 Workspace** — 自动扫描 3 个候选目录，找不到则用默认路径
> **用户不需要指定任何配置** — 认证自动检测，项目自动识别，Session 自动恢复
> **Dashboard 展示 6 项恢复状态** — 项目/Backend/Git/会话/记忆/工作区，各带彩色指示器
> **恢复确认** — 检测到未完成任务时询问"恢复并继续？"，是则构建恢复 Prompt 注入 Agent
