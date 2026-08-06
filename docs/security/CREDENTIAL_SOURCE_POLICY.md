# Credential Source Policy — 统一 API Key 来源与优先级

> 审查日期：2026-07-18
> 目标：消除多来源凭据冲突，确保全系统读取逻辑一致

---

## 目录

1. [现状审计](#1-现状审计)
2. [核心问题](#2-核心问题)
3. [设计目标](#3-设计目标)
4. [统一优先级策略](#4-统一优先级策略)
5. [CredentialBundle 增强设计](#5-credentialbundle-增强设计)
6. [显示规范](#6-显示规范)
7. [各模块修改指引](#7-各模块修改指引)
8. [冲突检测与警告](#8-冲突检测与警告)
9. [迁移方案](#9-迁移方案)
10. [测试场景](#10-测试场景)

---

## 1. 现状审计

### 1a. 所有凭据读取路径

| # | 上下文 | 函数/位置 | 解析逻辑 | 优先级 | 使用 Config？ |
|---|--------|-----------|---------|--------|-------------|
| 1 | `zmai doctor` | `Doctor._find_key()` (`doctor.py:98`) | `os.environ.get()` → `AuthStore.get_backend()` | **env → file** | ❌ |
| 2 | `zmai auth doctor` | `_find_auth_key()` (`main.py:875`) | `CredentialResolver().resolve()` | file → env | ❌ |
| 3 | `zmai auth status` | `_run_auth_status()` (`main.py:525`) | `CredentialResolver().resolve()` | file → env | ❌ |
| 4 | Preflight check | `_find_api_key()` (`preflight.py:204`) | `CredentialResolver().resolve()` | file → env | ❌ |
| 5 | Plugin config装配 | `PluginRegistry._build_config()` (`plugin.py:345`) | `CredentialResolver(config).resolve()` | file → config → env | ✅ |
| 6 | Backend 初始化 | `DeepSeekBackend.__init__()` (`deepseek.py:44`) | `config.get("api_key")` → `os.environ.get()` | config → env | ❌（用 config 但无 Resolver） |
| 7 | 启动诊断 | `_print_auth_debug()` (`main.py:828`) | `CredentialResolver().resolve()` | file → env | ❌ |
| 8 | env 注入 | `_inject_one()` (`resolver.py:167`) | `CredentialResolver().resolve()` | file → env | ❌ |

### 1b. 7 个路径 = 3 种不同行为

```
行为 A（env → file）:    路径 #1
行为 B（file → env）:     路径 #2, #3, #4, #7, #8
行为 C（file → config → env）: 路径 #5
行为 D（config → env）:  路径 #6
```

**四种行为在同一个系统中同时存在。**

### 1c. 当前 CredentialBundle 字段

```python
@dataclass
class CredentialBundle:
    api_key: str = ""        # 最终值（已按优先级合并）
    model: str = ""
    base_url: str = ""
    timeout: int = 0
    max_tokens: int = 0
    temperature: float = 0.0
    from_file: bool = False  # 文件是否有 Key（被 env 覆盖后仍为 True）
    from_env: bool = False   # env 是否设了 Key（覆盖文件后为 True）
```

**`from_file` 和 `from_env` 是独立布尔值，无法回答以下问题：**
- 文件中的 Key 和环境变量中的 Key 是否相同？
- 多个来源同时存在时，哪个是最终生效的？
- Config 文件中是否有 Key？

---

## 2. 核心问题

### 问题 1：四套优先级规则并行

| 路径 | 优先级 | 问题 |
|------|--------|------|
| Doctor | env → file | 与 Resolver 方向相反 |
| Resolver (无 config) | file → env | 漏掉了 config 来源 |
| Resolver (有 config) | file → config → env | 唯一完整的路径 |
| DeepSeekBackend | config → env | 漏掉了 file，且没有 Resolver |

### 问题 2：Doctor 使用独立逻辑

`Doctor._find_key()` 自己实现了一套 `os.environ.get()` → `AuthStore.get_backend()` 逻辑，完全绕过 `CredentialResolver`。结果：
- 优先级方向相反（env 优先读取而非优先覆盖）
- 不使用 Config 来源
- 与其他诊断工具（`zmai auth status`）可能显示不同结果

### 问题 3：Backend 实现直接读取环境变量

`DeepSeekBackend.__init__` 第 44 行：
```python
self._api_key: str = c.get("api_key", os.environ.get(self._env_key, ""))
```

直接从 `config` 字典和 `os.environ` 读取，不经过 `CredentialResolver`。这意味着：
- 如果 `_build_config()` 传入了错误的 config，Backend 会自行从 env 找补
- 日志中无法统一追踪 Key 来源
- 每个 Backend 实现的兜底逻辑可能不一致

### 问题 4：冲突检测为零

```
Credentials file: sk-A
Environment var:  sk-B
```
→ 系统使用 `sk-B`，**无任何提示**。

用户在向导中保存 `sk-A`，运行时用 `sk-B`，旧 Key 无效时不知问题在哪。

### 问题 5：来源显示不完整

`_run_auth_status()` 显示的来源只有两个值：
- `C:\Users\...\.zmai\credentials`（`from_file=True`）
- `环境变量`（`from_env=True`）

从不显示 `config`，即使 `CredentialResolver.resolve()` 中有 config 来源步骤。

### 问题 6：`inject_to_env()` 跳过已设环境变量

```python
def _inject_one(self, name: str) -> None:
    if os.environ.get(env_api_key):
        return   # ← 跳过注入
```

用户通过 `zmai auth update` 更新了文件 Key → 但环境变量中的旧 Key 持续生效 → `_inject_one` 跳过 → 运行时始终使用旧 Key。

---

## 3. 设计目标

### 3a. 原则

1. **唯一入口**：所有凭据读取必须通过 `CredentialResolver.resolve()`
2. **唯一优先级**：全系统使用同一套优先级规则
3. **透明来源**：每个读取点都能够确定当前生效的 Key 来自哪个来源
4. **冲突检测**：多来源存在不同 Key 时必须警告
5. **不暴露 Key**：日志/显示/诊断中绝不输出真实 Key

### 3b. 优先级规则（新）

```
最低:   credentials file  (~/.zmai/credentials)
  ↑     config files       (zmai.json / ~/.zmai/config.json)
  ↑     environment vars   (DEEPSEEK_API_KEY)
最高:   CLI 参数           (--api-key)
```

**变更**：CLI 参数作为最高优先级（当前未实现，仅声明位置）。

### 3c. 非目标

- 不改变加密方案（已在 `../review/CREDENTIALS_ENCRYPTION_REVIEW.md` 中处理）
- 不改变 `AuthStore` 的文件读写接口
- 不改变 `BackendPlugin` 的定义

---

## 4. 统一优先级策略

### 4a. 最终优先级

```
┌─────────────────────────────────────────────┐
│  优先级 5（最高）  CLI --api-key override     │ ← 新增
├─────────────────────────────────────────────┤
│  优先级 4         环境变量                     │
│                   DEEPSEEK_API_KEY            │
├─────────────────────────────────────────────┤
│  优先级 3         Config 文件                 │
│                   backends.deepseek.api_key   │
│                   (zmai.json / config.json)   │
├─────────────────────────────────────────────┤
│  优先级 2         Key 文件注入                │
│                   ~/.zmai/credentials.key     │
│                   (仅内部使用，非用户来源)      │
├─────────────────────────────────────────────┤
│  优先级 1（最低）  credentials 文件            │
│                   ~/.zmai/credentials         │
└─────────────────────────────────────────────┘
```

### 4b. 来源定义

| 来源 ID | 名称 | 来源 | 用户可配置？ |
|---------|------|------|------------|
| `credentials_file` | 凭据文件 | `~/.zmai/credentials` | ✅ `zmai auth update` |
| `config_file` | 配置文件 | `zmai.json` / `~/.zmai/config.json` | ✅ 手动编辑 |
| `environment` | 环境变量 | `DEEPSEEK_API_KEY` | ✅ Shell 配置 |
| `cli` | CLI 参数 | `--api-key`（未来） | ✅ 命令行 |
| `missing` | 未配置 | 无来源 | — |

### 4c. 覆盖规则

| 来源 A | 来源 B | 结果 |
|--------|--------|------|
| credentials: sk-A | (无) | 使用 sk-A，来源=file |
| (无) | env: sk-B | 使用 sk-B，来源=env |
| credentials: sk-A | env: sk-B | 使用 sk-B，来源=env |
| credentials: sk-A | config: sk-C | 使用 sk-C，来源=config |
| config: sk-C | env: sk-B | 使用 sk-B，来源=env |
| credentials: sk-A | config: sk-C, env: sk-B | 使用 sk-B，来源=env |

### 4d. 冲突定义

**冲突** = 两个或更多来源（file / config / env）同时包含非空 Key，且不完全相同。

```
冲突示例:
  credentials: sk-Abcdefghijk123
  env:         sk-Xyz1234567890     ← 不同

不冲突示例:
  credentials: sk-Abcdefghijk123
  env:         sk-Abcdefghijk123   ← 相同
```

**冲突检测算法**：Hash 比较。
```python
def _has_conflict(bundle: CredentialBundle) -> bool:
    keys = set()
    if bundle.file_key:
        keys.add(hashlib.sha256(bundle.file_key.encode()).hexdigest())
    if bundle.config_key:
        keys.add(hashlib.sha256(bundle.config_key.encode()).hexdigest())
    if bundle.env_key:
        keys.add(hashlib.sha256(bundle.env_key.encode()).hexdigest())
    return len(keys) > 1
```

---

## 5. CredentialBundle 增强设计

### 5a. 新字段

```python
@dataclass
class CredentialBundle:
    # ── 最终值（按优先级合并后） ──────────────────
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout: int = 0
    max_tokens: int = 0
    temperature: float = 0.0

    # ── 各来源原始值（用于冲突检测和显示） ─────────
    file_key: str = ""       # credentials 文件中的 Key
    config_key: str = ""     # Config 文件中的 Key
    env_key: str = ""        # 环境变量中的 Key

    # ── 来源标记 ────────────────────────────────
    from_file: bool = False   # file_key 非空
    from_config: bool = False # config_key 非空
    from_env: bool = False    # env_key 非空

    # ── 最终来源（resolve 后确定） ───────────────
    active_source: str = "missing"  # "credentials_file" | "config_file" | "environment" | "cli" | "missing"

    # ── 冲突状态 ────────────────────────────────
    has_conflict: bool = False
    conflict_sources: list[str] = field(default_factory=list)  # 参与冲突的来源列表
```

### 5b. resolve() 新逻辑

```python
def resolve(self, name: str) -> CredentialBundle:
    bundle = CredentialBundle()

    # ── Step 1: 从 credentials 文件加载 ──────────
    # 设置 bundle.file_key, bundle.from_file

    # ── Step 2: 从 Config 文件加载 ──────────────
    # 设置 bundle.config_key, bundle.from_config

    # ── Step 3: 从环境变量加载 ──────────────────
    # 设置 bundle.env_key, bundle.from_env

    # ── Step 4: 按优先级合并 ────────────────────
    # 确定 active_source，设置 bundle.api_key
    # 检测冲突，设置 has_conflict / conflict_sources

    return bundle
```

### 5c. 合并逻辑伪代码

```python
# 按优先级从高到低扫描
for source, key in [("environment", bundle.env_key),
                    ("config_file", bundle.config_key),
                    ("credentials_file", bundle.file_key)]:
    if key:
        bundle.api_key = key
        bundle.active_source = source
        break

# 冲突检测
present = []
if bundle.file_key: present.append(("credentials_file", bundle.file_key))
if bundle.config_key: present.append(("config_file", bundle.config_key))
if bundle.env_key: present.append(("environment", bundle.env_key))

if len(present) > 1:
    unique_keys = set(hashlib.sha256(k.encode()).hexdigest() for _, k in present)
    if len(unique_keys) > 1:
        bundle.has_conflict = True
        bundle.conflict_sources = [s for s, _ in present]
```

---

## 6. 显示规范

### 6a. `zmai auth status`（新格式）

```
  Claude (Anthropic)
    Configured : Yes
    Source     : environment
    Warning    : credentials file contains a different key.

  DeepSeek
    Configured : Yes
    Source     : credentials file

  Gemini (Google)
    Configured : No
    Source     : missing
```

### 6b. `zmai doctor` 显示（新格式）

```
  DeepSeek              PASS  (source: environment)
  Claude (Anthropic)    PASS  (source: credentials file)
  Gemini (Google)       FAIL  (source: missing)

  ⚠ DeepSeek: credentials file and environment have different keys.
    Use `zmai auth status` for details.
```

### 6c. 启动 banner（`_print_auth_debug()` 新格式）

```
  Auth ──────────────────────────────────────────────
    Backend:         DeepSeek
    Active Source:   environment
    Credentials File: C:\Users\...\.zmai\credentials  ✅ Loaded
    Config File:     zmai.json                        ❌ No key
    Environment:     DEEPSEEK_API_KEY                 ✅ Loaded
    ⚠  credentials file contains a different key.
```

### 6d. 显示规则

| 场景 | 来源显示 | 额外信息 |
|------|---------|---------|
| 仅 file 有 Key | `credentials file` | — |
| 仅 config 有 Key | `config file` | — |
| 仅 env 有 Key | `environment` | — |
| file + env 同 Key | `environment` | — |
| file + env 不同 Key | `environment` | ⚠ 冲突警告 |
| file + config + env 均不同 | `environment` | ⚠ 多来源冲突警告 |
| 所有来源均无 | `missing` | Configured: No |

### 6e. 绝不输出真实 Key 的规则

```python
def _safe_preview(key: str) -> str:
    """截取前 7 字符，用于显示。"""
    return key[:7] + "..." if key else ""

def _safe_hash(key: str) -> str:
    """Key 的 SHA256 摘要前 8 位，用于冲突检测的显示。"""
    return hashlib.sha256(key.encode()).hexdigest()[:8]
```

---

## 7. 各模块修改指引

### 7a. `CredentialBundle` (`auth/bundle.py`)

**新增字段**:
- `file_key: str` — 来自 credentials 文件的原始 Key
- `config_key: str` — 来自 Config 文件的原始 Key
- `env_key: str` — 来自环境变量的原始 Key
- `from_config: bool` — 是否有 config 来源
- `active_source: str` — 最终生效来源
- `has_conflict: bool` — 是否存在冲突
- `conflict_sources: list[str]` — 冲突来源列表

**移除**（可选）:
- `from_file: bool` — 保留，但语义改为"file_key 非空"
- `from_env: bool` — 保留，但语义改为"env_key 非空"

### 7b. `CredentialResolver.resolve()` (`auth/resolver.py`)

**重写逻辑**:
```
Step 1: 从 credentials 文件加载 → 存入 bundle.file_key
Step 2: 从 Config 文件加载 → 存入 bundle.config_key
Step 3: 从环境变量加载 → 存入 bundle.env_key
Step 4: 按优先级合并 → 设置 bundle.api_key 和 bundle.active_source
Step 5: 冲突检测 → 设置 bundle.has_conflict 和 bundle.conflict_sources
```

### 7c. `Doctor._find_key()` (`cli/doctor.py`)

**统一为**:
```python
@staticmethod
def _find_key(name: str, env_key_name: str = "") -> tuple[str, str]:
    """返回 (key, source)，使用 CredentialResolver 统一解析。"""
    from zmai.auth.resolver import CredentialResolver
    bundle = CredentialResolver().resolve(name)
    return bundle.api_key, bundle.active_source
```

**调用方 `_check_backend()`**:
- 使用返回的 source 显示状态
- 检查 `bundle.has_conflict` 显示警告

### 7d. `DeepSeekBackend.__init__()` (`gateway/backends/deepseek.py`)

**统一为**:
```python
def __init__(self, config: dict | None = None) -> None:
    self._config = config or {}
    c = self._config
    self._api_key = c.get("api_key", "")
    # 不再自行从 os.environ 兜底
    # config["api_key"] 应由 PluginRegistry._build_config() 确保
```

**前提**: `_build_config()` 保证传入的 config 包含正确的 `api_key`。

### 7e. `PluginRegistry._build_config()` (`gateway/plugin.py`)

**保持**使用 `CredentialResolver(config).resolve(plugin.name)`。

**增强**: 在返回的 cfg 中加入 `active_source` 和 `has_conflict` 供日志使用。

### 7f. `_inject_auth_credentials()` → `_inject_to_env()` (`auth/resolver.py`)

**重写 `_inject_one()`**:
```python
def _inject_one(self, name: str) -> None:
    env_api_key, env_model = _resolve_env_names(name)
    bundle = self.resolve(name)

    if bundle.active_source == "environment":
        # env 已是最高来源，无需注入
        # 但如果 file 也有 Key 且不同 → 需要警告
        if bundle.has_conflict:
            logger.warning(
                "%s: credentials file has a different key "
                "than environment variable %s",
                name, env_api_key,
            )
        return

    if bundle.api_key:
        # 从 file/config 注入到环境变量
        os.environ[env_api_key] = bundle.api_key
        if bundle.model:
            os.environ[env_model] = bundle.model
```

### 7g. `_run_auth_status()` (`cli/main.py`)

**显示新格式**:
```python
print(f"    Configured : {'Yes' if bundle.active_source != 'missing' else 'No'}")
print(f"    Source     : {_source_label(bundle.active_source)}")

if bundle.has_conflict:
    print(f"    Warning    : {_format_conflict(bundle)}")
```

### 7h. `_print_auth_debug()` (`cli/main.py`)

**显示新格式**:
```python
print(f"    Active Source:   {bundle.active_source}")
print(f"    Credentials File: {'✅ Loaded' if bundle.from_file else '❌ No key'}")
print(f"    Config File:     {'✅ Loaded' if bundle.from_config else '❌ No key'}")
print(f"    Environment:     {'✅ Loaded' if bundle.from_env else '❌ No key'}")
if bundle.has_conflict:
    print(f"    ⚠  {_format_conflict(bundle)}")
```

### 7i. `_run_auth_doctor()` (`cli/main.py`)

**统一为**使用 `CredentialResolver`（当前已使用 `_find_auth_key()` 即 Resolver，无需修改方法本身，但显示需增强）。

---

## 8. 冲突检测与警告

### 8a. 冲突检测时机

| 时机 | 触发 | 输出 |
|------|------|------|
| 启动时 | `_print_auth_debug()` | stderr 灰色文本 |
| `zmai auth status` | 用户查询 | 终端输出 |
| `zmai doctor` | 用户查询 | 终端输出 |
| `inject_one()` | 后台注入 | `logger.warning` |

### 8b. 警告格式

```python
def _format_conflict(bundle: CredentialBundle) -> str:
    parts = []
    if bundle.file_key:
        parts.append(f"credentials file ({_safe_hash(bundle.file_key)})")
    if bundle.config_key:
        parts.append(f"config file ({_safe_hash(bundle.config_key)})")
    if bundle.env_key:
        parts.append(f"environment ({_safe_hash(bundle.env_key)})")

    active = bundle.active_source
    conflictors = [p for p in parts if active not in p]

    return (
        f"Multiple sources have different keys: "
        f"{', '.join(parts)}.\n"
        f"Currently using {active}. "
        f"To use the key from credentials file, "
        f"unset the environment variable {bundle._env_name}."
    )
```

**输出示例**:
```
    Warning: Multiple sources have different keys:
             credentials file (a1b2c3d4), environment (e5f6g7h8).
             Currently using environment.
             To use the key from credentials file,
             unset the environment variable DEEPSEEK_API_KEY.
```

### 8c. 冲突解决方案

| 用户意图 | 操作 |
|----------|------|
| 使用 env var | 无操作（env 优先级最高） |
| 使用 file key | `unset DEEPSEEK_API_KEY` 或移除 Shell 配置 |
| 同步两者 | `zmai auth update` 更新 file 使其与 env 一致，或更新 env 使其与 file 一致 |
| 禁用冲突警告 | 暂无禁用机制（设计上不提供"静默忽略不同 Key"） |

---

## 9. 迁移方案

### 9a. 过渡期（保持向后兼容）

| 阶段 | 行为 | 持续时间 |
|------|------|---------|
| 1. 新增字段 | `CredentialBundle` 添加新字段，旧字段保留 | 立即 |
| 2. 统一 Resolver | 所有路径切换到 `CredentialResolver.resolve()` | 一个版本 |
| 3. 弃用独立逻辑 | `Doctor._find_key()` 标记 deprecated | 一个版本 |
| 4. 删除独立逻辑 | 移除各 Backend 的 `os.environ` 直接读取 | 下一个版本 |

### 9b. 对用户的可见变化

| 当前 | 变化后 |
|------|--------|
| `zmai auth status` 显示 "Source: 环境变量" | 显示 "Source: environment" + 冲突警告 |
| 多 Key 静默共存 | 多 Key 时显示明确警告 |
| `zmai doctor` 不显示来源 | 显示 "PASS (source: ...)" |
| 启动 banner 不显示来源 | 显示 "Active Source: ..." |

### 9c. 风险

| 风险 | 可能性 | 缓解措施 |
|------|--------|---------|
| 老版本调用方不识别新字段 | 低 | 旧字段保留，`__init__` 提供默认值 |
| `has_conflict` 误报 | 低 | SHA256 比较确保可靠 |
| 用户依赖旧显示格式 | 低 | 变化在文档中说明 |

---

## 10. 测试场景

### 10a. 正常场景

| # | 场景 | 期望结果 |
|---|------|---------|
| 1 | 仅 credentials 文件有 Key | `active_source=credentials_file`, 无冲突 |
| 2 | 仅环境变量有 Key | `active_source=environment`, 无冲突 |
| 3 | 仅 Config 文件有 Key | `active_source=config_file`, 无冲突 |
| 4 | file + env 有相同 Key | `active_source=environment`, 无冲突 |
| 5 | 所有来源均无 Key | `active_source=missing` |

### 10b. 冲突场景

| # | 场景 | 期望结果 |
|---|------|---------|
| 6 | file=sk-A, env=sk-B | `active_source=environment`, `has_conflict=True` |
| 7 | file=sk-A, config=sk-B | `active_source=config_file`, `has_conflict=True` |
| 8 | file=sk-A, config=sk-B, env=sk-C | `active_source=environment`, `has_conflict=True`, 三源冲突 |
| 9 | file=sk-A, config=sk-A, env=sk-B | `active_source=environment`, `has_conflict=True`（file+config 一致 vs env） |

### 10c. 显示场景

| # | 场景 | 期望显示 |
|---|------|---------|
| 10 | `zmai auth status` 无冲突 | `Configured: Yes` / `Source: environment` |
| 11 | `zmai auth status` 有冲突 | + `Warning: ...` |
| 12 | `zmai doctor` 无冲突 | `PASS (source: environment)` |
| 13 | `zmai doctor` 有冲突 | `PASS (source: environment)` + `⚠ ...` |
| 14 | 启动 banner | Active Source + 各来源状态 + 冲突警告 |

### 10d. 集成场景

| # | 场景 | 期望结果 |
|---|------|---------|
| 15 | env var 已设，用户 `zmai auth update` 更新 file | 运行时仍用 env，启动 banner 提示冲突 |
| 16 | env var 已设，用户 unset 后重启 zmai | 自动 fallback 到 file key，无冲突 |
| 17 | 用户执行 `zmai auth status` 发现冲突 | 按提示调整后冲突消失 |
| 18 | 纯 Config 文件 Key（无 file 无 env） | 正常使用，来源显示 config_file |

---

## 附录 A：关键代码位置速查

| 组件 | 文件 | 行号 |
|------|------|------|
| `CredentialBundle` | `src/zmai/auth/bundle.py` | 全部（9 行） |
| `CredentialResolver.resolve()` | `src/zmai/auth/resolver.py` | 66-139 |
| `CredentialResolver._inject_one()` | `src/zmai/auth/resolver.py` | 167-176 |
| `Doctor._find_key()` | `src/zmai/cli/doctor.py` | 97-112 |
| `_find_auth_key()` | `src/zmai/cli/main.py` | 875-883 |
| `_run_auth_status()` | `src/zmai/cli/main.py` | 525-553 |
| `_print_auth_debug()` | `src/zmai/cli/main.py` | 828-872 |
| `_find_api_key()` | `src/zmai/runtime/preflight.py` | 204-211 |
| `PluginRegistry._build_config()` | `src/zmai/gateway/plugin.py` | 345-373 |
| `DeepSeekBackend.__init__()` | `src/zmai/gateway/backends/deepseek.py` | 40-54 |

## 附录 B：来源标签（国际化准备）

```python
SOURCE_LABELS = {
    "credentials_file": "credentials file",
    "config_file": "config file",
    "environment": "environment",
    "cli": "CLI argument",
    "missing": "missing",
}
```

---

*报告自动生成于 2026-07-18 · 基于源码审计 7 条凭据读取路径*
