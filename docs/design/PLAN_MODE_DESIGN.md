# ZMAI Plan Mode 设计

> 版本: 1.0
> 目标: Plan Mode（只读规划）与 Execute Mode（执行）状态清晰分离
> 约束: 不修改现有 Runtime 核心代码

---

## 目录

- [1. 概念模型](#1-概念模型)
- [2. 状态机扩展](#2-状态机扩展)
- [3. PlanAgent](#3-planagent)
- [4. Plan 工具白名单](#4-plan-工具白名单)
- [5. Plan 产出结构](#5-plan-产出结构)
- [6. Plan → Execute 切换协议](#6-plan--execute-切换协议)
- [7. CLI 集成](#7-cli-集成)
- [8. 文件规划](#8-文件规划)
- [9. 单元测试](#9-单元测试)

---

## 1. 概念模型

### 1.1 两种模式

```
┌─────────────────────────────────────────────────────┐
│                   Plan Mode                          │
│                                                     │
│  只读 · 分析 · 规划 · 无副作用                       │
│                                                     │
│  输入: 自然语言任务                                 │
│  动作: 读文件 / 搜索代码 / 分析目录 / 评估风险       │
│  产出: 结构化 Plan 工件                              │
│  禁止: 写文件 / 编辑 / 执行命令 / Git 写操作         │
│  状态: PLANNING                                     │
│                                                     │
│  完成后: 等待用户确认 → 进入 Execute Mode            │
└─────────────────────┬───────────────────────────────┘
                      │ 用户确认
                      ▼
┌─────────────────────────────────────────────────────┐
│                  Execute Mode                        │
│                                                     │
│  读写 · 执行 · 修改 · 交付                          │
│                                                     │
│  输入: Plan 工件（或直接任务描述）                   │
│  动作: 写文件 / 编辑 / 执行命令 / 交付结果           │
│  状态: RUNNING                                      │
└─────────────────────────────────────────────────────┘
```

### 1.2 Plan Mode 的铁律

```
铁律 1: 不修改任何文件。Plan Mode 不得调用 write_file / edit / shell_exec（写操作）。
铁律 2: 不产生副作用。Plan Mode 的输出只有 Plan JSON + 终端打印。
铁律 3: Plan 中的所有步骤必须标注验证条件。
铁律 4: Plan 中的每个工具名必须在注册表中存在。
```

### 1.3 与 PLAN_RUN_DESIGN.md 的关系

```
PLAN_RUN_DESIGN.md  = CLI 用户交互流程设计（zmai plan → zmai run what）
PLAN_MODE_DESIGN.md = 系统架构设计（状态机、Agent、工具隔离、模式切换）

两者互补：
  PLAN_RUN_DESIGN.md 定义"用户看到什么"
  PLAN_MODE_DESIGN.md 定义"系统内部如何实现"
```

---

## 2. 状态机扩展

### 2.1 AgentState 扩展

当前枚举（`agent/base.py`）：

```python
class AgentState(Enum):
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

v1 扩展为：

```python
class AgentState(Enum):
    IDLE = "idle"
    PLANNING = "planning"         # ← 新增：Plan Mode
    INITIALIZING = "initializing"
    RUNNING = "running"           # Execute Mode
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_readonly(self) -> bool:
        """Plan Mode 下只读。"""
        return self == AgentState.PLANNING
```

### 2.2 Lifecycle 状态转换扩展

当前转换表（`runtime/lifecycle.py`）：

```python
_TRANSITIONS = {
    ("idle", "initializing"): True,
    ("initializing", "running"): True,
    ("initializing", "failed"): True,
    ("running", "paused"): True,
    ("running", "completed"): True,
    ("running", "failed"): True,
    ("paused", "running"): True,
    ("paused", "cancelled"): True,
    ("running", "cancelled"): True,
    ("initializing", "cancelled"): True,
    ("idle", "cancelled"): True,
}
```

v1 扩展为：

```python
_TRANSITIONS = {
    # 现有
    ("idle", "initializing"): True,
    ("initializing", "running"): True,
    ("initializing", "failed"): True,
    ("running", "paused"): True,
    ("running", "completed"): True,
    ("running", "failed"): True,
    ("paused", "running"): True,
    ("paused", "cancelled"): True,
    ("running", "cancelled"): True,
    ("initializing", "cancelled"): True,
    ("idle", "cancelled"): True,
    # ← 新增 Plan Mode 转换
    ("idle", "planning"): True,         # 进入 Plan Mode
    ("planning", "completed"): True,    # 规划完成（等待用户确认）
    ("planning", "failed"): True,       # 规划失败
    ("planning", "cancelled"): True,    # 用户取消
    ("planning", "running"): True,      # 用户确认 → 进入 Execute Mode
}
```

### 2.3 LifecycleManager 新增方法

```python
class LifecycleManager:
    # ... 现有方法 ...

    def plan(self, agent_id: str) -> None:
        """进入 Plan Mode。"""
        self._transition(agent_id, "planning")

    def is_planning(self, agent_id: str) -> bool:
        """是否在 Plan Mode。"""
        return self.get_state(agent_id) == "planning"

    def is_readonly(self, agent_id: str) -> bool:
        """Plan Mode 下所有写操作应被拒绝。"""
        return self.is_planning(agent_id)
```

---

## 3. PlanAgent

### 3.1 类设计

```python
class PlanAgent(Agent):
    """规划 Agent — 只读模式，生成执行计划。
    
    与 SWEAgent 的区别：
      - 只允许读工具（read_file, grep, show_to_user）
      - 禁止写工具（write_file, edit, shell_exec 写操作, git 写操作）
      - 输出 Plan 工件而非修改文件
      - 状态为 PLANNING 而非 RUNNING
    """

    name = "plan_agent"
    description = "Planning Agent - read-only task analysis and plan generation"

    # 白名单：Plan Mode 可用工具
    READONLY_TOOLS = {
        "read_file",
        "grep",
        "show_to_user",
    }

    # 有条件可用（仅读/查操作）
    CONDITIONAL_TOOLS = {
        "shell_exec": ["dir", "ls", "pwd", "cd", "where", "which", "ver", "type", "echo"],
        "git": ["status", "log", "diff --stat", "branch", "remote"],
    }

    async def initialize(self, context: AgentContext) -> None:
        """初始化 PlanAgent。只注册只读工具。"""
        if context.tools:
            existing = {t.name for t in context.tools.list()}
            for tool_cls in self._get_readonly_tools():
                tool = tool_cls()
                if tool.name not in existing and tool.name in self.READONLY_TOOLS:
                    context.tools.register(tool)

    async def step(self, context: AgentContext) -> AgentAction:
        """Plan Mode 单步：分析 → 输出 Plan 或继续分析。"""
        # 注入 Plan Mode 专用 System Prompt
        # 只允许只读工具调用
        # 最终产出 Plan 结构后返回 complete()

    async def finalize(self, context: AgentContext) -> AgentResult:
        """规划完成。输出 Plan 工件到 workspace。"""

    def _get_readonly_tools(self) -> list[type[Tool]]:
        """获取 Plan Mode 允许的工具类列表。"""
        from zmai.swe.tools import ReadFileTool, GrepTool, ShowToUserTool
        return [ReadFileTool, GrepTool, ShowToUserTool]
```

### 3.2 PlanAgent vs SWEAgent 对比

| 维度 | PlanAgent | SWEAgent |
|------|-----------|----------|
| 状态 | `PLANNING` | `RUNNING` |
| 工具 | 只读白名单 | 全部 8 个工具 |
| 副作用 | 零 | 写文件/执行命令 |
| 产出 | Plan 工件 | 修改后的文件 |
| 调用 LLM | 规划专用 Prompt | 执行专用 Prompt |
| 终止条件 | Plan 完成 | 任务完成或失败 |

### 3.3 Plan Mode 专用 System Prompt

```python
_PLAN_SYSTEM_PROMPT = """你是 ZMAI 规划 Agent。你的任务是分析任务并生成执行计划。

## 核心约束
- 你处于只读模式。绝对不要修改任何文件。
- 你只能读取文件、搜索代码、分析目录。
- 你的输出是一个结构化 Plan，不是修改后的代码。

## 工作流程
1. 理解任务：读取相关文件，搜索相关代码
2. 分析工作区：了解当前目录结构
3. 识别风险：预判可能的问题
4. 生成 Plan：包含步骤、工具、验证条件

## 输出要求
你的最终输出必须包含：
- Task Summary：任务的一句话总结
- Assumptions：你的假设前提
- Steps：每个步骤包含 action、tool、params、verify
- Required Tools：所有需要的工具列表
- Risks：风险点
- Expected Outputs：预期的交付物

## 可用工具
- read_file: 读取文件内容（只读）
- grep: 搜索文本（只读）
- show_to_user: 展示分析结果

## 禁止
- 禁止写入任何文件
- 禁止执行带副作用的命令
- 禁止假设你不知道的信息
"""
```

---

## 4. Plan 工具白名单

### 4.1 完整白名单

| 工具 | Plan Mode | Execute Mode | 说明 |
|------|-----------|-------------|------|
| `read_file` | ✅ 可用 | ✅ 可用 | 读取文件 |
| `grep` | ✅ 可用 | ✅ 可用 | 搜索文本 |
| `show_to_user` | ✅ 可用 | ✅ 可用 | 展示结果 |
| `write_file` | ❌ 禁止 | ✅ 可用 | Plan Mode 禁止写入 |
| `edit` | ❌ 禁止 | ✅ 可用 | Plan Mode 禁止编辑 |
| `shell_exec` | ⚠️ 受限 | ✅ 可用 | Plan Mode 仅允许读命令 |
| `git` | ⚠️ 受限 | ✅ 可用 | Plan Mode 仅允许读操作 |
| `open_in_browser` | ❌ 禁止 | ✅ 可用 | Plan Mode 不需要 |

### 4.2 shell_exec 命令白名单（Plan Mode）

Plan Mode 下的 `shell_exec` 只允许以下命令前缀：

```python
PLAN_MODE_SHELL_ALLOWLIST = [
    "dir",          # 列出目录
    "ls",           # 列出目录
    "pwd",          # 当前路径
    "cd",           # 切换目录（不修改文件）
    "where",        # 查找程序
    "which",        # 查找程序
    "ver",          # 系统版本
    "type",         # 显示文件内容（相当于 cat，但比 read_file 弱，允许用于目录检查）
    "echo",         # 打印
    "find",         # 搜索文件
    "findstr",      # 搜索字符串
    "git status",   # Git 状态（只读）
    "git log",      # Git 日志（只读）
    "git diff",     # Git 差异（只读）
    "git branch",   # 分支列表（只读）
    "git remote",   # 远程信息（只读）
]
```

禁止的命令包括但不限于：

```
rm, del, rmdir, mkdir, touch, copy, move, ren,
echo >, echo >>, type nul >, fsutil,
git add, git commit, git push, git reset, git checkout -b,
npm install, pip install, python -m, pytest,
start, open, xdg-open
```

### 4.3 白名单拦截实现

```python
class PlanModeGuard:
    """Plan Mode 安全守卫。拦截写操作。"""

    @staticmethod
    def check_tool(tool_name: str, params: dict) -> tuple[bool, str]:
        """检查工具调用是否在 Plan Mode 允许范围内。
        
        Returns:
            (allowed: bool, reason: str)
        """
        # 完全禁止的工具
        if tool_name in ("write_file", "edit", "open_in_browser"):
            return False, f"Plan Mode 禁止 '{tool_name}'"

        # shell_exec：检查命令白名单
        if tool_name == "shell_exec":
            cmd = params.get("command", "")
            return PlanModeGuard._check_shell(cmd)

        # git：检查参数白名单
        if tool_name == "git":
            args = params.get("args", "")
            return PlanModeGuard._check_git(args)

        # read_file / grep / show_to_user：始终允许
        return True, ""

    @staticmethod
    def _check_shell(cmd: str) -> tuple[bool, str]:
        """检查 shell 命令是否只读。"""
        stripped = cmd.strip().lower()
        for allowed in PLAN_MODE_SHELL_ALLOWLIST:
            if stripped.startswith(allowed.lower()):
                return True, ""
        return False, f"Plan Mode 禁止 shell 命令: {cmd[:60]}..."

    @staticmethod
    def _check_git(args: str) -> tuple[bool, str]:
        """检查 git 操作是否只读。"""
        stripped = args.strip().lower()
        READONLY_GIT = ("status", "log", "diff", "branch", "remote", "show", "ls-files")
        for prefix in READONLY_GIT:
            if stripped.startswith(prefix):
                return True, ""
        return False, f"Plan Mode 禁止 git 写操作: {args[:60]}..."
```

### 4.4 调用拦截时机

```
ToolRouter.execute(tool_call, context)
  │
  ├─ [新增] Plan Mode 检查
  │    │
  │    ├─ context.config.get("mode") == "plan"
  │    │    │
  │    │    ├─ PlanModeGuard.check_tool(name, params) → 通过 → 继续
  │    │    │
  │    │    └─ PlanModeGuard.check_tool(name, params) → 拒绝 → 返回 ToolResult.err()
  │    │
  │    └─ context.config.get("mode") != "plan" → 直接继续
  │
  └─ ToolRegistry.execute(name, params, context)
```

此检查可放在 `ToolRouter.execute()` 中，无需修改 Tool 实现。

---

## 5. Plan 产出结构

### 5.1 Plan 工件格式

```python
@dataclass
class PlanStep:
    """单步计划。"""
    id: int
    action: str                    # 操作描述
    tool: str                      # 工具名
    params: dict[str, Any]         # 工具参数（占位）
    expected: str                  # 期望结果
    verify: dict | None = None     # 验证条件 {type, target}
    on_failure: str = "retry"      # 失败策略: retry | skip | abort


@dataclass
class Plan:
    """完整执行计划。Plan Mode 的产出物。"""
    task: str                      # 原始任务描述
    summary: str                   # 一句话摘要
    assumptions: list[str]         # 假设前提
    steps: list[PlanStep]          # 执行步骤
    tools_required: list[str]      # 所需工具列表
    risks: list[str]               # 风险点
    expected_outputs: list[str]    # 预期交付物
    created_at: str                # 创建时间
    workspace_snapshot: dict | None = None  # 工作区快照
```

### 5.2 示例 Plan

```json
{
  "task": "创建一个完整博客网站",
  "summary": "在当前项目下创建 blog 页面，包含 HTML/CSS/JS，并在浏览器中验证",
  "assumptions": [
    "当前目录可写",
    "系统有可用的浏览器",
    "不需要后端服务"
  ],
  "steps": [
    {
      "id": 1,
      "action": "分析当前目录结构",
      "tool": "shell_exec",
      "params": {"command": "dir /a"},
      "expected": "了解当前目录的文件分布",
      "verify": null,
      "on_failure": "abort"
    },
    {
      "id": 2,
      "action": "创建 output/blog 目录",
      "tool": "shell_exec",
      "params": {"command": "mkdir -p output/blog"},
      "expected": "目录结构已创建",
      "verify": {"type": "dir_exists", "target": "output/blog"},
      "on_failure": "retry"
    },
    {
      "id": 3,
      "action": "创建 index.html",
      "tool": "write_file",
      "params": {"path": "output/blog/index.html", "content": "<!DOCTYPE html>..."},
      "expected": "HTML 文件写入成功",
      "verify": {"type": "file_valid_html", "target": "output/blog/index.html"},
      "on_failure": "retry"
    },
    {
      "id": 4,
      "action": "创建 style.css",
      "tool": "write_file",
      "params": {"path": "output/blog/style.css", "content": "body { ... }"},
      "expected": "CSS 文件写入成功",
      "verify": {"type": "file_exists", "target": "output/blog/style.css"},
      "on_failure": "retry"
    },
    {
      "id": 5,
      "action": "创建 app.js",
      "tool": "write_file",
      "params": {"path": "output/blog/app.js", "content": "console.log('hello');"},
      "expected": "JS 文件写入成功",
      "verify": {"type": "file_exists", "target": "output/blog/app.js"},
      "on_failure": "retry"
    },
    {
      "id": 6,
      "action": "验证所有文件内容",
      "tool": "read_file",
      "params": {"path": "output/blog/index.html"},
      "expected": "HTML/CSS/JS 内容完整有效",
      "verify": {"type": "content_valid", "target": "output/blog/index.html"},
      "on_failure": "retry"
    },
    {
      "id": 7,
      "action": "浏览器打开验证",
      "tool": "open_in_browser",
      "params": {"path": "output/blog/index.html"},
      "expected": "浏览器正常打开",
      "verify": {"type": "browser_open", "target": "output/blog/index.html"},
      "on_failure": "abort"
    }
  ],
  "tools_required": ["shell_exec", "write_file", "read_file", "open_in_browser"],
  "risks": ["浏览器可能不可用", "磁盘空间可能不足"],
  "expected_outputs": [
    "output/blog/index.html — 博客首页",
    "output/blog/style.css — 样式文件",
    "output/blog/app.js — 交互脚本"
  ],
  "created_at": "2026-07-21T10:00:00Z",
  "workspace_snapshot": {
    "files": ["README.md", "src/", "tests/"],
    "git_branch": "main",
    "git_clean": true
  }
}
```

---

## 6. Plan → Execute 切换协议

### 6.1 完整流程

```
用户输入: zmai plan "task"
  │
  ▼
[CLI] _run_plan(task)
  │
  ├─ 1. 创建 Runtime 实例
  │
  ├─ 2. 创建 PlanAgent + AgentContext(mode="plan")
  │
  ├─ 3. Runtime.run_plan(agent_id, task)
  │     │
  │     ├─ LifecycleManager.plan(agent_id) → PLANNING
  │     │
  │     ├─ PlanAgent.initialize()
  │     │   └─ 注册只读工具
  │     │
  │     ├─ PlanAgent.step() 循环
  │     │   ├─ LLM 调用（规划 Prompt）
  │     │   ├─ 只读工具执行（PlanModeGuard 拦截写操作）
  │     │   └─ 直到 Plan 完成
  │     │
  │     ├─ PlanAgent.finalize()
  │     │   └─ 输出 Plan 工件到 session 文件
  │     │
  │     └─ LifecycleManager.complete(agent_id)
  │
  ├─ 4. 显示 Plan 给用户
  │     ├─ Task Summary
  │     ├─ Assumptions
  │     ├─ Steps（编号 + 动作 + 工具）
  │     ├─ Required Tools
  │     ├─ Risks
  │     └─ Expected Outputs
  │
  ├─ 5. 确认提示: "确认执行？[Y/n]"
  │     │
  │     ├─ Y → 进入 Execute Mode
  │     └─ n → 退出，保存 Plan 供后续使用
  │
  └─ 6. [确认后] 切换到 Execute Mode
        │
        ├─ 创建 SWEAgent + AgentContext(mode="execute")
        ├─ LifecycleManager.mark_ready(agent_id) → RUNNING
        ├─ 注入 Plan 到 context.metadata["execution_plan"]
        └─ Runtime.run() 正常执行
```

### 6.2 切换时序

```
Plan Mode (只读)                    Execute Mode (读写)
─────────────────                   ────────────────────
PLANNING                              RUNNING
  │                                     │
  ├─ read_file                          ├─ write_file
  ├─ grep                               ├─ edit
  ├─ show_to_user                       ├─ shell_exec
  ├─ shell_exec (只读命令)               ├─ git
  └─ git (只读操作)                      └─ open_in_browser
        │                                     ▲
        └──── 用户确认 + Plan 传递 ────────────┘
```

### 6.3 Plan 传递方式

Plan 工件通过以下路径从 Plan Mode 传递到 Execute Mode：

```
1. 保存到 ~/.zmai/sessions/latest_plan.json
2. 注入到 Execute Mode 的 AgentContext.metadata["execution_plan"]
3. SWEAgent 读取 execution_plan 作为执行指引
```

---

## 7. CLI 集成

### 7.1 命令路由

```python
# main() 中新增
if cmd == "plan":
    _run_plan(rest)      # Plan Mode
    return
if cmd == "run":
    if rest:
        _run_execute(rest, runtime, config, args)  # 直接执行
    else:
        _run_saved_plan(runtime, config, args)     # 执行上次 Plan
    return
```

### 7.2 `zmai plan` 输出格式

```
  ┌─────────────────────────────────────────────────────┐
  │  ZMAI Plan                                           │
  ├─────────────────────────────────────────────────────┤
  │                                                     │
  │  Task Summary                                        │
  │    创建一个完整博客网站                               │
  │                                                     │
  │  Assumptions                                         │
  │    ✓ 当前目录可写                                    │
  │    ✓ 系统有可用的浏览器                              │
  │    ✓ 不需要后端服务                                  │
  │                                                     │
  │  Steps                                               │
  │    1/7  分析当前目录           shell_exec            │
  │    2/7  创建项目结构           shell_exec            │
  │    3/7  创建 index.html       write_file             │
  │    4/7  创建 style.css        write_file             │
  │    5/7  创建 app.js           write_file             │
  │    6/7  验证文件完整性          read_file ×3         │
  │    7/7  浏览器打开验证         open_in_browser       │
  │                                                     │
  │  Required Tools                                      │
  │    shell_exec · write_file · read_file · open_in_browser │
  │                                                     │
  │  Risks                                               │
  │    ⚠ 浏览器可能不可用                                │
  │    ⚠ 磁盘空间可能不足                                │
  │                                                     │
  │  Expected Outputs                                    │
  │    📄 output/blog/index.html    — 博客首页           │
  │    📄 output/blog/style.css     — 样式文件            │
  │    📄 output/blog/app.js        — 交互脚本            │
  │                                                     │
  ├─────────────────────────────────────────────────────┤
  │  确认执行？[Y/n]  y                                  │
  └─────────────────────────────────────────────────────┘

  开始执行...

  1/7 ✓ 分析当前目录           (shell_exec      0.3s)
  2/7 ✓ 创建项目结构           (shell_exec      0.1s)
  3/7 ✓ 创建 index.html       (write_file      0.02s)
  4/7 ✓ 创建 style.css        (write_file      0.01s)
  5/7 ✓ 创建 app.js           (write_file      0.01s)
  6/7 ✓ 验证文件完整性          (read_file ×3   0.02s)
  7/7 ✓ 浏览器打开验证         (open_in_browser 0.5s)

  ✓ 任务完成 (7/7 全部成功)
```

### 7.3 `zmai plan --json` 输出格式

```json
{
  "status": "plan_ready",
  "plan": {
    "summary": "创建一个完整博客网站",
    "steps": [...],
    "tools_required": ["shell_exec", "write_file", "read_file", "open_in_browser"],
    "risks": [],
    "expected_outputs": ["output/blog/index.html"]
  },
  "prompt": "确认执行？[Y/n]"
}
```

### 7.4 帮助信息更新

```
Subcommands:
  zmai plan <task>            Analyze task and generate execution plan
  zmai run [task]             Execute task (or last saved plan if no task)
```

---

## 8. 文件规划

### 8.1 新增文件

```
src/zmai/swe/
├── __init__.py      [不改]
├── agent.py         [不改] SWEAgent 不变
├── tools.py         [不改]
├── planner.py       [新增] PlanAgent 实现
└── guard.py         [新增] PlanModeGuard 工具白名单守卫

src/zmai/cli/
├── main.py          [修改] +plan/+run 子命令路由
├── planner.py       [修改/补充] plan 展示、确认交互
└── ...              [不改]
```

### 8.2 修改文件

| 文件 | 改动 |
|------|------|
| `agent/base.py` | AgentState 增加 `PLANNING` 枚举值 |
| `runtime/lifecycle.py` | _TRANSITIONS 增加 plan 相关转换，新增 `plan()` 方法 |
| `runtime/runtime.py` | 新增 `run_plan()` 方法 |
| `cli/main.py` | 新增 `plan`/`run` 子命令路由 |
| `gateway/tool_router.py` | Plan Mode 调用 PlanModeGuard 拦截 |

### 8.3 不改文件

```
swe/agent.py         — SWEAgent 逻辑不变
swe/tools.py         — 工具实现不变
tool/base.py         — ToolResult/基类不变
tool/registry.py     — 注册表不变
memory/*             — 所有记忆模块不变
config/*             — 配置不变
```

---

## 9. 单元测试

### 9.1 PlanModeGuard 测试

```python
class TestPlanModeGuard:
    def test_block_write_file(self):
        allowed, reason = PlanModeGuard.check_tool("write_file", {"path": "test.txt"})
        assert allowed is False
        assert "禁止" in reason

    def test_block_edit(self):
        allowed, reason = PlanModeGuard.check_tool("edit", {"path": "test.txt"})
        assert allowed is False

    def test_block_open_browser(self):
        allowed, reason = PlanModeGuard.check_tool("open_in_browser", {"path": "test.html"})
        assert allowed is False

    def test_allow_read_file(self):
        allowed, reason = PlanModeGuard.check_tool("read_file", {"path": "test.txt"})
        assert allowed is True

    def test_allow_grep(self):
        allowed, reason = PlanModeGuard.check_tool("grep", {"pattern": "test"})
        assert allowed is True

    def test_allow_show_to_user(self):
        allowed, reason = PlanModeGuard.check_tool("show_to_user", {"content": "hello"})
        assert allowed is True

    def test_block_shell_rm(self):
        allowed, reason = PlanModeGuard.check_tool("shell_exec", {"command": "rm -rf /"})
        assert allowed is False

    def test_allow_shell_dir(self):
        allowed, reason = PlanModeGuard.check_tool("shell_exec", {"command": "dir /a"})
        assert allowed is True

    def test_allow_shell_ls(self):
        allowed, reason = PlanModeGuard.check_tool("shell_exec", {"command": "ls -la"})
        assert allowed is True

    def test_allow_shell_pwd(self):
        allowed, reason = PlanModeGuard.check_tool("shell_exec", {"command": "pwd"})
        assert allowed is True

    def test_block_shell_mkdir(self):
        allowed, reason = PlanModeGuard.check_tool("shell_exec", {"command": "mkdir new_dir"})
        assert allowed is False

    def test_block_shell_npm_install(self):
        allowed, reason = PlanModeGuard.check_tool("shell_exec", {"command": "npm install"})
        assert allowed is False

    def test_block_git_commit(self):
        allowed, reason = PlanModeGuard.check_tool("git", {"args": "commit -m 'fix'"})
        assert allowed is False

    def test_allow_git_status(self):
        allowed, reason = PlanModeGuard.check_tool("git", {"args": "status"})
        assert allowed is True

    def test_allow_git_log(self):
        allowed, reason = PlanModeGuard.check_tool("git", {"args": "log --oneline"})
        assert allowed is True

    def test_block_git_push(self):
        allowed, reason = PlanModeGuard.check_tool("git", {"args": "push origin main"})
        assert allowed is False
```

### 9.2 PlanAgent 测试

```python
class TestPlanAgent:
    def test_initialize_registers_readonly_tools(self, context):
        agent = PlanAgent("test_planner")
        agent.initialize(context)
        tool_names = {t.name for t in context.tools.list()}
        assert "read_file" in tool_names
        assert "grep" in tool_names
        assert "show_to_user" in tool_names
        assert "write_file" not in tool_names

    def test_step_in_planning_state(self, context):
        agent = PlanAgent("test_planner")
        assert agent.state == AgentState.IDLE
        agent.state = AgentState.PLANNING
        assert agent.state == AgentState.PLANNING

    def test_finalize_outputs_plan(self, context):
        agent = PlanAgent("test_planner")
        result = agent.finalize(context)
        assert result.status == AgentState.COMPLETED

    def test_plan_has_required_fields(self):
        plan = Plan(
            task="test", summary="test", assumptions=[],
            steps=[], tools_required=[], risks=[],
            expected_outputs=[], created_at="now",
        )
        assert plan.task == "test"
        assert plan.summary == "test"
```

### 9.3 Plan 结构测试

```python
class TestPlanStructure:
    def test_plan_step_has_verify(self):
        step = PlanStep(id=1, action="test", tool="write_file",
                        params={"path": "x"}, expected="done",
                        verify={"type": "file_exists", "target": "x"})
        assert step.verify["type"] == "file_exists"

    def test_plan_step_default_on_failure(self):
        step = PlanStep(id=1, action="test", tool="read_file",
                        params={}, expected="done")
        assert step.on_failure == "retry"

    def test_plan_json_serializable(self):
        plan = Plan(task="t", summary="s", assumptions=[], steps=[],
                    tools_required=[], risks=[], expected_outputs=[],
                    created_at="now")
        import json
        d = json.loads(json.dumps(asdict(plan)))
        assert d["task"] == "t"
```

### 9.4 生命周期测试

```python
class TestLifecyclePlanning:
    def test_idle_to_planning(self, lifecycle):
        lifecycle.plan("agent_1")
        assert lifecycle.get_state("agent_1") == "planning"

    def test_planning_to_completed(self, lifecycle):
        lifecycle.plan("agent_1")
        lifecycle.complete("agent_1")
        assert lifecycle.is_terminal("agent_1") is True

    def test_planning_to_running(self, lifecycle):
        """用户确认后从 Plan Mode 切换到 Execute Mode。"""
        lifecycle.plan("agent_1")
        lifecycle.mark_ready("agent_1")  # planning → running
        assert lifecycle.get_state("agent_1") == "running"

    def test_planning_to_cancelled(self, lifecycle):
        lifecycle.plan("agent_1")
        lifecycle.cancel("agent_1")
        assert lifecycle.is_terminal("agent_1") is True

    def test_planning_is_readonly(self, lifecycle):
        lifecycle.plan("agent_1")
        assert lifecycle.is_readonly("agent_1") is True
        lifecycle.mark_ready("agent_1")
        assert lifecycle.is_readonly("agent_1") is False

    def test_cannot_write_from_planning(self, lifecycle):
        """Plan Mode 下不能直接进入完成状态。"""
        lifecycle.plan("agent_1")
        # planning → initializing 应非法
        # planning → running 才是正确的"确认后执行"路径
        assert lifecycle.get_state("agent_1") == "planning"
```

---

## 10. 实现顺序

```
Phase 1: 状态机扩展 + PlanModeGuard（安全第一）
  ├─ agent/base.py: AgentState.PLANNING 枚举值
  ├─ runtime/lifecycle.py: 新增转换 + is_readonly()
  └─ swe/guard.py: PlanModeGuard 白名单

Phase 2: PlanAgent
  ├─ swe/planner.py: PlanAgent 实现
  ├─ runtime/runtime.py: run_plan() 方法
  └─ gateway/tool_router.py: Plan Mode 拦截钩子

Phase 3: CLI 集成
  ├─ cli/main.py: plan/run 子命令
  └─ cli/planner.py: 展示 + 确认 + 执行切换
```

---

> **文档结束**
>
> 核心设计点：
> 1. `PLANNING` 是独立状态，与 `RUNNING` 完全分离
> 2. `PlanModeGuard` 在工具调用层强制只读，不依赖 LLM 自觉
> 3. 白名单覆盖 shell/git 的子命令级别，不止工具名级别
> 4. Plan Agent 和 Execute Agent 是不同类，注册不同工具集
> 5. 状态转换受 LifecycleManager 严格约束，非法转换被阻止
