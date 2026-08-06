# ZMAI Memory Review

> 审查日期: 2026-07-17
> 范围: Project Memory, Session Memory, Long Memory, Task Memory, 混用分析
> 文件: `src/zmai/memory/` (4 文件, 305 行), `src/zmai/cli/main.py`, `MEMORY_DESIGN.md` (1235 行)

---

## 一、执行摘要

ZMAI Memory 系统当前处于 **v1 到 v2 的过渡阶段**。

**已实现（v1 基准）：**
- Memory ABC + MemoryEntry 数据类（base.py）
- WorkingMemory（内存 dict，namespace 隔离）（working.py）
- LongTermMemory（JSONL 文件持久化）（long_term.py）
- MemoryManager（按 agent_id 配对 Working + LongTerm）（manager.py）

**设计文档（MEMORY_DESIGN.md v2.0，1235 行）规划了完整的三层架构 + 5 个新组件，但几乎全部未实现。**

| 概念 | 设计状态 | 代码状态 | 差距 |
|------|---------|---------|------|
| Working Memory | v2.0（LRU 淘汰） | v1（溢出抛异常） | 小 |
| Long-term Memory | v2.0（append-only O(1)） | v1（全文重写 O(n)） | 中 |
| **Project Memory** | **新增组件** | ❌ 不存在 | **大** |
| **Session Memory** | **SessionManager** | ❌ 仅 `_save_session` 工具函数 | **大** |
| **Task Memory** | **TaskState + Checkpoint** | ❌ 不存在 | **大** |
| Runtime 集成 | MemoryManager 是核心组件 | ❌ runtime.py 零引用 | **大** |

**最严重的问题**：MemoryManager 与 Agent 运行时完全解耦。runtime.py 不导入、不创建、不使用 MemoryManager。Agent 在执行任务时无法读写任何记忆。

---

## 二、Project Memory（项目记忆）

### 2.1 设计（MEMORY_DESIGN.md v2.0 §3）

| 属性 | 值 |
|------|-----|
| 存储位置 | `~/.zmai/memory/projects/<name>.jsonl` |
| 生命周期 | 永久，显式删除 |
| 作用域 | 项目全局，跨 Agent、跨 Session |
| 容量 | 10,000 条目 |
| 读取回退链 | Working → Project → Long-term |
| 写入策略 | 5 秒延迟批量写入（debounce） |

**设计中的核心功能：**
- 存储项目类型、工具链、检测结果
- 记录任务的 Session 历史
- 项目关键信息与决策
- 路径哈希（SHA-256）隔离项目内存文件

### 2.2 实现状态：❌ 不存在

`memory/` 目录下**没有 `project.py`**，`MemoryManager` 上**没有 `project()` 方法**。整个 Project Memory 概念仅为设计文档中的文本。

| 组件 | 文件 | 行数 | 状态 |
|------|------|------|------|
| `ProjectMemory` 类 | `project.py` | ~150 | 未创建 |
| 项目索引 | `~/.zmai/memory/index.json` | — | 未创建 |
| 目录隔离 | `~/.zmai/memory/projects/` | — | 未创建 |

### 2.3 影响

没有 Project Memory，Agent 每次在新会话中进入同一项目时，都需要重新发现项目的类型、工具链、检测结果。无法实现 LONG_TERM_DX.md 描述的 "项目上下文缓存"（30 天 TTL）。

---

## 三、Session Memory（会话记忆）

### 3.1 设计（MEMORY_DESIGN.md v2.0 §7）

| 属性 | 值 |
|------|-----|
| 存储位置 | `~/.zmai/sessions/ses_<uuid>.json` |
| 类 | `SessionManager` |
| 生命周期 | 创建 → 更新 → 暂停/恢复 → 清理（最多 20 个） |
| 存储内容 | agent_id、project、task、status、steps_completed、current_focus、files_modified、last_message |
| 快速查找 | `latest.json` 指向最新 session |

### 3.2 实现状态：❌ 不存在（仅有工具函数）

当前代码**没有 `SessionManager` 类**。只有两个工具函数：

```python
# cli/main.py:34-49
def _save_session(task: str) -> None:
    """保存任务文本到 ~/.zmai/sessions/latest.json"""
    Path(HOME / "sessions").mkdir(parents=True, exist_ok=True)
    write_json({"task": task, "time": _now()})

def _load_latest_session() -> str | None:
    """读取 latest.json 中的 task 文本"""
    # 返回 task 字符串或 None
```

**存储内容对比：**

| 字段 | 设计中的 SessionManager | 当前的 `_save_session` |
|------|------------------------|----------------------|
| session_id | ✅ UUID | ❌ |
| agent_id | ✅ 当前 agent | ❌ |
| project | ✅ 项目路径 | ❌ |
| task | ✅ 任务描述 | ✅ 仅此一项 |
| status | ✅ running/paused/completed/failed | ❌ |
| steps_completed | ✅ 进度 | ❌ |
| current_focus | ✅ 当前焦点 | ❌ |
| files_modified | ✅ 修改的文件列表 | ❌ |
| last_message | ✅ 最后交互 | ❌ |
| 时间戳 | ✅ 精确时间 | ✅ 有 |

### 3.3 使用差异

| 场景 | 设计行为 | 当前行为 |
|------|---------|---------|
| 启动时 | 检测未完成的 session，提示恢复 | 显示 "last task: xxx"（仅文本） |
| 多 session | 管理 20 个 session，支持历史回溯 | 只保存/读取 `latest.json` |
| 暂停恢复 | 保存完整状态，恢复时重建 Agent Context | 无暂停/恢复 |
| 跨项目 session | 检测项目是否匹配 | 无条件加载，不检查项目 |
| 中断检测 | 检测异常退出，提示恢复 | 无检测 |

---

## 四、Long Memory（长期记忆）

### 4.1 实现（已存在）

`LongTermMemory` 位于 `src/zmai/memory/long_term.py`（104 行）：

**文件格式**：JSONL（每行一个 JSON 对象），序列化后的 `MemoryEntry`

**路径**：`~/.zmai/memory/{agent_id}/{namespace}.jsonl`

```
~/.zmai/memory/
├── agent_abc123/
│   ├── default.jsonl        ← namespace "default"
│   ├── task.jsonl           ← namespace "task"
│   └── decisions.jsonl      ← namespace "decisions"
├── agent_def456/
│   └── default.jsonl
```

**核心方法：**

| 方法 | 行为 | 性能 |
|------|------|------|
| `store(key, value, namespace)` | 写入内存缓存 + **全文重写 JSONL** | O(n) |
| `read(key, namespace)` | 从缓存或文件加载 | O(1) |
| `delete(key, namespace)` | 删除 + **全文重写** | O(n) |
| `search(query, namespace)` | 子串匹配 key 或 str(value) | O(n) |
| `clear(namespace)` | 删除 JSONL 文件 | O(1) |
| `list_namespaces()` | 列出 `*.jsonl` 文件 | O(n) |

### 4.2 v1 → v2 差距

| 方面 | v1 现状 | v2 设计 |
|------|---------|---------|
| 写入策略 | 全文重写 | Append-only（`open("a")`） |
| 文件结构 | 每个 namespace 一个文件 | 每个 agent 一个文件，key-value 行 |
| 目录 | `~/.zmai/memory/<id>/` | `~/.zmai/memory/agents/<id>/` |
| 大小限制 | 构造函数接受 `max_file_size`（10MB）但**未实施检查** | 同样 |
| 写入性能 | 10 条目 ~5ms，1000 条目 ~500ms | O(1) 恒定的 ~1ms |

### 4.3 TTL 过期机制

- `MemoryEntry.ttl` 字段存在（以秒为单位，`None` = 永不过期）
- `is_expired` 属性已实现
- `LongTermMemory._load_namespace()` 过滤过期条目
- **没有任何代码路径设置 TTL**（`WorkingMemory.store()` 总是传 `ttl=None`）
- **没有主动清理机制**——只有惰性读取时检查

---

## 五、Task Memory（任务记忆）

### 5.1 设计（MEMORY_DESIGN.md v2.0 §5）

`TaskState` 数据类：

```python
@dataclass
class TaskState:
    task_id: str                    # 唯一标识
    description: str                # 任务描述
    status: str                     # running / paused / completed / failed
    steps_completed: int = 0
    steps_planned: int = 0
    current_focus: str = ""         # 当前焦点
    next_action: str = ""           # 下一步计划
    files_modified: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:       # → "Task: xxx (3/5 steps)"
    def to_prompt(self) -> str:     # Agent prompt 注入
```

存储到 LongTermMemory 的 `"task"` namespace：
```python
lm.store("task", task_state.to_dict(), namespace="task")
```

### 5.2 实现状态：❌ 不存在

| 组件 | 行数 | 状态 |
|------|------|------|
| `TaskState` dataclass | ~80 | 未创建 |
| Checkpoint 每步保存 | ~70 | 未创建 |
| Task prompt 注入 | ~30 | 未创建 |
| 断点恢复 | ~50 | 未创建 |

**影响**：
- Agent 中断后无法从断点恢复
- 多步任务无法追踪进度
- Agent 重启后不知道之前做了什么

### 5.3 与 Session Memory 的关系

设计文档中，Task Memory 和 Session Memory 是互补关系：

```
Session Memory                    Task Memory
├── session_id                    ├── task_id (1 session 可有多 task)
├── agent_id                      ├── description
├── project → 定位项目             ├── steps_completed / steps_planned
├── status: running/paused/...    ├── current_focus
├── last_message                  ├── files_modified
└── task_ids: [t1, t2, t3]        └── errors
                                  └── 每步自动保存
```

**当前两者都不存在**。Session 只保存 task 文本字符串，Task 无任何追踪。

---

## 六、是否混用（Mixing Analysis）

### 6.1 当前界限

现有实现的界限非常清晰——但仅因 **概念太少**：

| 维度 | 分隔方式 | 评估 |
|------|---------|------|
| Agent 间 | MemoryManager 按 `agent_id` 字典分隔 | ✅ 清晰 |
| Namespace 间 | WorkingMemory 用 `namespace` key、LongTermMemory 用独立文件 | ✅ 清晰 |
| Working vs Long-term | 内存 vs 磁盘，Manager 配对 | ✅ 清晰 |
| 项目 vs 会话 vs 任务 | **没有这些概念** | ⚠️ 不存在所以不混 |

**结论：当前不存在混用问题，因为这三个概念（项目/会话/任务）根本没有实现。**

### 6.2 如果实现 v2.0 后的潜在混用风险

| 场景 | 混用风险 | 级别 |
|------|---------|------|
| Project Memory 和 Long-term Memory 共存在 `~/.zmai/memory/` | 如果缺少 `projects/` 和 `agents/` 子目录隔离 | ⚠️ 高 |
| Session 和 Task 都存储 agent_id | 如果会话和任务使用同一个 agent_id 但含义不同 | ⚠️ 中 |
| 恢复链优先级混乱 | 如果 `Working → Project → Long-term` 回退链不明确 | ⚠️ 中 |
| namespace 命名冲突 | 如果不同组件使用同一 namespace 字符串（如 `"default"`） | ⚠️ 低 |

### 6.3 设计文档中的隔离策略

MEMORY_DESIGN.md v2.0 明确设计了隔离：

```
~/.zmai/memory/
├── index.json                          ← 项目索引（按路径哈希）
├── projects/
│   └── <path_hash>.jsonl               ← Project Memory（按路径隔离）
├── agents/
│   └── <agent_id>/
│       ├── default.jsonl               ← Long-term Memory
│       ├── task.jsonl                  ← Task State
│       ├── conversation.jsonl           ← Conversation Log（最多 500 条）
│       ├── knowledge.jsonl             ← 项目知识
│       └── decisions.jsonl             ← Agent 决策
└── sessions/
    ├── latest.json                     ← 最新 session 指针
    └── ses_<uuid>.json                 ← Session 快照
```

**如果按照此设计实现，隔离是充分的。** 问题在于没有任何东西被实现。

### 6.4 Runtime 集成缺口

当前 `runtime.py` 对 MemoryManager **零引用**：

```python
# runtime.py 的 __init__:
self._lifecycle = LifecycleManager()
self._state = StateManager(state_file, config)
self._scheduler = Scheduler(max_concurrent)
self._workspace = Workspace(root=ws_root, config=config)
self._gateway = BackendRegistry()
# ❌ 没有 MemoryManager！

# runtime.py 的 run():
agent = SWEAgent(agent_id, ...)
context = AgentContext(...)
# ❌ MemoryManager 没有传入 AgentContext
# ❌ 没有 checkpoint 调用
# ❌ 没有 restore 调用
# ❌ 没有 persist 调用
```

对比设计文档（MEMORY_DESIGN.md v2.0 §9.1）：

```python
class Runtime:
    def __init__(self, config):
        self.memory = MemoryManager()
        ...
    
    def run(self, task, agent_id, memory=None):
        memory = memory or self.memory
        agent = SWEAgent(agent_id, memory=memory)
        ...
        # 每步后: memory.checkpoint(agent_id)
        # 完成后: memory.persist(agent_id)
```

---

## 七、测试覆盖

### 7.1 现有测试（test_memory.py, 158 行, 21 个测试）

| 组件 | 测试数 | 覆盖内容 |
|------|-------|---------|
| MemoryEntry | 4 | 构造、序列化、TTL 过期 |
| WorkingMemory | 8 | CRUD、namespace 隔离、search、clear |
| LongTermMemory | 5 | CRUD、持久化、namespace 分离 |
| MemoryManager | 4 | Agent 隔离、cleanup、exists |

### 7.2 测试缺口

| 缺口 | 严重度 | 说明 |
|------|--------|------|
| `persist()` 未测试 | **高** | 48 行代码，零测试 |
| `restore()` 未测试 | **高** | 从 LongTerm 恢复到 Working 的逻辑 |
| `max_size` 溢出未测试 | 中 | WorkingMemory 抛 `ZMemoryError` |
| TTL 在 WorkingMemory 中未测试 | 中 | read() 检查过期并删除 |
| TTL 在 LongTermMemory 中未测试 | 中 | _load_namespace 过滤过期条目 |
| 文件损坏恢复未测试 | 中 | JSONDecodeError 静默跳过 |
| 并发访问未测试 | 中 | 所有类都有 threading.Lock |
| Session 边界测试 | 中 | 无此概念 |
| Project 边界测试 | 中 | 无此概念 |

---

## 八、总结与建议

### 8.1 当前评分

| 维度 | 评分 | 说明 |
|------|------|------|
| Working Memory | ★★★★☆ | 功能完整，namespace 隔离好，但溢出策略差 |
| Long-term Memory | ★★★☆☆ | 持久化功能正常，但全文重写性能差 |
| Project Memory | ☆☆☆☆☆ | 完全不存在 |
| Session Memory | ★☆☆☆☆ | 只有文本保存，无状态管理 |
| Task Memory | ☆☆☆☆☆ | 完全不存在 |
| Runtime 集成 | ★★☆☆☆ | MemoryManager 是孤儿组件，未被 runtime 使用 |
| 隔离清晰度 | ★★★★☆ | 当前概念少所以不混，v2 设计已有隔离方案 |
| 测试覆盖 | ★★★☆☆ | 21 个测试，但关键方法 persist/restore 未测 |

**综合: 2.5/5** — 基础功能可用，但核心集成缺失，v2 组件全部未实现。

### 8.2 四象限分析

```
                     已实现                 未实现
                ┌──────────────┬──────────────────┐
    Agent 级    │ WorkingMemory │  TaskState        │
                │ LongTermMemory│  CheckpointManager│
                │ MemoryManager │  ConversationLog  │
                ├──────────────┼──────────────────┤
    Project 级  │  (无)         │  ProjectMemory    │
                │              │  项目索引          │
                ├──────────────┼──────────────────┤
    Session 级  │ _save_session│  SessionManager    │
                │ (仅文本)      │  会话管理/恢复    │
                └──────────────┴──────────────────┘
```

### 8.3 优先实施建议

| 优先级 | 组件 | 原因 | Effort |
|--------|------|------|--------|
| **P0** | Runtime 集成 | MemoryManager 无人用，再好的内存设计也无用 | 4 小时 |
| **P1** | LongTermMemory append-write | 从 O(n) 到 O(1)，文件数多时差距显著 | 2 小时 |
| **P1** | WorkingMemory LRU 淘汰 | 溢出抛异常不可用，LRU 是基本要求 | 2 小时 |
| **P2** | TaskState + Checkpoint | Agent 需要追踪进度和断点恢复 | 6 小时 |
| **P2** | SessionManager | 会话管理和恢复是 CLI UX 的基础 | 8 小时 |
| **P3** | ProjectMemory | 跨会话项目缓存，长期 DX 提升 | 8 小时 |
| **P3** | ConversationLog | Agent 对话历史追踪 | 4 小时 |

### 8.4 隔离规则

如果实施 v2.0，必须遵守以下隔离规则以避免混用：

1. **文件系统隔离**：三种内存走三个不同目录
   - Project → `~/.zmai/memory/projects/<hash>.jsonl`
   - Agent → `~/.zmai/memory/agents/<id>/<ns>.jsonl`
   - Session → `~/.zmai/sessions/ses_<uuid>.json`

2. **生命周期隔离**
   - Project: 永久，显式删除
   - Agent (Long-term): 永久，与 Agent 生命周期绑定
   - Working: 临时，随 Agent 会话销毁
   - Session: 手动清理，最多保留 20 个

3. **读取优先级隔离**（防止跨层污染）
   - Agent 读取：Working → Long-term（不能读取其他 Agent 的 Long-term）
   - 项目读取：Project Memory（仅当前项目）
   - Session 读取：SessionManager（不能越 session）

4. **Namespace 命名规范**
   - 系统保留 namespace：`"default"`, `"task"`, `"conversation"`, `"knowledge"`, `"decisions"`
   - 用户命名空间不得以系统保留名为前缀

---

*Report generated by `claude` — 基于代码分析、测试分析、设计文档审查*
