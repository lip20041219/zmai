# ZMAI Memory Design v2.0

Version: 2.0
Date: 2026-07-16

> **关掉 ZMAI，第二天回来接着干。** Session、Context、Project Memory、Task 全部恢复。
>
> 不修改 Runtime / Agent / Gateway / Workflow / Workspace / CLI 等下游模块。
>
> 仅修改 `src/zmai/memory/` 模块。

---

## 目录

1. [现状审查](#1-现状审查)
2. [设计原则](#2-设计原则)
3. [三层记忆架构](#3-三层记忆架构)
4. [Working Memory（内存层）](#4-working-memory内存层)
5. [Project Memory（项目层）](#5-project-memory项目层)
6. [Long-term Memory（持久层）](#6-long-term-memory持久层)
7. [Session 自动恢复](#7-session-自动恢复)
8. [Task 状态追踪](#8-task-状态追踪)
9. [自动 Checkpoint](#9-自动-checkpoint)
10. [目录结构](#10-目录结构)
11. [恢复流程（完整链路）](#11-恢复流程完整链路)
12. [接口设计](#12-接口设计)
13. [文件清单与实现计划](#13-文件清单与实现计划)

---

## 1. 现状审查

### 1.1 当前实现

| 文件 | 行数 | 功能 | 状态 |
|------|------|------|------|
| `base.py` | 71 | `MemoryEntry` 数据类 + `Memory` 抽象基类 | ✅ |
| `working.py` | 67 | `WorkingMemory` — 内存 dict，TTL 过期 | ✅ |
| `long_term.py` | 104 | `LongTermMemory` — JSONL 文件按 namespace 持久化 | ✅ |
| `manager.py` | 63 | `MemoryManager` — 配对 Working + LongTerm | ⚠️ 严重缺陷 |

### 1.2 已知缺陷

| 缺陷 | 位置 | 影响 |
|------|------|------|
| **`restore()` 是空方法** | `manager.py:49-56` | `pass` 占位，不恢复任何数据 |
| **无 Session 记忆** | 不存在 | 关闭 ZMAI 后丢失当前会话 |
| **无 Project 记忆** | 不存在 | 切换项目后丢失项目上下文 |
| **无 Task 状态追踪** | 不存在 | Agent 不知道自己做到哪一步 |
| **无自动 Checkpoint** | 不存在 | 异常退出后工作丢失 |
| **search() 仅字符串匹配** | `working.py:55` / `long_term.py:88` | 无法语义搜索 |
| **每次 store() 全量写文件** | `long_term.py:48-51` | 每次写入重写整个 JSONL 文件 |

### 1.3 根因

当前 Memory 设计只有一个目标——**持久化 Key-Value**。但实际需要的是三件事：

```
需要做的              → 现在做的
────────────────────────────────
会话恢复              → ❌ 不存在
项目上下文持久化       → ❌ 不存在
任务进度追踪           → ❌ 不存在
Agent Key-Value 存储  → ✅ Working + LongTerm
```

---

## 2. 设计原则

### 2.1 三层分离

```
Working Memory  — Agent 运行中，纯内存，最快
Project Memory  — 项目级别，文件持久化，跨 Agent
Long-term Memory — Agent 级别，文件持久化，跨会话
```

每层解决不同的问题，互不重叠。

### 2.2 自动保存

```
不要等用户保存。自动 checkpoint。

时机：
  - 每次 Agent step 完成后（增量）
  - 用户 Ctrl+C / 退出 ZMAI 时（全量）
  - 每 60 秒（定时）
  - 系统信号 SIGTERM（应急）
```

### 2.3 自动恢复

```
启动时自动执行恢复链：
  1. 检测项目 → 加载 Project Memory
  2. 检测最近 Session → 加载对话历史 + Task 状态
  3. 加载 Agent Long-term Memory
  4. 构建完整 Context → 注入 Agent

用户感知： "继续昨天的 auth 重构"
                  ↓
           Agent 知道之前的代码、任务进度、对话历史
```

### 2.4 不修改下游

```
仅修改：  src/zmai/memory/   ← 重写

不修改：  src/zmai/runtime/*   ✗  （Memory 作为 Runtime 的资源注入）
          src/zmai/agent/*     ✗
          src/zmai/gateway/*   ✗
          src/zmai/workspace/* ✗
          src/zmai/workflow/*  ✗
          src/zmai/cli/*       ✗
```

---

## 3. 三层记忆架构

### 3.1 架构总览

```
┌────────────────────────────────────────────────────────────┐
│                     MemoryManager                           │
│  (统一入口，编排三层)                                        │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   Working    │    │   Project    │    │  Long-term   │  │
│  │   Memory     │    │   Memory     │    │   Memory     │  │
│  │              │    │              │    │              │  │
│  │ 纯内存        │    │ 文件持久化    │    │ 文件持久化    │  │
│  │ Agent 会话   │    │ 项目全局      │    │ Agent 级别    │  │
│  │ 速度最快     │    │ 跨 Agent      │    │ 跨会话        │  │
│  │              │    │ 跨会话        │    │              │  │
│  │ TTL 自动过期  │    │ 显式删除      │    │ 显式删除      │  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
│         │                  │                   │           │
│         ▼                  ▼                   ▼           │
│   内存 dict            .zmai/memory/      .zmai/memory/    │
│                       project.jsonl      agents/<id>.jsonl │
└────────────────────────────────────────────────────────────┘
```

### 3.2 三层对比

| 维度 | Working Memory | Project Memory | Long-term Memory |
|------|---------------|---------------|-----------------|
| 存储位置 | 内存 | `~/.zmai/memory/projects/<name>.jsonl` | `~/.zmai/memory/agents/<id>.jsonl` |
| 生命周期 | Agent 会话 | 永久（显式删除） | 永久（显式删除） |
| 作用域 | 单个 Agent | 项目全局 | 单个 Agent 跨会话 |
| 速度 | 纳秒级 | 毫秒级（文件 IO） | 毫秒级（文件 IO） |
| 容量 | 1000 条目 | 10000 条目 | 50000 条目 |
| TTL | ✅ 支持 | ❌ 不自动过期 | ❌ 不自动过期 |
| 写入策略 | 即时 | 延迟批量 (5s) | 延迟批量 (5s) |
| 典型数据 | 当前步骤结果、临时变量 | 项目配置、工具链、检测结果 | Agent 长期知识、任务记录 |

### 3.3 数据流

```
Agent 运行时 → MemoryManager
                  │
           ┌──────┴──────┐
           │              │
      Working Memory   Long-term Memory
      (当前步骤)       (持久化)
           │
           ▼
      Project Memory
      (项目级持久化)
```

读路径：`Working → Project → Long-term`（逐层 fallback）
写路径：`Working → (延迟) → Project / Long-term`（异步落盘）

---

## 4. Working Memory（内存层）

### 4.1 定位

Working Memory 是 Agent 的"便签纸"——当前会话中使用的临时数据，会话结束即可丢弃。

### 4.2 存储内容

| 类别 | 示例 | TTL |
|------|------|-----|
| 当前步骤结果 | 上一步的文件内容、grep 结果 | 10 分钟 |
| 临时变量 | 当前正在处理的函数名、行号 | 会话结束 |
| 中间计算 | 代码分析中的临时数据 | 5 分钟 |
| 工具缓存 | `read_file` 最近读取的文件 | 60 分钟 |

### 4.3 实现

```python
class WorkingMemory(Memory):
    """工作记忆。纯内存，TTL 自动过期。"""

    def __init__(self, max_size: int = 1000):
        self._data: dict[str, dict[str, MemoryEntry]] = {}
        self._lock = threading.Lock()
        self._max_size = max_size

    def store(self, key: str, value: Any, namespace: str = "default",
              ttl: int | None = None) -> None:
        with self._lock:
            ns = self._data.setdefault(namespace, {})
            if len(ns) >= self._max_size:
                # LRU 逐出：删除最旧的条目
                oldest = min(ns.values(), key=lambda e: e.updated_at)
                ns.pop(oldest.key, None)
            ns[key] = MemoryEntry(
                key=key, value=value, namespace=namespace,
                ttl=ttl,
            )

    def read(self, key: str, namespace: str = "default") -> Any:
        with self._lock:
            ns = self._data.get(namespace, {})
            entry = ns.get(key)
            if entry is None:
                return None
            if entry.is_expired:
                del ns[key]
                return None
            return entry.value

    def snapshot(self) -> dict[str, Any]:
        """返回所有未过期的条目快照，用于持久化到 Project Memory。"""
        with self._lock:
            result = {}
            for ns, entries in self._data.items():
                valid = {k: e.to_dict() for k, e in entries.items() if not e.is_expired}
                if valid:
                    result[ns] = valid
            return result

    # ... read/update/delete/search/clear/list_namespaces 保持不变
```

**变更：** `store()` 满时 LRU 逐出（原为抛异常）。新增 `snapshot()` 方法。

---

## 5. Project Memory（项目层）

### 5.1 定位（新增）

Project Memory 是**项目级别的持久化记忆**。切换 Agent 或重启 ZMAI 后仍然存在。

目标：Agent 启动后立即知道项目的上下文，不需要重新检测。

### 5.2 存储内容

```json
{
  "project": {
    "name": "my-project",
    "type": "python",
    "version": "3.13",
    "root": "/home/user/my-project",
    "last_used": "2026-07-16T10:00:00Z"
  },
  "detection": {
    "package_manager": "uv",
    "test_framework": "pytest",
    "linter": "ruff",
    "src_dirs": ["src"],
    "test_dirs": ["tests"]
  },
  "sessions": [
    {
      "session_id": "ses_abc123",
      "started_at": "2026-07-16T09:00:00Z",
      "ended_at": "2026-07-16T10:00:00Z",
      "agent_id": "agent_def456",
      "task": "重构 auth 模块",
      "status": "paused",
      "summary": "修改了 login.py 和 auth.py，测试通过 42/45"
    }
  ],
  "keywords": [
    "auth", "jwt", "login", "middleware"
  ],
  "artifacts": [
    {
      "path": "output/auth_new.py",
      "description": "新的 auth 模块实现",
      "created_at": "2026-07-16T09:30:00Z"
    }
  ]
}
```

### 5.3 存储位置

```
~/.zmai/memory/
├── projects/
│   ├── my-project.jsonl      ← 每个项目一个文件
│   └── other-project.jsonl
└── index.json                 ← 项目索引（名称 → 路径映射）
```

### 5.4 实现

```python
class ProjectMemory(Memory):
    """项目记忆。JSONL 持久化，跨 Agent/跨会话共享。"""

    def __init__(self, project_name: str):
        self._path = _project_path(project_name)
        self._lock = threading.Lock()
        self._batch_dirty = False
        self._last_flush = time.time()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """加载项目记忆。"""
        if not self._path.exists():
            return {
                "project": {"name": self._path.stem, "last_used": _now()},
                "detection": {},
                "sessions": [],
                "keywords": [],
                "artifacts": [],
            }
        entries = {}
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    d = json.loads(line)
                    entries[d["key"]] = d["value"]
                except (json.JSONDecodeError, KeyError):
                    continue
        return entries

    def _save(self) -> None:
        """批量延迟写入。"""
        self._batch_dirty = True
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        if self._batch_dirty and (time.time() - self._last_flush > 5.0):
            self._flush()

    def _flush(self) -> None:
        """写入磁盘。追加模式，不重写。"""
        # 每个 key 一行 JSON
        lines = []
        for key, value in self._data.items():
            if key == "project":
                value["last_used"] = _now()
            lines.append(json.dumps({"key": key, "value": value}, ensure_ascii=False))
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._batch_dirty = False
        self._last_flush = time.time()

    def get_project_info(self) -> dict[str, Any]:
        """获取项目基础信息。"""
        return self._data.get("project", {})

    def update_project_info(self, info: dict[str, Any]) -> None:
        """更新项目信息（检测结果等）。"""
        self._data["project"] = {**self._data.get("project", {}), **info}
        self._save()

    def add_session(self, session_info: dict[str, Any]) -> None:
        """记录一次会话。"""
        sessions = self._data.setdefault("sessions", [])
        sessions.append(session_info)
        # 只保留最近 20 条会话记录
        if len(sessions) > 20:
            sessions[:] = sessions[-20:]
        self._save()

    def get_latest_session(self) -> dict[str, Any] | None:
        """获取最近一次未完成的会话。"""
        sessions = self._data.get("sessions", [])
        for s in reversed(sessions):
            if s.get("status") in ("paused", "running"):
                return s
        return None

    def add_keywords(self, keywords: list[str]) -> None:
        """添加项目关键词（帮助搜索和回忆）。"""
        existing = set(self._data.setdefault("keywords", []))
        existing.update(kw.lower() for kw in keywords)
        self._data["keywords"] = sorted(existing)
        self._save()

    def store(self, key: str, value: Any, namespace: str = "default") -> None:
        ns_key = f"{namespace}.{key}" if namespace != "default" else key
        self._data[ns_key] = value
        self._save()

    def read(self, key: str, namespace: str = "default") -> Any:
        ns_key = f"{namespace}.{key}" if namespace != "default" else key
        return self._data.get(ns_key)

    def search(self, query: str, namespace: str = "default") -> list[MemoryEntry]:
        results = []
        q = query.lower()
        for key, value in self._data.items():
            if q in key.lower() or q in str(value).lower():
                results.append(MemoryEntry(key=key, value=value))
        return results

    def flush(self) -> None:
        """强制写入（退出时调用）。"""
        self._flush()
```

---

## 6. Long-term Memory（持久层）

### 6.1 定位

Long-term Memory 是 Agent 的"长期笔记本"——跨会话持久化的 Agent 知识。

### 6.2 存储内容

| namespace | 内容 | 示例 |
|-----------|------|------|
| `task` | 当前任务状态 | 任务描述、进度、完成步骤 |
| `conversation` | 对话历史 | 用户消息 / Agent 响应 |
| `files` | 修改过的文件记录 | 文件路径、修改摘要 |
| `knowledge` | Agent 学到的东西 | API 用法、项目约定 |
| `decisions` | 技术决策记录 | 为什么选择 A 而非 B |

### 6.3 对话历史持久化

```python
class ConversationLog:
    """对话历史管理。每个 Agent 一个 JSONL 文件。"""

    MAX_MESSAGES = 500  # 最大保留消息数

    def __init__(self, path: Path):
        self._path = path
        self._messages: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    self._messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    def append(self, role: str, content: str, metadata: dict | None = None) -> None:
        """追加一条消息。"""
        entry = {
            "role": role,  # "user" | "assistant" | "tool"
            "content": content,
            "timestamp": _now(),
            **(metadata or {}),
        }
        self._messages.append(entry)
        # 追加写入（单条）
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_recent(self, count: int = 20) -> list[dict[str, Any]]:
        """获取最近的 N 条消息。"""
        return self._messages[-count:]

    def truncate(self) -> None:
        """超出 MAX_MESSAGES 时裁剪旧消息。"""
        if len(self._messages) > self.MAX_MESSAGES:
            self._messages = self._messages[-self.MAX_MESSAGES:]
            # 重写文件
            lines = [json.dumps(m, ensure_ascii=False) for m in self._messages]
            self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

### 6.4 写入优化

**问题：** 当前 `LongTermMemory._save_namespace()` 每次全量写整个 JSONL 文件。

**优化：** 使用文件追加模式（`open("a")`），避免全量重写。

```python
def store(self, key: str, value: Any, namespace: str = "default") -> None:
    """写入一条记录。追加模式，O(1)。"""
    entry = MemoryEntry(key=key, value=value, namespace=namespace)
    path = self._file_path(namespace)

    # 内存缓存
    with self._lock:
        ns = self._cache.setdefault(namespace, {})
        ns[key] = entry

    # 文件追加
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
```

**变更：** 从每次全量写改为追加写。删除/更新操作仍然需要全量重写，但 `store()` 变为 O(1)。

---

## 7. Session 自动恢复

### 7.1 Session 数据结构

```json
{
  "session_id": "ses_abc123",
  "project": "my-project",
  "agent_id": "agent_def456",
  "task": "重构 auth 模块",
  "status": "paused",
  "started_at": "2026-07-16T09:00:00Z",
  "updated_at": "2026-07-16T10:00:00Z",
  "steps_completed": 5,
  "total_steps": 8,
  "files_modified": ["src/auth/login.py", "src/auth/middleware.py"],
  "current_focus": "正在修复 token 刷新逻辑",
  "last_message": "测试通过了 42/45，还有 3 个需要修复",
  "progress_summary": "已完成 login 重构，middleware 部分完成，token 刷新逻辑进行中"
}
```

### 7.2 存储位置

```
~/.zmai/sessions/
├── ses_abc123.json         ← 会话快照
└── latest.json             ← 最近会话的 symlink 引用
```

### 7.3 Session 管理器

```python
class SessionManager:
    """会话生命周期管理。"""

    SESSION_DIR = Path.home() / ".zmai" / "sessions"
    MAX_SESSIONS = 20

    def __init__(self):
        self.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self._session_id: str = ""
        self._data: dict[str, Any] = {}

    def create(self, project: str, agent_id: str, task: str) -> str:
        """创建新会话。"""
        self._session_id = f"ses_{uuid4().hex[:12]}"
        self._data = {
            "session_id": self._session_id,
            "project": project,
            "agent_id": agent_id,
            "task": task,
            "status": "running",
            "started_at": _now(),
            "updated_at": _now(),
            "steps_completed": 0,
            "total_steps": 0,
            "files_modified": [],
            "current_focus": "",
            "last_message": "",
            "progress_summary": "",
        }
        self._save()
        self._update_latest()
        return self._session_id

    def update(self, **kwargs) -> None:
        """更新会话状态。"""
        self._data.update(kwargs)
        self._data["updated_at"] = _now()
        self._save()
        self._update_latest()

    def pause(self) -> None:
        """暂停会话（用户 Ctrl+C / 退出）。"""
        self.update(status="paused")

    def resume(self, session_id: str) -> dict[str, Any] | None:
        """恢复会话。"""
        path = self.SESSION_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        self._data = data
        self._session_id = session_id
        self.update(status="running")
        return data

    def get_latest(self) -> dict[str, Any] | None:
        """获取最近未完成的会话。"""
        latest_path = self.SESSION_DIR / "latest.json"
        if not latest_path.exists():
            return None
        try:
            ref = json.loads(latest_path.read_text(encoding="utf-8"))
            return self.resume(ref["session_id"])
        except Exception:
            return None

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """列出最近会话。"""
        sessions = []
        for p in sorted(self.SESSION_DIR.glob("ses_*.json"), reverse=True)[:limit]:
            try:
                sessions.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        return sessions

    def _save(self) -> None:
        path = self.SESSION_DIR / f"{self._session_id}.json"
        path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _update_latest(self) -> None:
        path = self.SESSION_DIR / "latest.json"
        path.write_text(
            json.dumps({"session_id": self._session_id, "updated_at": _now()}),
            encoding="utf-8",
        )

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def data(self) -> dict[str, Any]:
        return self._data
```

### 7.4 自动保存时机

```
Agent step 完成
    │
    ├── session.update(steps_completed=N)
    ├── session.update(current_focus=...)
    ├── session.update(files_modified=[...])
    │
    ▼
用户 Ctrl+C
    │
    ├── session.pause()
    ├── project_memory.add_session(session.data)
    ├── long_term_memory.persist_all()
    └── (等待 500ms 确保文件写入完成)

第二天
    │
    ▼ zmai
    ├── session.get_latest() → 找到未完成会话
    ├── project_memory.get_latest_session() → 项目历史
    ├── long_term_memory.restore(agent_id) → Agent 记忆
    └── "继续昨天的 auth 重构？[Y/n]"
```

---

## 8. Task 状态追踪

### 8.1 结构

```python
@dataclass
class TaskState:
    """任务状态。在 Long-term Memory 的 'task' namespace 中维护。"""

    # 任务信息
    task_id: str
    description: str          # 原始任务描述
    status: str               # "running" | "paused" | "completed" | "failed"

    # 进度
    steps_planned: int = 0
    steps_completed: int = 0
    current_step: str = ""     # 当前步骤描述

    # 文件变更
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)

    # 执行记录
    commands_run: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # 上下文
    current_focus: str = ""    # Agent 当前在做什么
    next_action: str = ""      # 下一步计划做什么

    # 时间
    started_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        """生成人类可读的任务摘要。"""
        if self.status == "completed":
            return f"✅ {self.description} ({self.steps_completed} 步)"
        if self.status == "failed":
            return f"❌ {self.description} (第 {self.steps_completed} 步出错)"
        progress = f"{self.steps_completed}/{self.steps_planned}" if self.steps_planned else f"{self.steps_completed}"
        return f"⟳ {self.description} [{progress}] — {self.current_focus}"
```

### 8.2 集成到 Long-term Memory

```python
# 通过 Long-term Memory 的 'task' namespace 存储

# 写入
long_term.store("task", task_state.to_dict(), namespace="task")

# 读取
task_data = long_term.read("task", namespace="task")
if task_data:
    task_state = TaskState(**task_data)
```

### 8.3 Prompt 注入

```python
def get_task_prompt(task_state: TaskState | None) -> str:
    """从 Task State 生成 Agent Prompt。"""
    if not task_state:
        return ""

    return f"""
=== 任务状态 (自动恢复) ===
当前任务: {task_state.description}
进度: {task_state.steps_completed}/{task_state.steps_planned or '?'} 步
当前焦点: {task_state.current_focus or '无'}
下一步: {task_state.next_action or '无'}

已修改的文件:
{chr(10).join('  - ' + f for f in task_state.files_modified[-10:]) or '  无'}

最近错误:
{chr(10).join('  - ' + e for e in task_state.errors[-3:]) or '  无'}
"""
```

Agent 恢复时接收：

```
=== 任务状态 (自动恢复) ===
当前任务: 重构 auth 模块
进度: 5/8 步
当前焦点: 正在修复 token 刷新逻辑
下一步: 为新的 auth 模块添加测试

已修改的文件:
  - src/auth/login.py
  - src/auth/middleware.py

最近错误:
  - test_auth.py:45 AssertionError: token 刷新后过期时间不正确
```

---

## 9. 自动 Checkpoint

### 9.1 Checkpoint 策略

```python
class CheckpointManager:
    """自动 Checkpoint。确保异常退出后数据不丢失。"""

    CHECKPOINT_INTERVAL = 60  # 秒

    def __init__(self, memory_manager: MemoryManager):
        self._mm = memory_manager
        self._last_checkpoint: float = 0
        self._running = False

    def maybe_checkpoint(self, force: bool = False) -> None:
        """按时间间隔触发 checkpoint。"""
        now = time.time()
        if not force and (now - self._last_checkpoint < self.CHECKPOINT_INTERVAL):
            return
        self._checkpoint()
        self._last_checkpoint = now

    def _checkpoint(self) -> None:
        """执行 checkpoint：快照 Working → 持久化到 Long-term。"""
        for agent_id in self._mm.list_active_agents():
            # 1. Working Memory 快照 → Long-term
            snapshot = self._mm.working(agent_id).snapshot()
            for ns, entries in snapshot.items():
                for key, entry_dict in entries.items():
                    self._mm.long_term(agent_id).store(
                        key, entry_dict["value"], namespace=ns,
                    )

            # 2. Session 更新
            self._mm.session_manager.update(
                last_checkpoint=_now(),
            )

    def shutdown_checkpoint(self) -> None:
        """退出前全量 checkpoint。"""
        self._checkpoint()
        self._mm.session_manager.pause()
        # 强制刷新所有文件写入
        self._mm.flush_all()
```

### 9.2 触发点集成

```python
# 在 Runtime 的 run() 循环中
while step_count < ctx.max_steps:
    action = await agent.step(ctx)

    # 自动 checkpoint（每步）
    memory_manager.checkpoint()

    ...
```

### 9.3 退出信号处理

```python
import signal

def _setup_signal_handlers(mm: MemoryManager):
    """注册退出信号处理。"""

    def _on_exit(signum, frame):
        """安全退出：保存所有记忆。"""
        mm.checkpoint_manager.shutdown_checkpoint()

    signal.signal(signal.SIGTERM, _on_exit)  # kill
    signal.signal(signal.SIGINT, _on_exit)   # Ctrl+C (如果未被 CLI 捕获)
```

---

## 10. 目录结构

### 10.1 完整目录

```
~/.zmai/
├── sessions/
│   ├── ses_abc123.json         ← 会话快照
│   ├── ses_def456.json
│   └── latest.json              ← 最近会话引用
│
└── memory/
    ├── index.json                ← 项目索引
    │
    ├── projects/
    │   ├── my-project.jsonl      ← 项目记忆
    │   └── other-project.jsonl
    │
    └── agents/
        ├── agent_def456/
        │   ├── task.jsonl        ← Task 状态（对话历史）
        │   ├── conversation.jsonl ← 对话历史
        │   ├── knowledge.jsonl   ← 学到的知识
        │   └── decisions.jsonl   ← 技术决策
        └── agent_789ghi/
            └── ...
```

### 10.2 目录职责

| 目录 | 内容 | 生命周期 |
|------|------|---------|
| `sessions/` | 会话快照（对话摘要 + 任务进度） | 自动清理（保留 20 条） |
| `memory/projects/` | 项目级记忆（类型、工具链、关键词） | 永久 |
| `memory/agents/` | Agent 级记忆（知识、决策、对话历史） | 永久 |

---

## 11. 恢复流程（完整链路）

### 11.1 启动恢复

```
用户: $ zmai
         │
         ▼
    CLI main()
         │
         ├── detect.py:detect()
         │   └── 找到项目根 → ProjectInfo {name: "my-project"}
         │
         ├── MemoryManager(project="my-project")
         │   │
         │   ├── ProjectMemory("my-project") → 加载项目记忆
         │   │   ├── project_info: {type: "python", version: "3.13"}
         │   │   ├── sessions: [...]  ← 历史会话列表
         │   │   └── keywords: ["auth", "jwt"]  ← 项目关键词
         │   │
         │   ├── SessionManager.get_latest()
         │   │   └── → ses_abc123 (paused, task="重构 auth 模块")
         │   │
         │   └── 如果存在未完成会话 → 提示用户恢复
         │       "检测到上次未完成任务: 重构 auth 模块 (进度 5/8)"
         │       "恢复并继续？[Y/n]: Y"
         │
         ▼
    用户确认恢复
         │
         ├── 1. SessionManager.resume("ses_abc123")
         │      → status: "running"
         │
         ├── 2. LongTermMemory.restore("agent_def456")
         │      → task.jsonl → TaskState
         │      → conversation.jsonl → 最近 20 条消息
         │      → knowledge.jsonl → 学到的知识
         │
         ├── 3. 构建恢复 Prompt
         │      → "继续重构 auth 模块。你之前完成了..."
         │      → "最近修改的文件: login.py, middleware.py"
         │      → "下一步: 为 auth 模块添加测试"
         │
         └── 4. 注入 Agent Context → 开始执行
```

### 11.2 退出保存

```
用户: Ctrl+C / exit / EOF
         │
         ▼
    Shutdown Hook
         │
         ├── 1. SessionManager.pause()
         │      → ses_abc123.status = "paused"
         │      → ses_abc123.current_focus = "正在修复 token 刷新"
         │
         ├── 2. ProjectMemory.add_session(session_data)
         │      → my-project.jsonl 追加一条会话
         │
         ├── 3. LongTermMemory.persist_all()
         │      → 所有 Working → JSONL 追加写入
         │
         ├── 4. CheckpointManager.shutdown_checkpoint()
         │      → 强制刷新所有文件
         │
         └── 5. output("Bye 👋")
```

### 11.3 恢复决策

| 场景 | 检测方式 | 行为 |
|------|---------|------|
| 首次启动 | 无 Session / Project Memory | 从零开始 |
| 同项目继续 | `latest.json` 中 session 的项目 == 当前项目 | 自动提示恢复 |
| 换项目 | `latest.json` 项目 != 当前项目 | 加载新项目记忆，不恢复旧 Session |
| 同项目新任务 | 用户输入新任务而非恢复 | 可选：先保存旧 Session，再创建新 Session |
| 意外退出 | Session 状态为 "running"（非 "paused"） | 检测到异常退出，自动恢复 |

---

## 12. 接口设计

### 12.1 MemoryManager 新接口

```python
class MemoryManager:
    """记忆管理器。统一入口，编排三层记忆。"""

    def __init__(self, project: str = ""):
        # 三层
        self._working: dict[str, WorkingMemory] = {}
        self._project: ProjectMemory | None = None
        self._long_term: dict[str, LongTermMemory] = {}

        # 会话
        self._sessions = SessionManager()

        # Checkpoint
        self._checkpointer = CheckpointManager(self)

        # 如果指定了项目，立即加载 Project Memory
        if project:
            self._project = ProjectMemory(project)

    # ── 三层访问 ──

    def working(self, agent_id: str) -> WorkingMemory:
        """获取 Agent 的 Working Memory（延迟创建）。"""
        if agent_id not in self._working:
            self._working[agent_id] = WorkingMemory()
        return self._working[agent_id]

    def project(self) -> ProjectMemory | None:
        """获取 Project Memory。"""
        return self._project

    def long_term(self, agent_id: str) -> LongTermMemory:
        """获取 Agent 的 Long-term Memory（延迟创建）。"""
        if agent_id not in self._long_term:
            self._long_term[agent_id] = LongTermMemory(
                root_dir=Path.home() / ".zmai" / "memory" / "agents" / agent_id,
            )
        return self._long_term[agent_id]

    # ── 会话管理 ──

    def create_session(self, agent_id: str, task: str) -> str:
        """创建新会话。"""
        project_name = self._project.get_project_info().get("name", "default") if self._project else "default"
        return self._sessions.create(project_name, agent_id, task)

    def get_session(self) -> SessionManager:
        return self._sessions

    # ── 恢复 ──

    def restore(self, agent_id: str) -> RestoreSummary:
        """恢复 Agent 的所有记忆。

        从 Project Memory + Long-term Memory 恢复：
          - Task 状态
          - 对话历史
          - 项目上下文
        """
        summary = RestoreSummary()

        # 1. 恢复 Long-term Memory
        lm = self.long_term(agent_id)
        task_data = lm.read("task", namespace="task")
        if task_data:
            summary.task_state = TaskState(**task_data)
            summary.restored_items += 1

        # 2. 恢复对话历史
        conv = ConversationLog(
            self._agent_memory_path(agent_id) / "conversation.jsonl",
        )
        recent = conv.get_recent(20)
        if recent:
            summary.conversation = recent
            summary.restored_items += len(recent)

        # 3. 恢复项目上下文
        if self._project:
            proj_info = self._project.get_project_info()
            summary.project_context = proj_info
            summary.restored_items += 1

        return summary

    def flush_all(self) -> None:
        """强制持久化所有记忆（退出时调用）。"""
        for agent_id in self._long_term:
            lm = self._long_term[agent_id]
            lm.persist_all()  # 确保所有缓存写入磁盘
        if self._project:
            self._project.flush()

    # ── Checkpoint ──

    def checkpoint(self, force: bool = False) -> None:
        """自动 Checkpoint。"""
        self._checkpointer.maybe_checkpoint(force=force)


@dataclass
class RestoreSummary:
    """恢复摘要。注入 Agent 时使用。"""
    task_state: TaskState | None = None
    conversation: list[dict[str, Any]] = field(default_factory=list)
    project_context: dict[str, Any] = field(default_factory=dict)
    restored_items: int = 0
    session_data: dict[str, Any] = field(default_factory=dict)

    def to_prompt(self) -> str:
        """生成注入 Agent 的恢复 Prompt。"""
        parts = ["", "=== 记忆恢复 ===", f"恢复条目: {self.restored_items}", ""]

        if self.task_state:
            parts.append(self.task_state.summary())
            parts.append("")

        if self.conversation:
            last = self.conversation[-1]
            role = last.get("role", "?")
            content = last.get("content", "")[:200]
            parts.append(f"上一条消息 ({role}): {content}")
            parts.append("")

        if self.project_context:
            parts.append(f"项目类型: {self.project_context.get('type', '?')}")
            if self.project_context.get("test_framework"):
                parts.append(f"测试框架: {self.project_context['test_framework']}")

        return "\n".join(parts)
```

### 12.2 Runtime 集成

Runtime 不需要修改。MemoryManager 作为外部资源传入 Agent：

```python
# runtime.py — 集成点（唯一修改）
async def run(self, agent_id, task, ..., memory_manager=None):
    ws_path = self._workspace.prepare(agent_id)

    # 如果提供了 MemoryManager，恢复记忆
    if memory_manager:
        summary = memory_manager.restore(agent_id)
        # 注入到 AgentContext
        ctx.metadata["restore_summary"] = summary

    # 任务循环中自动 checkpoint
    while ...:
        action = await agent.step(ctx)
        if memory_manager:
            memory_manager.checkpoint()  # 每步自动 checkpoint
        ...

    # 退出时保存
    if memory_manager:
        memory_manager.get_session().pause()
        memory_manager.flush_all()
```

**注意：** 这是 runtime.py 中唯一的新增代码。不修改现有逻辑，仅在适当位置插入钩子。

---

## 13. 文件清单与实现计划

### 13.1 新增文件

```
src/zmai/memory/
├── project.py              # 🔴 新增 — Project Memory（项目级持久化）
├── session.py              # 🔴 新增 — Session Manager（会话生命周期）
├── task.py                 # 🔴 新增 — Task State（任务状态追踪）
├── checkpoint.py           # 🔴 新增 — Checkpoint Manager（自动保存）
└── conversation.py         # 🔴 新增 — Conversation Log（对话历史持久化）
```

### 13.2 重写文件

```
src/zmai/memory/
├── __init__.py             # 🔧 更新导出
├── manager.py              # 🔧 重写 — 三层编排 + 恢复 + 会话 + checkpoint
├── working.py              # 🔧 重写 — LRU 逐出（原抛异常），新增 snapshot()
└── long_term.py            # 🔧 重写 — 追加写入（原全量重写）
```

### 13.3 不变文件

```
src/zmai/memory/base.py     ✅ 不变（MemoryEntry + Memory 抽象基类）
src/zmai/runtime/*          ✅ 不变（仅添加钩子调用点）
src/zmai/agent/*            ✅ 不变
src/zmai/gateway/*          ✅ 不变
src/zmai/workspace/*        ✅ 不变
src/zmai/workflow/*         ✅ 不变
src/zmai/cli/*              ✅ 不变
```

### 13.4 代码量变化

```
v1.0:
  base.py           71 行
  working.py        67 行
  long_term.py     104 行
  manager.py        63 行
  总计              305 行

v2.0:
  base.py           71 行（不变）
  working.py        80 行（+13, LRU + snapshot）
  long_term.py     110 行（+6, 追加写入）
  project.py       150 行（新增）
  session.py       130 行（新增）
  task.py           80 行（新增）
  checkpoint.py     70 行（新增）
  conversation.py   70 行（新增）
  manager.py       160 行（重写）
  总计              921 行（+616 行，但功能大幅增加）
```

### 13.5 实现优先级

```
P0 — 恢复基础（1.5 天）
├── manager.py 重写         — 三层编排 + restore() 实现
├── session.py              — Session 生命周期 + latest.json
├── project.py              — Project Memory 基础
└── working.py 优化          — LRU 逐出 + snapshot()

P1 — 持久化（1 天）
├── long_term.py 优化       — 追加写入 + persist_all()
├── conversation.py          — 对话历史持久化
├── task.py                  — Task State 追踪
└── checkpoint.py            — 自动 checkpoint + 退出保存

P2 — 恢复体验（0.5 天）
├── RestoreSummary.to_prompt() — 注入 Agent 的恢复 prompt
├── 多会话列表               — 选择恢复哪个会话
└── 异常退出检测              — status=running 的自动处理
```

---

> **总结：**
>
> ZMAI Memory v2.0 从"KV 存储"进化为"三层记忆系统"：
>
> 1. **三层架构** — Working（内存速记）→ Project（项目持久化）→ Long-term（Agent 知识）
> 2. **Session 恢复** — 关闭 ZMAI 后第二天回来，自动找到未完成任务，恢复对话历史和 Task 进度
> 3. **Project Memory（新增）** — 项目级上下文持久化，跨 Agent 跨会话共享
> 4. **Task 状态追踪（新增）** — 结构化的任务进度、文件修改、焦点追踪，注入 Agent Prompt
> 5. **Conversation Log（新增）** — 对话历史追加写入，恢复时注入最近的上下文
> 6. **自动 Checkpoint** — 每步 Agent step 后增量保存，退出时全量持久化，异常退出不丢数据
> 7. **性能优化** — `store()` 从 O(n) 全量写变为 O(1) 追加写
>
> **关闭 ZMAI → 第二天回来 → `zmai` → "继续昨天的 auth 重构？" → "Y" → 接着干。**
