# ZMAI Memory System v1 设计

> 版本: 1.0
> 日期: 2026-07-21
> 存储: 结构化 JSON（无向量数据库）
> 约束: 不修改现有 Runtime 核心代码

---

## 目录

- [1. 现有系统分析](#1-现有系统分析)
- [2. v1 核心概念](#2-v1-核心概念)
- [3. 用户数据模型](#3-用户数据模型)
- [4. 命名空间规范](#4-命名空间规范)
- [5. CLI 命令设计](#5-cli-命令设计)
- [6. 敏感信息保护](#6-敏感信息保护)
- [7. Agent 集成方式](#7-agent-集成方式)
- [8. 存储架构](#8-存储架构)
- [9. 单元测试规范](#9-单元测试规范)
- [10. 文件规划](#10-文件规划)

---

## 1. 现有系统分析

### 1.1 已有代码（可直接复用）

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| `MemoryEntry` | `memory/base.py` | ✅ 完整 | key/value/namespace/ttl/created_at/updated_at |
| `WorkingMemory` | `memory/working.py` | ✅ 完整 | 内存 dict，LRU 淘汰，TTL 过期 |
| `LongTermMemory` | `memory/long_term.py` | ✅ 完整 | JSONL 文件持久化，append-only，墓碑删除 |
| `MemoryManager` | `memory/manager.py` | ✅ 完整 | Working+LongTerm 配对，persist/restore |
| `MemoryEntry` 测试 | `tests/test_memory.py` | ✅ 33 个测试 | 全部通过 |

### 1.2 现有使用方式

```python
# Runtime 初始化
self._memory = MemoryManager()

# 每个 Agent 启动时从长期记忆恢复
self._memory.restore(agent_id)

# SWEAgent 每次 step 注入记忆上下文
wm = context.memory.working(context.agent_id)
mem_items = wm.search("")  # 全部条目
# 最多 10 条 → 注入 system_prompt

# 每次 tool call 后自动保存
context.memory.working(context.agent_id).store(
    f"tool:{tc.name}", {...}, namespace="tools"
)

# Agent 完成后持久化
self._memory.persist(agent_id)
```

### 1.3 v1 需要新增的能力

| 能力 | 当前 | v1 目标 |
|------|------|---------|
| 用户可查看 | ❌ | `zmai memory list` |
| 用户可搜索 | ❌ | `zmai memory list --search <q>` |
| 用户可修改 | ❌ | `zmai memory set <key> <value>` |
| 用户可删除 | ❌ | `zmai memory delete <key>` / `zmai memory clear` |
| 用户可导出 | ❌ | `zmai memory export [--format json\|md]` |
| 用户可关闭 | ❌ | `zmai memory disable` / `zmai memory enable` |
| 用户偏好保存 | ❌ | 用户说"记住 XX" → 保存 |
| 敏感信息过滤 | ❌ | API Key 等不被自动保存 |
| 信息分类查看 | ❌ | 按 namespace 查看各类记忆 |
| 记忆统计 | ❌ | 总条目数、各 namespace 大小 |

---

## 2. v1 核心概念

### 2.1 记忆分类

```
用户记忆（User Memory）
├── preferences     → 用户偏好（主题、默认模型、输出风格等）
├── facts           → 用户明确要求记住的事实
├── projects        → 项目级上下文
└── history         → 历史任务摘要（自动保存）

系统记忆（System Memory）— 自动维护，用户可通过 CLI 管理
├── tools           → 常用工具使用记录
├── interactions    → 交互模式
└── learnings       → Agent 从任务中总结的经验
```

### 2.2 存储路径

```
~/.zmai/memory/
├── preferences.jsonl     # 用户偏好
├── facts.jsonl           # 用户要求记住的信息
├── projects.jsonl        # 项目上下文
├── history.jsonl         # 历史任务摘要
├── tools.jsonl           # 工具使用记录
├── interactions.jsonl    # 交互模式
└── system.jsonl          # 系统标记（enable/disable, version 等）
```

### 2.3 记忆条目格式

每条记忆是一个 JSONL 行：

```json
{
  "key": "theme",
  "value": "dark",
  "namespace": "preferences",
  "source": "user",
  "created_at": "2026-07-21T10:00:00Z",
  "updated_at": "2026-07-21T10:00:00Z",
  "ttl": null,
  "tags": ["cli", "display"],
  "summary": "用户偏好暗色主题"
}
```

相比现有的 `MemoryEntry`，v1 扩展字段：

| 字段 | 现有 | v1 | 说明 |
|------|------|----|------|
| `key` | ✅ | ✅ | 唯一标识 |
| `value` | ✅ | ✅ | 任意 JSON 值 |
| `namespace` | ✅ | ✅ | 分类命名空间 |
| `created_at` | ✅ | ✅ | 创建时间 |
| `updated_at` | ✅ | ✅ | 更新时间 |
| `ttl` | ✅ | ✅ | 过期时间（秒） |
| `source` | ❌ | **新增** | `"user"` 或 `"agent"`，用于过滤和可信度提示 |
| `tags` | ❌ | **新增** | 标签数组，方便搜索和分类 |
| `summary` | ❌ | **新增** | 人类可读摘要（CLI 列表时显示） |

### 2.4 MemoryStore（v1 新增门面类）

```python
class MemoryStore:
    """v1 Memory 门面。
    
    封装 LongTermMemory，提供：
    - 带分类的存取
    - 敏感信息过滤
    - 用户友好查询
    - 统计/导出
    """
    
    _SENSITIVE_PATTERNS = [
        r"sk-[a-zA-Z0-9]{20,}",       # API Key 格式
        r"AKIA[0-9A-Z]{16}",           # AWS Access Key
        r"-----BEGIN.*PRIVATE KEY-----",  # 私钥
    ]
    _SENSITIVE_KEYS = [
        "api_key", "apikey", "api-key", "secret",
        "password", "token", "credential",
        "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
    ]
    _SENSITIVE_NAMESPACES = ["credentials", "secrets", "tokens"]
```

---

## 3. 用户数据模型

### 3.1 用户偏好（preferences namespace）

```python
# 自动/手动保存的用户偏好
PREFERENCE_KEYS = {
    "theme": {"type": "str", "enum": ["dark", "light", "plain"]},
    "default_backend": {"type": "str"},
    "default_model": {"type": "str"},
    "output_format": {"type": "str", "enum": ["text", "json"]},
    "language": {"type": "str"},
    "max_tokens": {"type": "int"},
    "temperature": {"type": "float"},
}
```

存储示例：
```json
{"key": "theme", "value": "dark", "namespace": "preferences", "source": "user", "tags": ["cli"], "summary": "终端暗色主题"}
{"key": "default_backend", "value": "claude", "namespace": "preferences", "source": "user", "tags": ["runtime"], "summary": "默认模型后端"}
```

### 3.2 用户事实（facts namespace）

用户明确说"记住 XXX"时保存。

```json
{"key": "my_email", "value": "user@example.com", "namespace": "facts", "source": "user", "tags": ["contact"], "summary": "用户邮箱"}
{"key": "project_repo", "value": "https://github.com/user/zmai", "namespace": "facts", "source": "user", "tags": ["project"], "summary": "项目仓库地址"}
```

### 3.3 项目上下文（projects namespace）

按项目路径或名称索引。

```json
{"key": "zmai", "value": {"language": "python", "framework": "none", "test": "pytest", "build": "setuptools"}, "namespace": "projects", "source": "agent", "tags": ["python", "cli"], "summary": "ZMAI 项目特征"}
{"key": "my_web_app", "value": {"language": "typescript", "framework": "react", "test": "vitest"}, "namespace": "projects", "source": "agent", "tags": ["web", "react"], "summary": "Web 项目特征"}
```

### 3.4 历史任务摘要（history namespace）

Agent 每次任务完成后自动保存摘要。

```json
{
  "key": "2026-07-21_001",
  "value": {
    "task": "创建 HTML 文件并打开",
    "status": "completed",
    "tools_used": ["write_file", "open_in_browser"],
    "key_files": ["output/index.html"],
    "duration_seconds": 45
  },
  "namespace": "history",
  "source": "agent",
  "tags": ["html", "delivery"],
  "summary": "创建 HTML 文件并浏览器打开（成功）"
}
```

### 3.5 工具偏好（tools namespace）

Agent 自动记录常用的工具和参数模式。

```json
{"key": "read_file_frequency", "value": 12, "namespace": "tools", "source": "agent", "summary": "read_file 已使用 12 次"}
{"key": "preferred_shell", "value": {"cmd": "python", "reason": "cross-platform"}, "namespace": "tools", "source": "agent", "summary": "常用 Python 执行脚本"}
```

---

## 4. 命名空间规范

### 4.1 完整列表

| 命名空间 | 源 | 用途 | 用户可删？ | 存活期 |
|----------|-----|------|-----------|--------|
| `preferences` | user+agent | 用户偏好设置 | ✅ | 永久 |
| `facts` | user | 用户要求记住的事实 | ✅ | 永久 |
| `projects` | agent | 项目检测结果 | ✅ | 手动清理 |
| `history` | agent | 历史任务摘要 | ✅ | 自动上限 100 条 |
| `tools` | agent | 工具使用统计 | ✅ | 自动上限 50 条 |
| `interactions` | agent | 交互模式 | ✅ | 自动上限 50 条 |

### 4.2 命名空间配额

| 命名空间 | 最大条目 | 超出策略 |
|----------|---------|---------|
| `preferences` | 50 | 拒绝新条目 |
| `facts` | 200 | 淘汰最旧 |
| `projects` | 50 | 淘汰最旧 |
| `history` | 100 | 淘汰最旧 |
| `tools` | 50 | 淘汰最旧 |
| `interactions` | 50 | 淘汰最旧 |

### 4.3 保留 key 前缀

```
system:       — 系统内部使用（不可手动修改）
agent:        — Agent 自动记录的元信息
```

---

## 5. CLI 命令设计

### 5.1 命令树

```
zmai memory
├── list              # 列出所有记忆条目
│   ├── --namespace   # 按命名空间过滤
│   ├── --search / -s # 搜索关键词
│   ├── --source      # 按来源过滤 (user|agent)
│   ├── --tag / -t    # 按标签过滤
│   ├── --limit / -l  # 限制条数
│   └── --json        # JSON 输出
│
├── show <key>        # 查看单条详情
│   └── --namespace   # 指定命名空间
│
├── set <key> <value> # 设置记忆条目
│   ├── --namespace   # 指定命名空间（默认 facts）
│   ├── --tags        # 标签
│   └── --summary     # 摘要
│
├── delete <key>      # 删除单条
│   └── --namespace   # 指定命名空间
│
├── clear             # 清除全部（或指定 namespace）
│   ├── --namespace   # 仅清除指定命名空间
│   └── --force       # 跳过确认
│
├── export            # 导出
│   ├── --namespace   # 仅导出指定命名空间
│   ├── --format      # json | md（默认 json）
│   └── --output      # 输出文件路径
│
├── stats             # 统计信息
│
├── enable            # 开启记忆功能
│
├── disable           # 关闭记忆功能
│
└── status            # 查看记忆系统状态
```

### 5.2 命令详细设计

#### `zmai memory list`

```
zmai memory list
  → 显示所有命名空间及其条目数
  → 默认只显示 summary 和 key（不显示完整 value）

输出:
  Memory (总 24 条)
  ─────────────────────────────────────────────
  preferences     3 条
    ├─ theme                 暗色主题
    ├─ default_backend       claude
    └─ output_format         text

  facts            2 条
    ├─ my_email              user@example.com
    └─ project_repo          github.com/user/zmai

  history          15 条
    ├─ 2026-07-21_001        创建 HTML 文件 (成功)
    ├─ 2026-07-20_005        修复测试 (失败)
    └─ ... (最多显示 10 条)

  tools            4 条
    ├─ read_file_frequency   12 次
    └─ ...

zmai memory list --search html
  → 只显示包含 "html" 的条目

zmai memory list --namespace history --limit 5
  → 只显示 history 命名空间的最新 5 条

zmai memory list --source user
  → 只显示用户主动保存的条目

zmai memory list --json
  → JSON 格式输出（供脚本使用）
```

#### `zmai memory show`

```
zmai memory show theme
  → 在所有命名空间中查找 key=theme，显示详情

zmai memory show my_email --namespace facts
  → 在 facts 命名空间中查找

输出:
  Key:       my_email
  Value:     user@example.com
  Namespace: facts
  Source:    user
  Tags:      [contact]
  Created:   2026-07-21T10:00:00Z
  Updated:   2026-07-21T10:00:00Z
  Summary:   用户邮箱
```

#### `zmai memory set`

```
zmai memory set theme dark
  → 保存/更新偏好条目

zmai memory set my_email "user@example.com" --namespace facts --tags contact --summary "我的邮箱"
  → 在 facts 命名空间保存

zmai memory set project_repo '{"url": "https://...", "language": "python"}' --json
  → 保存 JSON value
```

#### `zmai memory delete`

```
zmai memory delete my_email
  → 删除 key=my_email 的条目（跨所有 namespace 搜索）

zmai memory delete my_email --namespace facts
  → 指定 namespace 删除
```

#### `zmai memory clear`

```
zmai memory clear
  → 确认后清除所有记忆（保留 system 标记）
  Are you sure? [y/N]

zmai memory clear --namespace history
  → 只清除历史任务摘要

zmai memory clear --namespace preferences --force
  → 强制清除，跳过确认
```

#### `zmai memory export`

```
zmai memory export
  → 导出全部记忆为 JSON 文件，打印路径

zmai memory export --namespace history --format md --output history.md
  → 导出 history 为 Markdown 文档

zmai memory export --format md
  → 导出全部为人类可读的 Markdown 报告
```

#### `zmai memory stats`

```
zmai memory stats
  → 显示记忆系统统计

输出:
  Memory System Status
  ──────────────────────────────────
  Status:       Enabled
  Storage:      ~/.zmai/memory/ (4.2 KB)
  Total:        24 条目
  By namespace:
    preferences:    3 条目 (0.5 KB)
    facts:          2 条目 (0.3 KB)
    history:       15 条目 (2.8 KB)
    tools:          4 条目 (0.6 KB)
  By source:
    user:           5 条目
    agent:         19 条目
```

#### `zmai memory enable / disable / status`

```
zmai memory disable
  → 写入 system 标记文件，Agent 将不再读取/写入记忆

zmai memory enable
  → 移除 disable 标记

zmai memory status
  → 显示当前状态（enabled/disabled），存储路径，条目数
```

### 5.3 CLI 输出的敏感信息脱敏

任何 CLI 输出中，value 字段若匹配敏感模式，显示为 `***`：

```
zmai memory show my_api_key
  → Value: *** (sensitive)   # 不显示真实值
```

---

## 6. 敏感信息保护

### 6.1 自动过滤

在 `MemoryStore.store()` 中，每次写入前检查：

```python
class SensitiveDataFilter:
    """敏感信息过滤器。拦截 API Key、密码等。"""

    _PATTERNS = [
        re.compile(r"sk-[a-zA-Z0-9]{20,}"),           # Anthropic/DeepSeek Key
        re.compile(r"AKIA[0-9A-Z]{16}"),               # AWS Access Key
        re.compile(r"-----BEGIN.*PRIVATE KEY-----"),   # 私钥
        re.compile(r"ghp_[a-zA-Z0-9]{36}"),            # GitHub Token
        re.compile(r"gho_[a-zA-Z0-9]{36}"),            # GitHub OAuth
        re.compile(r"xox[bpsa]-[a-zA-Z0-9-]{10,}"),    # Slack Token
        re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"),  # JWT
    ]

    _SENSITIVE_KEYS = {
        "api_key", "apikey", "api-key", "api.secret",
        "secret", "secret_key", "secretkey",
        "password", "passwd", "pass",
        "token", "access_token", "refresh_token",
        "credential", "credentials",
        "auth", "authorization",
        "private_key", "private-key",
        "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY", "GEMINI_API_KEY",
    }

    _SENSITIVE_NAMESPACES = {
        "credentials", "secrets", "tokens", "keys",
    }

    @classmethod
    def contains_sensitive(cls, value: Any) -> bool:
        """检查值是否包含敏感信息。"""
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict):
            # 检查 dict 的 key 名
            if any(k.lower() in cls._SENSITIVE_KEYS for k in value):
                return True
            text = json.dumps(value)
        else:
            text = str(value)

        # 检查值内容
        for pattern in cls._PATTERNS:
            if pattern.search(text):
                return True
        return False

    @classmethod
    def filter(cls, key: str, value: Any, namespace: str) -> tuple[bool, str]:
        """过滤敏感信息。
        
        Returns:
            (blocked: bool, reason: str)
            blocked=True → 该条目被阻止保存
        """
        # 检查命名空间
        if namespace.lower() in cls._SENSITIVE_NAMESPACES:
            return True, f"命名空间 '{namespace}' 被禁止"

        # 检查 key 名
        if key.lower() in cls._SENSITIVE_KEYS:
            return True, f"Key '{key}' 可能包含敏感信息"

        # 检查值内容
        if cls.contains_sensitive(value):
            return True, "值包含疑似 API Key 或凭据"

        return False, ""
```

### 6.2 禁止规则

```
❌ 禁止自动保存:
   - ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GEMINI_API_KEY 值
   - 任何匹配 sk-* 格式的字符串
   - ~/.zmai/credentials 文件内容
   - 环境变量中的 API Key
   - 任何密码/密钥/Token

✅ 允许保存:
   - 用户偏好（主题、输出格式等）
   - 项目技术栈信息
   - 历史任务摘要（不含凭据）
   - 工具使用统计
   - 用户明确要求保存的事实
```

### 6.3 安全写入流程

```
MemoryStore.store(key, value, namespace)
  │
  ├─ SensitiveDataFilter.filter(key, value, namespace)
  │    │
  │    ├─ blocked=True  → 日志警告 + 拒绝写入 + 返回错误
  │    │
  │    └─ blocked=False → 继续
  │
  └─ LongTermMemory.store(key, value, namespace)
```

---

## 7. Agent 集成方式

### 7.1 自动保存时机

| 时机 | 内容 | namespace | 条件 |
|------|------|-----------|------|
| 任务完成 | 任务摘要 | `history` | `finalize()` 中 |
| 工具调用 | 工具名+成功/失败 | `tools` | `step()` 中 |
| 用户偏好 | 检测到的偏好 | `preferences` | 用户明确表达时 |
| 项目检测 | 项目类型/技术栈 | `projects` | 首次检测到时 |

### 7.2 用户命令保存

当用户说"记住 XXX"或"保存 XXX"时，Agent 调用：

```python
# 在 SWEAgent 中新增系统 Prompt 指令
"""
当用户明确要求你记住某条信息时（例如 "记住"、"保存"、“记一下”）：
  执行 show_to_user 告知用户已保存
"""

# 实际保存由 MemoryStore.set() CLI 命令完成
# 或 Agent 通过工具调用 memory_store 工具（v2 可选）
```

### 7.3 记忆注入方式（每次 step）

当前 SWEAgent 已支持记忆注入：

```python
# 已存在的逻辑（swe/agent.py step()）
wm = context.memory.working(context.agent_id)
mem_items = wm.search("")  # 全部条目
if mem_items:
    mem_lines = []
    for e in mem_items[:10]:
        val_str = str(e.value)[:120]
        mem_lines.append(f"- {e.key}: {val_str}")
    memory_context = "\n## 记忆上下文\n" + "\n".join(mem_lines) + "\n"
```

v1 增强为按命名空间分类注入：

```python
def _build_memory_context(wm) -> str:
    """构建分层记忆上下文。"""
    sections = []

    # 1. 用户事实（最高优先级）
    facts = wm.search("", namespace="facts")
    if facts:
        lines = [f"  - {e.key}: {str(e.value)[:100]}" for e in facts[:5]]
        sections.append("## 用户记住的信息\n" + "\n".join(lines))

    # 2. 用户偏好
    prefs = wm.search("", namespace="preferences")
    if prefs:
        lines = [f"  - {e.key}: {str(e.value)[:100]}" for e in prefs[:5]]
        sections.append("## 用户偏好\n" + "\n".join(lines))

    # 3. 项目上下文
    projs = wm.search("", namespace="projects")
    if projs:
        lines = [f"  - {e.key}: {str(e.value)[:100]}" for e in projs[:3]]
        sections.append("## 项目上下文\n" + "\n".join(lines))

    # 4. 历史摘要（最近 3 条）
    history = wm.search("", namespace="history")
    if history:
        recent = sorted(history, key=lambda e: e.created_at, reverse=True)[:3]
        lines = [f"  - {e.summary or str(e.value)[:80]}" for e in recent]
        sections.append("## 最近任务\n" + "\n".join(lines))

    return "\n\n".join(sections) if sections else ""
```

### 7.4 enable/disable 控制

```python
# MemoryStore.check_enabled() → 读取 ~/.zmai/memory/.enabled 标记
# disable 时:
#   - Agent 不读取记忆（不注入 system prompt）
#   - Agent 不写入记忆（不保存历史/tools）
#   - CLI 命令不受影响（用户可以手动 list/export）
```

---

## 8. 存储架构

### 8.1 完整路径结构

```
~/.zmai/memory/                       # Memory 根目录
├── .enabled                          # 标记文件（存在=启用，不存在=禁用）
├── preferences.jsonl                 # 用户偏好
├── facts.jsonl                       # 用户事实
├── projects.jsonl                    # 项目上下文
├── history.jsonl                     # 历史任务摘要
├── tools.jsonl                       # 工具使用记录
├── interactions.jsonl                # 交互模式
└── v1.json                           # 元信息（版本、配额等）
```

### 8.2 存储格式（JSONL — 复用现有 LongTermMemory）

当前 `LongTermMemory` 已经支持：
- JSONL append-only 写入（O(1)）
- 后写入覆盖前读取
- 墓碑标记删除
- 按 namespace 分文件

**v1 完全复用此机制**，无需新存储引擎。

### 8.3 元信息文件（v1.json）

```json
{
  "version": "1.0",
  "created_at": "2026-07-21T10:00:00Z",
  "updated_at": "2026-07-21T10:00:00Z",
  "enabled": true,
  "namespaces": {
    "preferences": {"max_entries": 50, "current": 3},
    "facts": {"max_entries": 200, "current": 2},
    "history": {"max_entries": 100, "current": 15},
    "tools": {"max_entries": 50, "current": 4},
    "interactions": {"max_entries": 50, "current": 0}
  }
}
```

### 8.4 数据流

```
用户 CLI 操作                   Agent 自动操作
    │                              │
    ▼                              ▼
MemoryStore                     MemoryStore
    │                              │
    ├─ set(key, val, ns)           ├─ store_history(task_summary)
    ├─ get(key, ns)                ├─ store_tool_usage(tool_name)
    ├─ delete(key, ns)             ├─ store_project_context(ctx)
    ├─ clear(ns)                   └─ load_context(agent_id)
    ├─ export(ns, format)
    └─ stats()
         │
         ▼
SensitiveDataFilter.filter(key, value, namespace)
         │
    ┌────┴────┐
    │ 通过     │ 阻止
    ▼          ▼
 LongTermMemory  返回错误 + 日志警告
 .store()
```

---

## 9. 单元测试规范

### 9.1 测试文件

```python
# tests/test_memory_v1.py
```

### 9.2 SensitiveDataFilter 测试

```python
class TestSensitiveDataFilter:
    def test_block_api_key_string(self):
        """API Key 格式字符串被拦截。"""
        blocked, reason = SensitiveDataFilter.filter(
            "my_key", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "preferences"
        )
        assert blocked is True
        assert "API Key" in reason or "敏感" in reason

    def test_block_deepseek_key(self):
        """DeepSeek API Key 被拦截。"""
        blocked, reason = SensitiveDataFilter.filter(
            "key", "sk-abcdefghijklmnopqrstuvwxyz123456", "facts"
        )
        assert blocked is True

    def test_block_sensitive_key_name(self):
        """key 名称含敏感词时被拦截。"""
        blocked, reason = SensitiveDataFilter.filter(
            "api_key", "some_value", "preferences"
        )
        assert blocked is True

    def test_block_sensitive_namespace(self):
        """敏感命名空间被拦截。"""
        blocked, reason = SensitiveDataFilter.filter(
            "anything", "anything", "credentials"
        )
        assert blocked is True

    def test_allow_normal_value(self):
        """正常值通过过滤。"""
        blocked, reason = SensitiveDataFilter.filter(
            "theme", "dark", "preferences"
        )
        assert blocked is False

    def test_allow_normal_fact(self):
        """正常事实通过过滤。"""
        blocked, reason = SensitiveDataFilter.filter(
            "my_email", "user@example.com", "facts"
        )
        assert blocked is False

    def test_block_jwt_token(self):
        """JWT Token 被拦截。"""
        blocked, reason = SensitiveDataFilter.filter(
            "auth", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNvrPwGjqPjJ", "facts"
        )
        assert blocked is True

    def test_block_private_key(self):
        """私钥内容被拦截。"""
        blocked, reason = SensitiveDataFilter.filter(
            "key", "-----BEGIN RSA PRIVATE KEY-----\nABCDEF\n-----END RSA PRIVATE KEY-----", "facts"
        )
        assert blocked is True

    def test_allow_json_value(self):
        """JSON value 不包含敏感词时通过。"""
        blocked, reason = SensitiveDataFilter.filter(
            "project", {"language": "python", "test": "pytest"}, "projects"
        )
        assert blocked is False

    def test_block_dict_with_api_key(self):
        """dict 值中包含 api_key 敏感 key 名称时拦截。"""
        blocked, reason = SensitiveDataFilter.filter(
            "config", {"url": "https://example.com", "api_key": "12345"}, "preferences"
        )
        assert blocked is True
```

### 9.3 MemoryStore 测试

```python
class TestMemoryStore:
    def test_store_and_list(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("theme", "dark", namespace="preferences", source="user")
        store.set("email", "a@b.com", namespace="facts", source="user")
        entries = store.list()
        assert len(entries) == 2

    def test_list_by_namespace(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("k1", "v1", namespace="preferences", source="user")
        store.set("k2", "v2", namespace="history", source="agent")
        prefs = store.list(namespace="preferences")
        assert len(prefs) == 1
        assert prefs[0].key == "k1"

    def test_list_with_search(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("theme", "dark", namespace="preferences", source="user", summary="暗色主题")
        store.set("email", "a@b.com", namespace="facts", source="user", summary="邮箱")
        results = store.list(search="theme")
        assert len(results) == 1
        assert results[0].key == "theme"

    def test_list_by_source(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("k1", "v1", namespace="preferences", source="user")
        store.set("k2", "v2", namespace="history", source="agent")
        assert len(store.list(source="user")) == 1
        assert len(store.list(source="agent")) == 1

    def test_show(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("theme", "dark", namespace="preferences", source="user")
        entry = store.show("theme")
        assert entry is not None
        assert entry.value == "dark"

    def test_show_with_namespace(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("theme", "dark", namespace="preferences", source="user")
        store.set("theme", "light", namespace="facts", source="user")
        entry = store.show("theme", namespace="preferences")
        assert entry.value == "dark"

    def test_delete(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("k", "v", namespace="facts", source="user")
        assert store.delete("k") is True
        assert store.show("k") is None

    def test_delete_nonexistent(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        assert store.delete("nobody") is False

    def test_clear_all(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("k1", "v1", namespace="preferences", source="user")
        store.set("k2", "v2", namespace="facts", source="user")
        count = store.clear()
        assert count == 2
        assert len(store.list()) == 0

    def test_clear_namespace(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("k1", "v1", namespace="preferences", source="user")
        store.set("k2", "v2", namespace="facts", source="user")
        count = store.clear(namespace="preferences")
        assert count == 1
        assert len(store.list()) == 1

    def test_stats(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("k1", "v1", namespace="preferences", source="user")
        store.set("k2", "v2", namespace="history", source="agent")
        stats = store.stats()
        assert stats["total"] == 2
        assert stats["namespaces"]["preferences"] == 1
        assert stats["namespaces"]["history"] == 1
        assert stats["by_source"]["user"] == 1
        assert stats["by_source"]["agent"] == 1

    def test_export_json(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("k", "v", namespace="facts", source="user")
        data = store.export()
        assert len(data) == 1
        assert data[0]["key"] == "k"

    def test_export_markdown(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.set("theme", "dark", namespace="preferences", source="user", summary="暗色主题")
        md = store.export(format="md")
        assert "# Memory Export" in md
        assert "theme" in md
        assert "暗色主题" in md

    def test_sensitive_save_blocked(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        with pytest.raises(ValueError, match="敏感|sensitive|API Key"):
            store.set("my_key", "sk-ant-api03-fakekey123", namespace="facts", source="user")

    def test_enable_disable(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        assert store.is_enabled() is True
        store.disable()
        assert store.is_enabled() is False
        store.enable()
        assert store.is_enabled() is True

    def test_quota_enforcement(self, tmp_path):
        store = MemoryStore(root=tmp_path, quotas={"test_ns": 2})
        store.set("a", "1", namespace="test_ns", source="user")
        store.set("b", "2", namespace="test_ns", source="user")
        with pytest.raises(ValueError, match="配额|quota|上限"):
            store.set("c", "3", namespace="test_ns", source="user")
```

### 9.4 CLI 命令集成测试

```python
class TestMemoryCLI:
    def test_memory_list_command(self, runner):
        result = runner.invoke(main, ["memory", "list"])
        assert result.exit_code == 0
        assert "Memory" in result.output

    def test_memory_set_command(self, runner, tmp_path):
        result = runner.invoke(main, ["memory", "set", "test_key", "test_val"])
        assert result.exit_code == 0
        assert "saved" in result.output or "已保存" in result.output

    def test_memory_set_sensitive_rejected(self, runner):
        result = runner.invoke(main, ["memory", "set", "api_key", "sk-ant-xxx"])
        assert result.exit_code != 0
        assert "敏感" in result.output or "sensitive" in result.output.lower()

    def test_memory_show_command(self, runner):
        runner.invoke(main, ["memory", "set", "show_key", "val"])
        result = runner.invoke(main, ["memory", "show", "show_key"])
        assert result.exit_code == 0
        assert "show_key" in result.output

    def test_memory_delete_command(self, runner):
        runner.invoke(main, ["memory", "set", "del_key", "val"])
        result = runner.invoke(main, ["memory", "delete", "del_key"])
        assert result.exit_code == 0

    def test_memory_clear_command(self, runner):
        result = runner.invoke(main, ["memory", "clear", "--force"])
        assert result.exit_code == 0

    def test_memory_export_command(self, runner, tmp_path):
        output = tmp_path / "export.json"
        result = runner.invoke(main, ["memory", "export", "--output", str(output)])
        assert result.exit_code == 0

    def test_memory_stats_command(self, runner):
        result = runner.invoke(main, ["memory", "stats"])
        assert result.exit_code == 0
        assert "Total" in result.output

    def test_memory_disable_enable(self, runner):
        result = runner.invoke(main, ["memory", "disable"])
        assert result.exit_code == 0
        result = runner.invoke(main, ["memory", "enable"])
        assert result.exit_code == 0

    def test_memory_status(self, runner):
        result = runner.invoke(main, ["memory", "status"])
        assert result.exit_code == 0
        assert "Enabled" in result.output or "Disabled" in result.output
```

---

## 10. 文件规划

### 10.1 新增文件

```
src/zmai/memory/
├── __init__.py              # [修改] 导出 MemoryStore, SensitiveDataFilter
├── base.py                  # [不改] MemoryEntry, Memory ABC
├── working.py               # [不改] WorkingMemory
├── long_term.py             # [不改] LongTermMemory
├── manager.py               # [不改] MemoryManager
├── store.py                 # [新增] MemoryStore — v1 门面类
├── filter.py                # [新增] SensitiveDataFilter — 敏感信息过滤
└── cli.py                   # [新增] CLI 命令处理（memory 子命令）

src/zmai/cli/
├── main.py                  # [修改] 添加 "memory" 子命令入口
└── ...                      # [不改] 其他

tests/
├── test_memory.py           # [不改] 现有 33 个测试
├── test_memory_v1.py        # [新增] MemoryStore + SensitiveDataFilter + CLI 测试
└── ...                      # [不改] 其他
```

### 10.2 修改文件

| 文件 | 改动范围 |
|------|---------|
| `src/zmai/memory/__init__.py` | 新增导出 `MemoryStore`, `SensitiveDataFilter` |
| `src/zmai/cli/main.py` | 新增 `"memory"` 子命令路由 → 调用 `memory/cli.py` |

### 10.3 不改文件

| 文件 | 理由 |
|------|------|
| `memory/base.py` | MemoryEntry 已有 source/tags/summary 可加字段，但不改现有构造 |
| `memory/working.py` | 完全复用 |
| `memory/long_term.py` | 完全复用 |
| `memory/manager.py` | 完全复用 |
| `swe/agent.py` | 记忆注入逻辑改进是 v1.1 优化，非必须 |
| `runtime/runtime.py` | 不改 |
| `agent/base.py` | 不改 |
| 全部 `gateway/*` | 不改 |
| 全部 `tool/*` | 不改 |

### 10.4 测试规模

```
新增测试: ~45 个
  └─ TestSensitiveDataFilter:   ~10 个
  └─ TestMemoryStore:           ~18 个
  └─ TestMemoryCLI:             ~12 个
  └─ 集成/边界:                  ~5 个

现有测试零回归: 290+ 全通过
```

---

> **文档结束**
>
> v1 核心：在现有 `LongTermMemory` 之上封装 `MemoryStore`，新增 `SensitiveDataFilter` 防护和 CLI 命令。
> 零改动现有核心代码，所有新增在 `memory/store.py` + `memory/filter.py` + `memory/cli.py` 中。
> 45 个新增测试覆盖全部功能和边界情况。
