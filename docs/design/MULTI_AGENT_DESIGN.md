# ZMAI Multi-Agent Design

> 设计日期: 2026-07-17
> 状态: 设计阶段（未实现）
> 概念: Manager Agent、Worker Agent、SubAgent、Agent Orchestration

---

## 一、设计目标

1. **Manager/Worker 模式** — Manager Agent 分解任务，Worker Agent 并行执行
2. **SubAgent 生命周期** — Agent 可创建子 Agent，子 Agent 完成后父 Agent 获取结果
3. **层级隔离** — 每层 Agent 有独立 workspace、memory、tool 权限
4. **结果聚合** — Manager 自动汇总 Worker 结果

---

## 二、当前 Agent 架构回顾

**现状**：单个 `SWEAgent`，单层执行：

```
Runtime.run(agent_id, task)
  └── SWEAgent(agent_id)
       └── step() → AgentAction
            ├── "continue" → 继续
            ├── "complete" → 结束
            └── "fail"     → 报错

所有 agent_id 是扁平字符串：agent_12345
无父子关系，无层级，无并行。
```

**设计差距**：

| 能力 | 当前 | 目标 |
|------|------|------|
| Agent 类型 | 仅 SWEAgent | ManagerAgent / WorkerAgent / 自定义 |
| 层级 | 扁平 agent_id | `parent/child` 层级 |
| 并行 | Scheduler 简单限制 | Worker 池 + 结果汇聚 |
| 通信 | 无 | SubAgentResult 通道 |
| 工具隔离 | 全局 ToolRegistry | 按角色过滤工具 |

---

## 三、核心概念

### 3.1 Agent 层级

```
Manager Agent
  ├── agent_id: "task_001"
  │
  ├── Worker Agent 1
  │   └── agent_id: "task_001/w_1"
  │       └── 独立 workspace + memory
  │
  ├── Worker Agent 2
  │   └── agent_id: "task_001/w_2"
  │
  └── Worker Agent N
      └── agent_id: "task_001/w_N"
```

### 3.2 三种 Agent 角色

| 角色 | 类 | 职责 | 工具集 |
|------|-----|------|--------|
| **Manager** | `ManagerAgent` | 分解任务、分配 Worker、聚合结果 | Planner + 只读工具 |
| **Worker** | `WorkerAgent` | 执行具体子任务 | 读写工具 + Shell |
| **SubAgent** | `SubAgent` (通用) | 被其他 Agent 动态创建 | 由父 Agent 指定 |

### 3.3 AgentID 层级

使用 `/` 分隔的层级结构：

```
"root"                      ← 顶级 Agent
"root/planner"              ← Manager 的子 Agent
"root/planner/w_1"          ← Worker
"root/planner/w_2"          ← Worker
"root/planner/w_2/helper"   ← Worker 又创建了 SubAgent
```

Workspace 路径自动映射：

```
workspace/
├── root/                          ← 顶级 Agent
├── root__planner/                 ← 子 Agent（/ → __）
├── root__planner__w_1/
└── root__planner__w_2/
```

> Agent ID 中的 `/` 在文件路径中转换为 `__`。Workspace 的 `_agent_path()` 验证逻辑需添加对 `/` 的支持（当前拒绝 `/`）。

---

## 四、ManagerAgent

```python
# agent/manager.py（新增）

class ManagerAgent(Agent):
    """Manager Agent：分解任务，分配 Worker，聚合结果。"""

    name = "manager_agent"
    description = "Task decomposition and worker coordination"

    async def initialize(self, context: AgentContext) -> None:
        """加载 Manager 专用工具（无读写工具，只有规划工具）。"""
        context.tools.register(DecomposeTool())   # 任务分解
        context.tools.register(AggregateTool())   # 结果聚合
        # Manager 不注册 ReadFile/WriteFile/Shell

    async def step(self, context: AgentContext) -> AgentAction:
        """根据任务复杂度决定：直接完成或分配 Worker。"""
        # 1. 调用 Backend 分析任务
        # 2. 如果任务可分解：
        #    a. 返回 AgentAction 包含 subtasks
        #    b. Runtime 据此创建 Worker Agent
        # 3. 如果任务简单：直接返回 complete
        ...

    async def finalize(self, context: AgentContext) -> AgentResult:
        """汇总所有 Worker 结果。"""
        ...
```

### 4.1 任务分解流程

```
Manager.step()
  │
  ├── "写一个 Python Web 服务器"
  │
  ├── 分解为子任务:
  │   ├── w_1: "创建项目结构"
  │   ├── w_2: "实现路由层"
  │   └── w_3: "添加测试"
  │
  ├── Runtime 为每个子任务创建 Worker
  │   └── 等待全部完成
  │
  └── 聚合结果 → "complete"
```

### 4.2 工具限制

Manager Agent 只有规划和只读工具：

```
Manager 可用工具:
  ├── decompose_task     ← 将任务分解为子任务列表
  ├── aggregate_results  ← 合并 Worker 输出
  └── read_file          ← 只读，检查 Worker 产物

Manager 不可用:
  ❌ write_file          ← 不直接写文件
  ❌ shell_exec          ← 不执行命令
  ❌ git                 ← 不操作 git
```

---

## 五、WorkerAgent

```python
# agent/worker.py（新增）

class WorkerAgent(Agent):
    """Worker Agent：执行被分配的子任务。"""

    name = "worker_agent"
    description = "Executes subtasks assigned by Manager"

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self._subtask: str = ""       # 父 Agent 分配的任务
        self._parent_id: str = ""     # 父 Agent ID

    async def initialize(self, context: AgentContext) -> None:
        """Worker 有完整的工具集。"""
        # 注册全部 8 个 SWE 工具
        ...

    async def step(self, context: AgentContext) -> AgentAction:
        """执行子任务，与 SWEAgent 类似但输出会被 Manager 聚合。"""
        ...
```

### 5.1 Worker 生命周期

```
Runtime 收到 Manager 的 subtasks:
  │
  ├── 为每个 subtask 创建 WorkerAgent
  │   ├── agent_id = "manager_id/w_1"
  │   ├── task = subtask.description
  │   └── parent_id = manager_id
  │
  ├── 并行执行（受 Scheduler max_concurrent 限制）
  │
  └── 全部完成后通知 Manager
```

### 5.2 Worker 输出契约

Worker 的输出必须是结构化的，以便 Manager 聚合：

```python
@dataclass
class WorkerOutput:
    """Worker 的标准输出格式。"""
    agent_id: str
    subtask: str
    status: Literal["completed", "failed"]
    summary: str              # 简短摘要（Manager 可读）
    files_modified: list[str] # 修改的文件列表
    output: str               # 完整输出
    error: str | None = None
```

---

## 六、SubAgent 机制

SubAgent 是更通用的概念——任何 Agent 可以在执行过程中创建子 Agent：

```python
# agent/sub_agent.py（新增）

class SubAgentMixin:
    """混入类，使 Agent 具备创建 SubAgent 的能力。"""

    async def spawn_subagent(
        self,
        context: AgentContext,
        task: str,
        agent_cls: type[Agent] = SWEAgent,
        tool_names: list[str] | None = None,
    ) -> WorkerOutput:
        """创建 SubAgent 并等待结果。

        Args:
            context: 当前 Agent 的上下文
            task: 子任务描述
            agent_cls: Agent 类（默认 SWEAgent）
            tool_names: 允许 SubAgent 使用的工具列表（None=全部）

        Returns:
            WorkerOutput 标准结果
        """
        sub_id = f"{context.agent_id}/sub_{context.step_count}"
        # Runtime 创建新的 Agent 实例
        # 限制工具集（如果指定了 tool_names）
        # 设置独立的 workspace 子目录
        # 等待完成，返回 WorkerOutput
```

### 6.1 工具隔离

SubAgent 的工具集可由父 Agent 限制：

```python
# 父 Agent 创建只读 SubAgent
result = await self.spawn_subagent(
    context,
    task="读取 config.json 的内容",
    tool_names=["read_file", "grep"],  # 只能读，不能写
)

# 默认：全部工具
result = await self.spawn_subagent(
    context,
    task="实现用户登录功能",
    # tool_names=None → 8 个工具全可用
)
```

### 6.2 深度限制

防止无限递归 SubAgent：

```python
MAX_SUBAGENT_DEPTH = 5

def _check_depth(agent_id: str) -> bool:
    """检查 SubAgent 层级深度。"""
    depth = agent_id.count("/")
    return depth < MAX_SUBAGENT_DEPTH
```

---

## 七、Runtime 多 Agent 支持

### 7.1 Runtime.run() 扩展

```python
class Runtime:
    async def run(
        self,
        agent_id: str,
        task: str,
        backend: str | None = None,
        agent_cls: type[Agent] = SWEAgent,  # ← 新增：Agent 类型
        parent_id: str | None = None,        # ← 新增：父 Agent
        tool_names: list[str] | None = None, # ← 新增：工具限制
        ...
    ) -> dict[str, Any]:
        # 1. agent_id 层级验证
        # 2. 检查 SubAgent 深度
        # 3. 创建指定类型的 Agent
        agent = agent_cls(agent_id)

        # 4. 如果指定了 tool_names，创建受限 ToolRegistry
        if tool_names is not None:
            tools = ToolRegistry()
            for name in tool_names:
                try:
                    tools.register(self._tools.get(name))
                except ToolError:
                    pass
            ctx.tools = tools

        # 5. 执行（同现有逻辑）
        ...
```

### 7.2 SubAgent 执行流程

```python
# Runtime 新增方法
async def run_subagent(
    self,
    parent_id: str,
    task: str,
    agent_cls: type[Agent] = SWEAgent,
    tool_names: list[str] | None = None,
) -> WorkerOutput:
    """运行 SubAgent 并返回结构化结果。"""
    sub_id = f"{parent_id}/sub_{uuid4().hex[:8]}"

    if sub_id.count("/") > MAX_SUBAGENT_DEPTH:
        return WorkerOutput(
            agent_id=sub_id, subtask=task,
            status="failed", summary="SubAgent 深度超限",
            files_modified=[], output="", error="max depth exceeded"
        )

    result = await self.run(
        agent_id=sub_id,
        task=task,
        agent_cls=agent_cls,
        parent_id=parent_id,
        tool_names=tool_names,
    )

    return WorkerOutput(
        agent_id=sub_id, subtask=task,
        status=result.get("status", "failed"),
        summary=result.get("output", "")[:200],
        files_modified=[],  # 从 workspace manifest 读取
        output=result.get("output", ""),
        error=result.get("error"),
    )
```

---

## 八、Scheduler 扩展

当前 Scheduler 只有最大并发限制。多 Agent 需要更丰富的调度能力：

```python
# runtime/scheduler.py（扩展）

class Scheduler:
    def __init__(self, max_concurrent: int = 10):
        self._tasks: dict[str, asyncio.Task] = {}
        self._queue: list[ScheduledTask] = []   # ← 新增：等待队列
        self._max_concurrent = max_concurrent
        self._parent_child: dict[str, list[str]] = {}  # ← 新增：父子映射

    async def schedule_subtask(
        self,
        parent_id: str,
        child_id: str,
        coro: Coroutine,
    ) -> asyncio.Task:
        """调度 SubAgent 任务，记录父子关系。"""
        self._parent_child.setdefault(parent_id, []).append(child_id)

        if self.running_count() >= self._max_concurrent:
            # 入队等待
            ...

        task = asyncio.create_task(coro, name=child_id)
        self._tasks[child_id] = task
        return task

    def get_children(self, parent_id: str) -> list[str]:
        """获取某个 Agent 的所有子 Agent ID。"""
        return self._parent_child.get(parent_id, [])

    async def wait_children(self, parent_id: str) -> list[dict]:
        """等待所有子 Agent 完成并收集结果。"""
        children = self.get_children(parent_id)
        results = []
        for cid in children:
            result = await self.wait(cid)
            results.append(result)
        return results
```

---

## 九、工作流示例

### 9.1 Manager/Worker 模式

```python
# 用户发起
result = await runtime.run(
    agent_id="refactor_task",
    task="将 auth 模块拆分为多个文件",
    agent_cls=ManagerAgent,
)
```

内部流程：

```
Runtime.run("refactor_task")
  └── ManagerAgent.step()
       ├── LLM 分析：需要 3 个 Worker
       ├── AgentAction.cont(subtasks=[
       │     {"id": "w_1", "task": "创建 auth/models.py"},
       │     {"id": "w_2", "task": "创建 auth/views.py"},
       │     {"id": "w_3", "task": "创建 auth/controllers.py"},
       │   ])
       │
       ├── Runtime.run_subagent("refactor_task/w_1", ...)
       ├── Runtime.run_subagent("refactor_task/w_2", ...)
       ├── Runtime.run_subagent("refactor_task/w_3", ...)
       │
       ├── Scheduler.wait_children("refactor_task")
       │
       └── ManagerAgent.step()（再次）
            ├── 聚合结果
            └── AgentAction.complete("auth 模块拆分为 3 个文件")
```

### 9.2 工具隔离

```
refactor_task (ManagerAgent)
  工具: [decompose_task, aggregate_results, read_file]
  无权: write_file, shell_exec, git

refactor_task/w_1 (WorkerAgent)
  工具: [read_file, write_file, shell_exec, grep, edit, git]

refactor_task/w_2 (WorkerAgent)
  工具: [read_file, write_file, shell_exec, grep, edit, git]
```

### 9.3 资源分配

```
workspace/
├── refactor_task/                      ← Manager workspace
│   └── .state/manifest.json
│
├── refactor_task__w_1/                 ← Worker 1
│   ├── output/auth/models.py
│   └── .state/manifest.json
│
├── refactor_task__w_2/                 ← Worker 2
│   ├── output/auth/views.py
│   └── .state/manifest.json
│
└── refactor_task__w_3/                 ← Worker 3
    ├── output/auth/controllers.py
    └── .state/manifest.json
```

Memory 隔离：

```
~/.zmai/memory/
├── refactor_task/                      ← Manager 记忆
├── refactor_task__w_1/                 ← Worker 1 记忆
├── refactor_task__w_2/                 ← Worker 2 记忆
└── refactor_task__w_3/                 ← Worker 3 记忆
```

---

## 十、Manager 的 AgentAction 扩展

当前 `AgentAction` 只有四种类型：`continue`、`pause`、`complete`、`fail`。

为支持 Manager 分配任务，新增 `delegate` 类型：

```python
@dataclass
class AgentAction:
    type: Literal["continue", "pause", "complete", "fail", "delegate"]
    output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    pause_reason: str | None = None
    error: str | None = None
    subtasks: list[SubTaskDef] | None = None  # ← 新增：子任务列表


@dataclass
class SubTaskDef:
    """子任务定义。Manager 返回此结构，Runtime 据此创建 Worker。"""
    id: str               # Worker 的短 ID（如 "w_1"）
    task: str             # 子任务描述
    agent_cls: str = "worker"  # Agent 类型名
    tool_names: list[str] | None = None  # 工具限制
```

Runtime 处理 `delegate`：

```python
if action.type == "delegate" and action.subtasks:
    children = []
    for st in action.subtasks:
        child_id = f"{agent_id}/{st.id}"
        cls = {"worker": WorkerAgent, "manager": ManagerAgent}.get(st.agent_cls, SWEAgent)
        result = await self.run_subagent(child_id, st.task, cls, st.tool_names)
        children.append(result)
    # 子任务全部完成后，继续 Manager 的 step（结果注入 metadata）
    ctx.metadata["subtask_results"] = children
    continue  # 回到 Manager 的 step 以聚合结果
```

---

## 十一、文件清单

| 文件 | 操作 | 行数 | 内容 |
|------|------|------|------|
| `src/zmai/agent/base.py` | 修改 | +15 | AgentAction 加 `delegate` 类型 + SubTaskDef |
| `src/zmai/agent/manager.py` | **新增** | ~120 | ManagerAgent 类 |
| `src/zmai/agent/worker.py` | **新增** | ~80 | WorkerAgent 类 |
| `src/zmai/agent/sub_agent.py` | **新增** | ~60 | SubAgentMixin + WorkerOutput |
| `src/zmai/runtime/runtime.py` | 修改 | +50 | run() 加 agent_cls + run_subagent() + delegate 处理 |
| `src/zmai/runtime/scheduler.py` | 修改 | +40 | schedule_subtask + parent_child + wait_children |
| `src/zmai/workspace/workspace.py` | 修改 | +10 | _agent_path 支持 `/` → `__` 转换 |
| `tests/test_multi_agent.py` | **新增** | ~200 | Manager/Worker 测试 |
| **总计** | | **~575** | |

---

## 十二、安全与边界

| 边界 | 策略 |
|------|------|
| **SubAgent 深度** | 最多 5 层（`MAX_SUBAGENT_DEPTH = 5`） |
| **Worker 数量** | 单 Manager 最多 10 个 Worker（受 Scheduler max_concurrent 限制） |
| **工具隔离** | 父 Agent 可限制子 Agent 的工具集 |
| **Workspace 隔离** | 各 Agent 独立目录，不可互相访问 |
| **Memory 隔离** | 各 Agent 独立 namespace，不可互相读取 |
| **生命周期** | 子 Agent 完成/失败不影响父 Agent 继续执行 |

---

*Design by `claude` — 基于 `agent/base.py`、`runtime/runtime.py`、`runtime/scheduler.py` 当前架构*
