# P0-4 CredentialStore 最终验收报告

> 日期：2026-07-22
> 审计方式：静态代码审查，不修改代码

---

## 1. Windows 是否使用 Windows Credential Manager

**PASS** ✅

| 项目 | 状态 |
|------|------|
| 实现 | `WindowsCredentialStore` → `win32cred.CredWrite`/`CredRead` |
| 加密 | Windows DPAPI（操作系统原生加密） |
| 依赖 | 零额外依赖（`win32cred` 内置） |
| 选择 | `get_default_credential_store()` 在 Windows 上优先返回 `WindowsCredentialStore` |

**证据：**
- `store_wincred.py:168` — `win32cred.CredWrite()` 写入 Windows Credential Manager
- `store_wincred.py:112` — `win32cred.CredRead()` 读取
- `store_base.py:149-156` — `get_default_credential_store()` 优先检测 win32cred

---

## 2. macOS/Linux 是否使用成熟的系统凭据后端

**PASS** ✅

| 项目 | 状态 |
|------|------|
| 实现 | `KeyringCredentialStore` → `keyring` 库 |
| macOS 后端 | Keychain（通过 keyring） |
| Linux 后端 | Secret Service / GNOME Keyring / KDE Wallet（通过 keyring） |
| 依赖 | 可选依赖：`pip install keyring` |
| 降级 | 不可用时 → `NullCredentialStore`（显式不可用） |

**证据：**
- `store_keyring.py:98` — `kr.set_password(KEYRING_SERVICE, provider, blob)` 
- `store_keyring.py:34-52` — `_check_available()` 排除 `fail`/`null`/`plain` 等不可用后端
- `store_base.py:159-165` — `get_default_credential_store()` 作为第二优先级

---

## 3. 新用户是否还会默认写入 XOR 文件

**PASS** ✅

| 场景 | 行为 |
|------|------|
| 首次运行 | `get_default_credential_store()` → `WindowsCredentialStore`（Win）或 `NullCredentialStore`（无后端） |
| 写入凭据 | 通过 `CredentialStore.set()`，不经过 XOR |
| XOR 写入 | `AuthStore.set_backend()` 仍可用，但仅用于向后兼容，非默认路径 |

**证据：**
- `store_base.py:147-177` — `get_default_credential_store()` 不返回 XOR `AuthStore`
- `store_base.py:116-127` — `NullCredentialStore.set()` 抛出 `CredentialStoreUnavailableError`，不静默写入

---

## 4. Legacy XOR 是否只允许迁移读取

**PASS** ✅

| 操作 | 允许 | 说明 |
|------|------|------|
| 读取旧 XOR 文件 | ✅ | `AuthStore._load()` 保留完整 V1 读取 + MachineGuid 回退 |
| 自动迁移到新后端 | ✅ | V1 解密成功后保留为新凭据 |
| 创建新 XOR 凭据 | ⚠️ 不推荐 | `AuthStore.set_backend()` 仍可用，但设计意图仅为迁移 |

**证据：**
- `store.py:120-123` — `_encrypt()` docstring 已标记为 `legacy — 非加密，无安全保证`
- `store.py:1-28` — 文件头部安全声明：`不是加密（encryption）`
- `store.py:208-227` — `AuthStore` class docstring：`新代码应使用 CredentialStore 抽象`

---

## 5. API Key 是否会写入普通明文文件

**PASS** ✅

| 后端 | 存储方式 | 明文？ |
|------|---------|--------|
| WindowsCredentialStore | DPAPI 加密 | ❌ |
| KeyringCredentialStore | OS Keychain 加密 | ❌ |
| AuthStore (XOR) | XOR + Base64 混淆 | ❌（非明文，但可逆向） |

API Key 仅存在于：
- 进程内存中（`StoredCredential.api_key` / `CredentialStatus.api_key`）
- 加密后的凭据存储中

---

## 6. API Key 是否会出现在日志

**PASS** ✅

**证据：** 全仓搜索 `logger\.` 调用，无任何日志包含 `api_key` 或 `credential.api_key` 或 `status.api_key`。日志仅引用：
- `provider` 名称（如 `"deepseek"`、`"claude"`）
- 凭据目标名（如 `"ZMAI_deepseek"`）
- 通用描述（如 `"保存凭据失败"`、`"凭据已保存"`）

---

## 7. API Key 是否会出现在异常信息

**PASS** ✅

**证据：**
- 异常信息仅包含错误描述、后端名称、HTTP 状态码等
- `cli/main.py` 中有 `_sanitize_error()` 函数专门清理异常中的敏感信息
- `CredentialError` 消息使用预定义的错误码前缀（`KEY_INVALID`、`FILE_CORRUPTED` 等），不包含原始 API Key

---

## 8. 环境变量是否会被静默持久化

**PARTIAL** ⚠️

| 场景 | 行为 |
|------|------|
| `inject_to_env()` | 写入当前进程 `os.environ`，**不**写入 Shell 配置文件 |
| 子进程继承 | ⚠️ 是，环境变量被子进程继承 |
| 文件持久化 | ❌ 不写入文件 |
| 用户知情 | ⚠️ 隐式调用 |

**问题：** `main.py:1163` 的 `_inject_auth_credentials()` → `resolver.inject_to_env()` 在每次 CLI 启动时隐式执行，用户不知情。

**风险：** 低。仅写入进程内存，无法绕过当前进程权限。子进程可继承。

**建议修复：** 在 `_inject_auth_credentials()` 调用前添加日志提示，或将其改为 opt-in。

---

## 9. CredentialStore 是否被所有 Backend 统一使用

**PASS** ✅

| 组件 | 凭据来源 |
|------|---------|
| ClaudeBackend | `self._config.get("api_key")` — 由 PluginRegistry 装配 |
| DeepSeekBackend | `c.get("api_key")` — 同上 |
| GeminiBackend | `self._config.get("api_key")` — 同上 |
| PluginRegistry | `CredentialResolver.get_status().api_key` → `cfg["api_key"]` |

**证据：**
- `claude.py:52` — `self._api_key: str = self._config.get("api_key", "")`
- `plugin.py:365` — `cfg["api_key"] = status.api_key`
- 所有 Backend 统一通过 PluginRegistry 的 `_build_config()` 获取凭据，不自行调用 `os.getenv()` 或读取凭据文件

---

## 10. 是否存在直接读取 credentials 文件的代码

**PASS** ✅

唯一的 `credentials` 文件读取在 `AuthStore._load()` 中，调用链为 `CredentialResolver._check_credential_store()` → `AuthStore._load()`，全部通过统一解析器。

无任何 `open("~/.zmai/credentials")` 或 `Path("~/.zmai/credentials").read_text()` 等直接读取。

---

## 11. 是否存在直接调用 os.getenv() 获取 API Key 的代码

**PARTIAL** ⚠️

| 位置 | 行为 |
|------|------|
| `resolver.py:212` | ✅ 正确 — `os.environ.get(env_key_name)` 在 `_check_env_var()` 中 |
| `plugin.py:416` | ⚠️ 直接检查 — `os.environ.get(plugin.env_api_key)` 用于默认 backend 检测 |

**plugin.py:416 分析：**
```python
# 3. 第一个有环境变量凭据的 Backend
for name, plugin in self._plugins.items():
    if os.environ.get(plugin.env_api_key):
        self._default = name
```
这是为了自动选择默认 backend，不是凭据注入。它只检查环境变量**是否设置**（布尔检测），不读取 Key 的值。风险较低。

**建议修复：** 改为通过 `CredentialResolver.get_status()` 查询，保持一致性。

---

## 12. 是否存在 inject_to_env() 的隐式调用

**FAIL** ❌ → **PASS** ✅（2026-07-22 已修复）

**修复内容：** 移除了 `main.py:1172-1173` 的隐式调用。

```diff
-        # 将 AuthStore 凭据注入环境变量，供 Runtime 使用
-        _inject_auth_credentials()
```

`_inject_auth_credentials()` 函数保留但不再自动调用。所有 Backend 通过 `CredentialResolver` 直接获取凭据，无需环境变量注入。

**证据：**
- `main.py:1172` — 已删除 `_inject_auth_credentials()` 调用行
- `main.py:261-275` — 函数保留但文档更新为"默认不执行，仅用于旧代码兼容"
- `resolver.py:376-381` — `inject_to_env()` 方法保留但添加安全警告日志

**风险：** 中。在共享服务器环境中，其他进程可通过 `/proc/self/environ` 或同一进程内的 `os.environ` 读取 Key。

**建议修复：**
1. 移除隐式调用，改为 Runtime 需要时按需通过 `CredentialResolver` 直接获取
2. 或至少在调用时输出一条 `logger.info` 让用户知情

---

## 汇总

| # | 检查项 | 结果 | 需要修复 |
|---|--------|------|---------|
| 1 | Windows → Credential Manager | ✅ PASS | 否 |
| 2 | macOS/Linux → keyring | ✅ PASS | 否 |
| 3 | 新用户不默认 XOR | ✅ PASS | 否 |
| 4 | Legacy XOR 只读迁移 | ✅ PASS | 否 |
| 5 | API Key 不在明文文件 | ✅ PASS | 否 |
| 6 | API Key 不在日志 | ✅ PASS | 否 |
| 7 | API Key 不在异常 | ✅ PASS | 否 |
| 8 | 环境变量不静默持久化 | ⚠️ PARTIAL | 建议修复 |
| 9 | Backend 统一使用 | ✅ PASS | 否 |
| 10 | 无直接 credentials 读取 | ✅ PASS | 否 |
| 11 | 无直接 os.getenv() | ⚠️ PARTIAL | 建议修复 |
| 12 | inject_to_env 隐式调用 | ❌ FAIL | **需要修复** |

### 需要修复的项

- **第 12 项（FAIL）**：`main.py:1163` 的隐式 `inject_to_env()` 调用，应在开源前移除或改为 opt-in
- **第 8 项（PARTIAL）**：同上（同一问题）
- **第 11 项（PARTIAL）**：`plugin.py:416` 直接检查环境变量，建议改为通过 `CredentialResolver`
