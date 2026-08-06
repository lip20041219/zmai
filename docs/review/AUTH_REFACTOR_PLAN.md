# AUTH_REFACTOR_PLAN — 认证逻辑统一重构方案

> **目标：** First Run Wizard、`zmai auth update`、Runtime 三者使用同一套认证逻辑。
> **约束：** 禁止不同路径读写不同文件；禁止不同 Backend 使用不同读取逻辑。
> **本文仅提出方案，不涉及代码修改。**

---

## 一、现状梳理：三条路径的三套逻辑

### 1.1 First Run Wizard（`_first_run_wizard`）

**写操作（两份文件）：**
| 文件 | 写入内容 | 触发条件 |
|------|----------|----------|
| `~/.zmai/credentials` | `api_key` + `model/base_url/timeout/max_tokens/temperature` + `active_backend = name` | 总是 |
| `~/.zmai/config.json` | `gateway.default_backend` + `backends.<name>.model/base_url/timeout/max_tokens/temperature` | **仅首次** (`if not cfg_path.exists()`) |

**环境变量注入：**
```python
os.environ[env_key_name] = key          # 强制覆盖
os.environ.setdefault(env_model, model)  # 不覆盖已有
```

### 1.2 `zmai auth update`（`_run_auth` sub="update"）

**写操作（一份文件）：**
| 文件 | 写入内容 | 触发条件 |
|------|----------|----------|
| `~/.zmai/credentials` | `api_key` + `model/base_url/timeout/max_tokens/temperature` + `active_backend = name` | 总是 |

**不写 `~/.zmai/config.json`、不注入环境变量。**

### 1.3 Runtime（`main()` → `_inject_auth_credentials` → `PluginRegistry`）

**启动流程：**

```
main()
  ├─ _inject_auth_credentials()   ① 提前注入（仅内置 Backend，不包含 Plugin）
  ├─ [可能 Wizard]
  ├─ _inject_auth_credentials()   ② 再次注入（冗余）
  └─ Runtime(config)
       └─ PluginRegistry(config)
            ├─ _discover()        注册所有 Backend
            ├─ _auto_select_default()
            └─ _build_config()    按需装配配置
```

**默认 Backend 选择（`_auto_select_default`）：**

优先级：
1. `config.gateway.default_backend`（来自 `~/.zmai/config.json` 或 `zmai.json`）
2. 第一个有环境变量凭据的 Backend
3. 第一个已注册的 Backend

注：**不检查 `AuthStore.active_backend`**（即 `~/.zmai/credentials` 中的 `active_backend` 字段）。

**配置装配（`_build_config`）：**

声明优先级：`plugin defaults < credentials < config file < env vars < CLI`

实际实现（`plugin.py:345-393`）：
| 层 | 策略 | 效果 |
|----|------|------|
| Plugin 默认值 | 硬编码赋值 | 基础值 |
| AuthStore 读回 | `setdefault(k, v)` | 不覆盖默认值，**读回的可能是 env var 数据而非文件数据** |
| Config 文件 | `cfg.update(bc)` + 逐 key 赋值 | **覆盖所有之前的值** |
| 环境变量 | `setdefault(k, v)` | 不覆盖 Config 文件值 |

**实际优先级变成：** `plugin defaults < credentials ~ env vars (均 setdefault) < config file (override)`

与声明的 `config file < env vars` 顺序相反。

---

## 二、问题清单

### 🔴 P0 — 逻辑错误

| # | 问题 | 影响 |
|---|------|------|
| 1 | `_build_config` 中 Config 文件使用 `update`（强覆盖），Env 使用 `setdefault`（弱覆盖），导致 **Config 文件 > 环境变量**，与声明顺序和直观期望相反。 | 用户设置了 `DEEPSEEK_API_KEY` 但 Config 文件中有旧的 `api_key` → 旧值获胜。 |
| 2 | `_auto_select_default` **不查 `AuthStore.active_backend`**。`zmai auth update deepseek` 后 `~/.zmai/credentials` 中的 `active_backend` 已更新，但 Runtime 仍可能选择其它 Backend。 | 用户运行 `zmai auth update deepseek` 后立即 `zmai "task"`，但 Runtime 选了 claude 作为默认。 |
| 3 | `AuthStore.get_backend()` 在 **env var 已设置时返回 env var 数据**（含 `from_env=True`），丢失文件中的 `model/base_url/timeout` 等字段。`_build_config` 在第二层得到 env var 数据而非文件数据。 | `_build_config` 的"credentials 层"实际混入了 env var 值，与第四层产生优先级冲突。 |

### 🟠 P1 — 不一致

| # | 问题 | 影响 |
|---|------|------|
| 4 | Wizard 写两份文件（`credentials` + `config.json`），`auth update` 只写一份（`credentials`）。 | `zmai auth update deepseek ...` 后，`~/.zmai/config.json` 中的 `gateway.default_backend` 还是旧的。 |
| 5 | Wizard 写 `config.json` 时有 **`if not exists` 守卫**。第二次运行 Wizard 不会更新 `config.json`，`gateway.default_backend` 永远定格在首次选择。 | 用户再次运行 Wizard 切换 Backend 后，config.json 不会同步。 |
| 6 | `_inject_auth_credentials()` 在 `main()` 中被调用 **两次**（L768 + L806），第二次总是冗余。 | Wizard 路径下已由 Wizard 直接设 env var；非 Wizard 路径下 L768 已完成全部注入。 |
| 7 | `_inject_auth_credentials()` 只遍历内置 Backend（`BACKEND_METADATA`），**不覆盖 Plugin Backend**。 | 插件 Backend 需要靠 `_build_config` 中的 AuthStore 读取，没有提前注入。 |

### 🟡 P2 — 重复与散落

| # | 问题 | 影响 |
|---|------|------|
| 8 | **API Key 解析逻辑重复 5 处**：`_find_auth_key`(main.py)、`_find_api_key`(preflight.py)、`AuthStore.get_backend`(store.py)、`_inject_auth_credentials`(main.py)、`_build_config`(plugin.py)。 | 修一处漏三处，新增逻辑容易不一致。 |
| 9 | **Backend 元信息获取重复 3 式**：`get_backend_info()` (backends/__init__.py)、`PluginRegistry.get_plugin().env_api_key`、`info["env_api_key"]` 硬编码回退。 | 获取 env var 名称的方式不统一。 |
| 10 | **Preflight 独自维护 API Key 查找**（`_find_api_key`），与 `_inject_auth_credentials` 逻辑等价但不共享代码。 | 冷路径可能读到过时的 env var。 |

---

## 三、重构目标

```
┌─────────────────────────────────────────────────────────────┐
│                    CredentialResolver                        │
│  (统一凭证解析器, 所有路径共用)                                 │
│                                                             │
│  resolve(name) → unified CredentialBundle {                  │
│    api_key, model, base_url, timeout,                       │
│    max_tokens, temperature, source,                         │
│    from_env, from_file                                      │
│  }                                                          │
└──────────────────────────┬──────────────────────────────────┘
           │                         │
           ▼                         ▼
┌─────────────────────┐   ┌─────────────────────────┐
│  AuthStore           │   │  PluginRegistry / Runtime │
│  (仅文件 I/O,        │   │  （只消费 CredentialBundle, │
│   不查 env var)      │   │   不自己拼 env/file 逻辑）   │
└─────────────────────┘   └─────────────────────────┘
```

### 3.1 单一解析器

创建一个**无状态**的 `CredentialResolver`（或增强 AuthStore 方法），作为全系统读取 API Key 的唯一入口：

```python
class CredentialResolver:
    def resolve(self, name: str) -> CredentialBundle:
        """按统一优先级解析：file → config → env（声明抽象，不涉及实现）"""
        ...
```

**读取优先级（从低到高）：**
1. Plugin 默认值（hardcoded）
2. 凭据文件 `~/.zmai/credentials`（`AuthStore` 存储的持久化值）
3. Config 文件 `~/.zmai/config.json` / `zmai.json`
4. 环境变量（`DEEPSEEK_API_KEY` 等）
5. CLI 参数（`--api-key=...`）

**设计约束：**
- 所有路径调用同一个 `resolve(name)`。
- `resolve()` 返回统一结构体，不分别暴露"文件值"和"env var 值"。
- `resolve()` **绝不打印 Key**。
- Plugin Backend 和内置 Backend 走完全相同路径。

### 3.2 AuthStore 职责收窄

AuthStore 只做 **文件 I/O**，不再查询环境变量：

- `read(name) → dict | None` — 仅从加密文件读取
- `write(name, data)` — 写入加密文件
- `list() → list[str]` — 列出已存储的 Backend
- `get_active() → str` — 读取文件中的 `active_backend`
- `set_active(name)` — 设置文件中的 `active_backend`

删除当前 `AuthStore.get_backend()` 中的 env var 检查逻辑。

### 3.3 默认 Backend 单一来源

```
Active Backend 的权威来源：AuthStore.active_backend
                            ↑
                  所有写入路径都更新这里
                            ↓
PluginRegistry._auto_select_default()
  优先级改为：AuthStore.active_backend → config.gateway.default_backend
             → 第一个有 env var 的 Backend → 第一个已注册
```

**要求：**
- Wizard 和 `auth update` 都通过 `AuthStore.set_active()` 写入。
- `~/.zmai/config.json` 不再承载 `gateway.default_backend`（避免双来源）。
- 若用户愿意，Config 文件中的 `gateway.default_backend` 可作为覆盖项，但**只有一个写入路径**。

### 3.4 消除重复写入

Wizard 和 `auth update` 统一为：

```
AuthStore.write(name, api_key, model, ...)    # 写 credentials
AuthStore.set_active(name)                     # 设 active_backend
```

不再由 Wizard 额外写 `~/.zmai/config.json`。

### 3.5 `_inject_auth_credentials` 职责合并到 CredentialResolver

将散布的 env var 注入逻辑统一为：

```python
class CredentialResolver:
    def inject_to_env(self, name: str | None = None) -> None:
        """将凭据注入当前进程的 os.environ（可指定某 Backend 或全部）。"""
```

仅保留**一处**调用（`main()` 中的一次调用），删除重复调用。

---

## 四、重构步骤（建议执行顺序）

### Step 1：抽取 `CredentialBundle` 数据结构

- 定义命名字段：`api_key`(仅内部使用，绝不打印), `model`, `base_url`, `timeout`, `max_tokens`, `temperature`, `source`(enum: FILE|CONFIG|ENV|CLI), `exists: bool`
- 归入 `zmai/auth/bundle.py`（新文件）或内嵌在 `zmai/auth/resolver.py`

### Step 2：收窄 AuthStore

- 移除 `AuthStore.get_backend()` 中的 env var 检查 → 纯文件 I/O
- 新增 `AuthStore.read(name) → dict | None`
- 保留 `AuthStore.set_backend()`, `list_backends()`, `get_active_backend()`, `set_active_backend()` 等

### Step 3：实现 CredentialResolver

- 实现 `resolve(name) → CredentialBundle`
- 实现 `inject_to_env(name | None)`
- 按统一优先级读取
- 覆盖内置 Backend 和 Plugin Backend（通过 `PluginRegistry` 获取元信息）

### Step 4：替换调用点

| 调用点 | 替换为 |
|--------|--------|
| `_find_auth_key()` in `cli/main.py` | `CredentialResolver.resolve(name).api_key` |
| `_find_api_key()` in `preflight.py` | `CredentialResolver.resolve(name).api_key` |
| `_build_config()` in `plugin.py` 步骤 2 | `CredentialResolver.resolve(name).dict` |
| `_build_config()` in `plugin.py` 步骤 4 | 移除，合并到 resolver |
| `_inject_auth_credentials()` | `CredentialResolver().inject_to_env()` |

### Step 5：统一写入路径

- Wizard 移除 `~/.zmai/config.json` 写入 → 只写 `credentials`
- `auth update` 保持只写 `credentials`（不变）
- 统一调用 `AuthStore.set_active(name)`

### Step 6：修正 `_auto_select_default`

- 新增 `AuthStore.get_active_backend()` 最高优先级
- 移除 `_inject_auth_credentials()` 第二次调用

### Step 7：验证

| 场景 | 预期行为 |
|------|----------|
| Wizard 配置 DeepSeek → 立即执行任务 | Runtime 使用 DeepSeek Key |
| `zmai auth update claude` → 执行任务 | Runtime 使用 Claude |
| 环境变量 `DEEPSEEK_API_KEY` 已设 → 执行任务 | 环境变量优先 |
| Config 文件有 `api_key` → 执行任务 | Config 覆盖文件，环境变量覆盖 Config |
| 插件 Backend 配置 → 执行任务 | 与内置 Backend 完全相同 |

---

## 五、不修改的范围

| 项目 | 理由 |
|------|------|
| `zmai auth doctor` 显示逻辑 | 仅读不写，可用 resolver 替换底层但不影响输出格式 |
| `zmai auth list` 的 `key_preview` | 仅截取前缀展示，不暴露完整 Key |
| `_print_auth_debug` | 仅读不写，其诊断逻辑以 resolver 为准即可 |
| Config 文件的其它字段（`cli.theme`, `workspace.root` 等） | 与认证无关 |
| `~/.zmai/credentials` 加密格式 (XOR + base64) | 加密方案不在本次重构范围内 |

---

## 六、风险与注意事项

1. **向后兼容：** 用户现有的 `~/.zmai/config.json` 中若有 `gateway.default_backend`，在迁移后需要被 CredentialResolver 识别为旧格式，优先级高于文件但低于 env var。可以在过渡期保留读取但废弃写入。
2. **Plugin Backend 的 env_api_key 动态获取：** 需确保 `CredentialResolver` 能访问 `PluginRegistry` 或共享的元信息注册表。
3. **`_inject_to_env` 的时序：** 必须在 PluginRegistry 初始化之前调用，以便 `_auto_select_default` 能查到 env var。
