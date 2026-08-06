# Auth Flow Review — DeepSeek API Key 保存后仍显示未配置

> 审查日期：2026-07-18
> 状态：实际验证完成
> 范围：First Run Wizard → credentials 写入 → Config Loader → Runtime → Gateway → DeepSeekBackend

---

## 零、摘要

当前系统**能正常工作** — 已验证 `zmai doctor` 和 `zmai auth status` 均显示 DeepSeek 已配置。

但认证链路中存在 **4 个静默失败点**，在特定条件下会导致"保存后仍显示未配置"。

---

## 一、认证全链路追踪

### 1a. 首次运行（Wizard）

```
用户运行 zmai
  → _should_show_wizard()
    → 检查 env var DEEPSEEK_API_KEY? 未设
    → 检查 AuthStore().list_backends()? 空
    → 返回 True（显示向导）

_first_run_wizard()
  → 用户选择 DeepSeek
  → 用户输入 API Key

  → AuthStore().set_backend("deepseek", key, ...)
    → AuthStore.__init__()
      → self._key = _machine_key()        ← 派生加密密钥
      → self._data = self._load()         ← 加载（或新建空数据）
    → 设置 backends.deepseek.api_key = key
    → self._save()
      → json.dumps(self._data)
      → _encrypt(plain, self._key)        ← XOR + base64
      → CREDENTIALS_FILE.write_text(...)  ← 写入 ~/.zmai/credentials

_inject_auth_credentials()
  → resolver.inject_to_env()
    → _inject_one("deepseek")
      → os.environ.get("DEEPSEEK_API_KEY")? 未设 → 继续
      → CredentialResolver().resolve("deepseek")
        → AuthStore._load() 解密 ~/.zmai/credentials
        → 返回 bundle with api_key = "sk-..."
      → os.environ["DEEPSEEK_API_KEY"] = "sk-..."  ← 注入进程环境变量
```

### 1b. 后续运行（同一终端 / 新终端）

```
main()
  ├── _should_show_wizard()
  │     → env var 检查（新终端无）
  │     → AuthStore().list_backends()
  │       → AuthStore.__init__()
  │         → self._key = _machine_key()
  │         → self._load() 解密 ~/.zmai/credentials
  │           → 成功 → list_backends() → ["deepseek"]
  │     → 返回 False（跳过向导）

  ├── _inject_auth_credentials()           ← 始终执行
  │     → CredentialResolver().resolve("deepseek")
  │       → AuthStore._load() 解密文件
  │       → 从文件读取 api_key
  │     → os.environ["DEEPSEEK_API_KEY"] = "sk-..."

  ├── Config() 构造
  │     → 低: zmai.json          (gateway.default_backend = "auto")
  │     → 高: ~/.zmai/config.json (gateway.default_backend = "deepseek")  ← 覆盖
  │     → env: ZMAI_* 前缀       (不含 DEEPSEEK_API_KEY)
  │     → CLI: --key=value

  └── Runtime(config) → PluginRegistry(config)
        → _auto_select_default()
          → 1. AuthStore().get_active_backend() = "deepseek" ✅
          → _default = "deepseek"

        → runtime.run(backend=None)
          → preflight.check(None, gateway, config)
            → resolved = gateway.default_name = "deepseek"
            → _find_api_key("deepseek", "DEEPSEEK_API_KEY")
              → CredentialResolver().resolve("deepseek").api_key
                → 文件 key / env var key → 返回 ✅
          → gateway.get("deepseek")
            → _build_config(plugin)
              → CredentialResolver(config).resolve("deepseek")
                → 返回 bundle with api_key
            → cfg["api_key"] = bundle.api_key
            → DeepSeekBackend(config=cfg)
              → self._api_key = cfg["api_key"] ✅
```

---

## 二、7 个具体问题答案

### Q1: API Key 实际写入到哪里？

**`~/.zmai/credentials`**（`C:\Users\MECHREVO\.zmai\credentials`，408 字节）

加密方案：XOR + base64

```
明文 JSON → json.dumps → UTF-8 bytes
  → XOR 加密（密钥 = SHA256(MachineGuid)）
  → base64 编码
  → 写入文件（UTF-8）
```

解密验证 —— **当前成功** ✅：
```
Key source: MachineGuid
Backend: deepseek, api_key length: 35
model: deepseek-chat, active_backend: deepseek
```

---

### Q2: Runtime 实际从哪里读取？

Runtime 不直接读取 credentials 文件。通过两条间接路径：

| 路径 | 调用方 | 用途 | 实现 |
|------|--------|------|------|
| `_find_api_key()` → `CredentialResolver().resolve("deepseek")` | `preflight.py:211` | Preflight 检查 | 无 Config → 仅 file + env |
| `_build_config()` → `CredentialResolver(config).resolve("deepseek")` | `plugin.py:363` | 构建 Backend 配置 | 有 Config → file + config + env |

`CredentialResolver.resolve()` 三源合并优先级：

| 优先级 | 来源 | 详情 |
|--------|------|------|
| 1（低） | AuthStore 文件 | 解密 `~/.zmai/credentials` |
| 2 | Config 文件 | `zmai.json` / `~/.zmai/config.json`（不含 api_key） |
| 3（高） | 环境变量 | `DEEPSEEK_API_KEY`（覆盖前两者） |

---

### Q3: 是否路径不一致？

**否。** 所有组件使用相同路径 `Path.home() / ".zmai" / "credentials"`。

| 组件 | 路径常量 | 实际值 |
|------|----------|--------|
| `AuthStore.CREDENTIALS_FILE` | `Path.home() / ".zmai" / "credentials"` | `C:\Users\MECHREVO\.zmai\credentials` |
| `AuthStore.AUTH_DIR` | `Path.home() / ".zmai"` | `C:\Users\MECHREVO\.zmai` |
| `FileSource(global_cfg)` | `Path.home() / ".zmai" / "config.json"` | `C:\Users\MECHREVO\.zmai\config.json` |
| 项目 zmai.json | `root / "zmai.json"` | `D:\desk\ZMAI\zmai.json` |

---

### Q4: 是否文件格式错误？

**当前文件格式正确，可解密。**

但加密方案缺乏完整性校验，存在 **静默损坏风险**：

```
AuthStore._load()
  │
  ├── _decrypt(cipher, self._key)
  │     ├── base64.b64decode() → 可能失败（文件损坏）
  │     ├── XOR 解密 → 可能产生非 UTF-8 字节
  │     └── .decode() → 可能失败
  │
  ├── 失败 → 明文降级（JSON 解析）
  │     └── 加密数据 = base64，不是合法 JSON → 必失败
  │
  └── 双重失败 → 静默返回空数据 ← 🔴
        └── 无日志、无告警
```

**加密密钥不一致**是最可能触发此路径的原因：

```
场景: 进程 A 用 MachineGuid 写入 → 加密
      进程 B 无法访问 MachineGuid → 用 fallback 密钥解密
      → 解密结果非 UTF-8 → .decode() 失败
      → 明文降级失败 → 返回空 backends
      → "未配置"
```

---

### Q5: 是否环境变量覆盖了配置文件？

**是的，且这是设计中环境变量的最高优先级。**

但在当前环境中存在**两类问题**：

**问题 5a：env var 与 credentials 文件 key 不同**
```
Credentials 文件中的 key: sk-da4fd23...（通过向导保存的）
环境变量中的 key:         sk-ca40c42d6de3...（来自 shell 配置）
```
`CredentialResolver.resolve()` 返回环境变量中的 key，而非文件中的 key。两者不同但都有效。

**问题 5b：`_inject_one()` 在 env var 已存在时跳过注入**
```python
def _inject_one(self, name):
    if os.environ.get(env_api_key):  # ← 环境变量已设
        return                        # ← 跳过，不更新

    bundle = self.resolve(name)
    if bundle.api_key and not bundle.from_env:
        os.environ[env_api_key] = bundle.api_key  # ← 不会执行
```

→ 用户通过 `zmai auth update` 更新了文件中的 key，但 env var 中的旧 key 继续生效 → "明明更新了，还是不行。"

---

### Q6: 是否 Config Loader 没有加载 credentials？

**这是设计行为，不是 bug。**

| 系统 | 职责 | 是否含 api_key |
|------|------|---------------|
| `Config`（FileSource/EnvSource） | 非敏感配置（model, base_url, timeout） | ❌ 不含 |
| `AuthStore` / `CredentialResolver` | 敏感凭据（api_key） | ✅ 管理 |

`CredentialResolver.resolve()` 在两种路径中都调用 AuthStore，独立于 Config。

Config 的 source 顺序：
```
zmai.json（低）→ ~/.zmai/config.json（高）→ env（更高）→ CLI（最高）
```

`~/.zmai/config.json` 正确覆盖 `zmai.json`。**Config 本身没有问题。**

---

### Q7: 是否 DeepSeekBackend 没有收到 API Key？

**当前能收到。**

验证记录：
```
PluginRegistry._build_config(deepseek):
  config has api_key: True ✅
  api_key length: 35
  model: deepseek-v4-flash
  base_url: https://api.deepseek.com/v1

Preflight check: api_key found ✅
AuthStore.get_backend: has api_key ✅
```

**但以下场景 DeepSeekBackend 收不到 Key：**

1. **credentials 文件解密失败** → `_build_config` 中 `bundle.exists` 为 False → 不设 `cfg["api_key"]` → DeepSeekBackend 收到空 config → `os.environ.get("DEEPSEEK_API_KEY")` 也未设 → 空字符串
2. **`_auto_select_default()` 选了 claude → fallback 到 deepseek** → 仍能收到 Key（`_build_config` 被调用），流程正确
3. **`zmai auth status` / `zmai auth doctor` 子命令** → 绕过 `_inject_auth_credentials()` → 但 `CredentialResolver` 直接检查文件 → 能找到 Key（前提是解密成功）

---

## 三、已发现的 4 个缺陷

### 🔴 缺陷 1：`_machine_key()` 调用不一致导致静默解密失败

**文件**: `src/zmai/auth/store.py:_machine_key()` (line 21), `_load()` (line 73)

**问题**: 每次 `AuthStore()` 被创建时都重新派生密钥，派生结果可能在进程间不一致。

```python
def _machine_key():
    if sys.platform == "win32":
        try:
            with winreg.OpenKey(HKLM, r"SOFTWARE\Microsoft\Cryptography") as k:
                guid = QueryValueEx(k, "MachineGuid")
                return sha256(guid.encode()).digest()  # ← 32 字节
        except:
            pass
    # Linux paths (不存在于 Windows)
    # ...
    return sha256(b"zmai-fallback-key").digest()  # ← 硬编码兜底
```

**触发条件**:
| 环境 | 期望行为 | 实际风险 |
|------|----------|---------|
| 正常 Windows | `MachineGuid` 稳定 | ✅ 始终一致 |
| 受限进程 | 权限不足 → fallback 密钥 | ❌ 与之前写入的 MachineGuid 密钥不一致 |
| WSL | `sys.platform` = "linux" → fallback 密钥 | ✅ 始终 fallback，一致 |
| Python 沙箱 | 无法读取注册表 → fallback 密钥 | ❌ 可能不一致 |

**影响**: 解密失败 → `_load()` 返回空数据（无告警）→ 所有后续读取认为"无凭据"。

---

### 🟠 缺陷 2：env var 与文件 key 不一致时无提示

**文件**: `src/zmai/auth/resolver.py:resolve()` (line 106), `_inject_one()` (line 160)

**问题**: 当环境变量中存在不同于文件的 key 时，系统完全静默使用 env var 的 key。

```python
# 用户通过向导更新了文件 key，但 shell 配置中仍有旧 env var
# 同一 shell 启动 zmai → 旧 env var 始终优先
```

**影响**: 用户运行 `zmai auth update` 看到 "saved"，但实际运行时使用的是 env var 中的旧 key。

---

### 🟡 缺陷 3：两种诊断工具的 key 检查逻辑不一致

**文件**:
- `src/zmai/cli/doctor.py:Doctor._find_key()` (line 91) — `zmai doctor`
- `src/zmai/cli/main.py:_find_auth_key()` (line 850) — `zmai auth doctor`

| 命令 | 检查顺序 | 路径 |
|------|---------|------|
| `zmai doctor` | env var → AuthStore | `Doctor._find_key()` |
| `zmai auth doctor` | CredentialResolver (file → config → env) | `_find_auth_key()` |
| `zmai auth status` | CredentialResolver (file → config → env) | `CredentialResolver` |
| Preflight | CredentialResolver (file → config → env) | `_find_api_key()` |

`zmai doctor` 单独实现了一套检查逻辑，可能与其他工具显示不一致。

---

### 🟡 缺陷 4：`zmai auth` 无参数时强制进入向导

**文件**: `src/zmai/cli/main.py:_run_auth()` (line 701)

```python
def _run_auth(argv):
    if not argv:
        _first_run_wizard()  # ← 无条件，即使已配置
        return
```

用户期望 `zmai auth` 显示帮助或状态，但却进入了配置向导。

---

## 四、验证记录汇总

```
凭证文件存在:          ✅ C:\Users\MECHREVO\.zmai\credentials (408 字节)
解密测试:              ✅ PASS (MachineGuid 密钥)
API Key 长度:          ✅ 35 字符
active_backend:        "deepseek"

CredentialResolver.resolve(deepseek):
  api_key exists:      ✅ True
  from_file:           ✅ True
  from_env:            ⚠️ True（当前 env var 也设了不同 key）

Preflight _find_api_key:  ✅ api_key found
PluginRegistry._build_config:  ✅ config has api_key

zmai doctor:           ✅ DeepSeek = PASS
zmai auth status:      ✅ DeepSeek = Configured (from file)
```

---

## 五、最可能根因

**核心问题**：`_machine_key()` 在不同进程/环境中的不一致性导致 `~/.zmai/credentials` 解密静默失败。

### 典型故障场景

```
1. 用户首次运行 zmai → 向导保存 key → 使用 MachineGuid 加密写入
2. 系统正常运行多次（解密成功 → MachineGuid 可访问）
3. 某次运行时：
   a. AuthStore._load() 调用 _decrypt()
   b. _machine_key() 因权限/环境问题返回 fallback 密钥
   c. 解密出非 UTF-8 字节 → UnicodeDecodeError
   d. 明文降级尝试 → 加密文件不是合法 JSON → JSONDecodeError
   e. _load() 返回 {"backends": {}}   ← 静默
   f. list_backends() → [] → _should_show_wizard() → True
   g. 用户看到向导 → "怎么又让我配置？"
4. 用户重新输入 key → 写入文件（用当前可用的 MachineGuid）
5. 一切恢复正常 → 用户困惑
```

### 次要因素

环境变量 `DEEPSEEK_API_KEY` 在 shell 配置中已设（`sk-ca40c42d...`），与文件中用户保存的 key（`sk-da4fd23...`）不同。当用户通过向导或 `zmai auth update` 更新文件 key 时，运行时使用的始终是环境变量中的 old key。

---

## 六、修复建议

| 优先级 | 缺陷 | 建议修复 |
|--------|------|---------|
| **P0** | 解密失败无日志 | `_load()` 的 `except Exception` 中加 `logger.warning()` |
| **P0** | `_machine_key()` 进程间不一致 | 缓存密钥到类变量：`_machine_key_cache: bytes` |
| **P1** | env var 覆盖文件 key 无提示 | `resolve()` 在 env var 与文件 key 不同时打 debug 日志 |
| **P1** | `_inject_one` 跳过已设 env var | `zmai auth update` 同时更新进程内 env var |
| **P2** | `zmai auth` 无参数强制向导 | 有凭据时显示状态，无凭据时进入向导 |
| **P2** | `zmai doctor`/`zmai auth doctor` 不一致 | 统一使用 `CredentialResolver` |

---

## 七、关键代码位置速查

| 组件 | 文件 | 关键行 |
|------|------|--------|
| Wizard 触发 | `src/zmai/cli/main.py` | `_should_show_wizard()` L127 |
| Wizard 写入 | `src/zmai/cli/main.py` | `_first_run_wizard()` L142 |
| 环境注入 | `src/zmai/cli/main.py` | `_inject_auth_credentials()` L223 |
| Config 构造 | `src/zmai/cli/main.py` | `Config(sources=[...])` L984 |
| 加密存储 | `src/zmai/auth/store.py` | `_encrypt()` L40 / `_decrypt()` L47 |
| 密钥派生 | `src/zmai/auth/store.py` | `_machine_key()` L21 |
| 文件加载 | `src/zmai/auth/store.py` | `_load()` L73 |
| 统一解析 | `src/zmai/auth/resolver.py` | `resolve()` L62 |
| 环境注入逻辑 | `src/zmai/auth/resolver.py` | `_inject_one()` L160 |
| Preflight | `src/zmai/runtime/preflight.py` | `check()` L65 |
| 默认选择 | `src/zmai/gateway/plugin.py` | `_auto_select_default()` L377 |
| 配置装配 | `src/zmai/gateway/plugin.py` | `_build_config()` L345 |
| DeepSeek 初始化 | `src/zmai/gateway/backends/deepseek.py` | `__init__()` L40 |
| Doctor 诊断 | `src/zmai/cli/doctor.py` | `_check_backend()` L67 |

---

*报告自动生成于 2026-07-18 · 实际代码验证 + 运行时测试*
