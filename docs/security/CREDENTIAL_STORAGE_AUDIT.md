# Credential Storage Security Audit

> 日期：2026-07-22
> 审计范围：`zmai.auth.store` + `zmai.auth.resolver` + `zmai.auth.status`

---

## 1. 当前安全模型

### 架构总览

```
Credentials sources (优先级低→高):
  ┌─────────────────────┐
  │  ~/.zmai/credentials │  XOR+Base64 文件 ← 本审计焦点
  │  ~/.zmai/config.json │  明文 JSON 文件
  │  zmai.json           │  明文 JSON 文件（项目）
  │  环境变量            │  进程内存
  └─────────────────────┘
        ↓
  CredentialResolver.get_status()
        ↓
  Backend config 装配
```

### 当前加密流程

```
Key file (~/.zmai/credentials.key):
  os.urandom(32) → Base64 编码 → 写入文件 (chmod 600)

↓ SHA-256

32 bytes 派生密钥

↓ XOR (重复密钥流)

API Key + JSON 结构 → Base64 → ~/.zmai/credentials
```

### 存储位置

| 文件 | 用途 | 权限 |
|------|------|------|
| `~/.zmai/credentials.key` | 32 字节随机密钥（Base64） | Unix: 600, Windows: ignored |
| `~/.zmai/credentials` | 加密后的凭据数据 | Unix: 600, Windows: ignored |
| `~/.zmai/config.json` | 明文配置（可选含 Key） | 默认 644 |
| `zmai.json` | 项目配置（可选含 Key） | 项目目录 |

### 密钥生命周期

- **生成**：首次 `AuthStore()` 实例化时
- **存储**：`~/.zmai/credentials.key`（Base64 编码）
- **派生**：`hashlib.sha256(key_bytes).digest()` → 32 字节 XOR 密钥
- **轮换**：`AuthStore.rotate_key()` — 删除旧 key 文件 → 重新生成 → 重加密
- **迁移兼容**：旧 `MachineGuid` / Linux `machine-id` / 硬编码 fallback

---

## 2. 当前漏洞

### V1: XOR 不是加密（P0-4）

```python
# store.py:120-124
def _encrypt(plain: str, key: bytes) -> str:
    encrypted = bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
    return base64.b64encode(encrypted).decode()
```

| 弱点 | 严重程度 | 说明 |
|------|----------|------|
| **密钥重用攻击** | 🟡 中 | 同一 32 字节密钥加密所有凭据。两个密文 XOR 抵消密钥 → 可推导原文 |
| **已知明文攻击** | 🟡 中 | API Key 以 `sk-`/`sk-ant-` 开头，已知前缀可恢复部分密钥流 |
| **无认证加密** | 🟠 高 | 无 MAC 签名，攻击者可篡改密文内容（翻转 bit 改变原文） |
| **确定性加密** | 🟢 低 | 无 Initialization Vector，同一明文每次加密结果相同 |
| **密钥文件泄露** | 🔴 严重 | `~/.zmai/credentials.key` + `~/.zmai/credentials` 两者同时泄露 → 凭据全量暴露 |

### V2: 文件权限在 Windows 上无效

```python
# store.py:66-69
try:
    KEY_FILE.chmod(0o600)  # Unix only
except Exception:
    pass  # Windows 静默忽略
```

Windows 上 `credentials.key` 和 `credentials` 文件的权限不受 Python 控制，默认所有用户可读。

### V3: 测试导入 broken

```python
# test_auth.py:12
from zmai.auth.store import AuthStore, _encrypt, _decrypt, _machine_key
```

`_machine_key` 函数在 2026-07 升级中已被移除（替换为 `_resolve_key` + `_legacy_machine_keys`），导致 `test_auth.py` 无法收集，整个测试文件被排除。

### V4: 注入环境变量泄露

```python
# resolver.py:366
os.environ[env_api_key] = status.api_key
```

`inject_to_env()` 将解密后的 API Key 写入进程环境变量。子进程可读取 `/proc/self/environ`（Unix）或通过 `os.environ`（Python 子进程）获取。

---

## 3. 推荐方案

### 设计目标

```
CredentialStore (抽象基类)
  │
  ├── WinCredStore          ← Windows Credential Manager (win32cred)
  ├── KeychainStore         ← macOS Keychain (security CLI / keyring)
  ├── SecretServiceStore    ← Linux libsecret (secretstorage / keyring)
  └── EncryptedFileStore    ← 跨平台 fallback (AES-GCM 加密文件)
```

### 方案 A（推荐）：OS 原生凭据存储 + 零外部依赖

利用 Python 标准库 + 平台自带 API，不增加外部依赖：

| 平台 | 机制 | 实现方式 |
|------|------|----------|
| Windows | `win32cred.CredWrite` / `CredRead` | 直接调用 Win32 API（`pywin32` 内置） |
| macOS | `security add-generic-password` / `find-generic-password` | CLI 子进程调用 |
| Linux | `secret-tool store` / `secret-tool lookup` | CLI 子进程调用（`libsecret-tools`）|

**优点**：零依赖，真·操作系统级加密（DPAPI / Keychain / Secret Service）
**缺点**：macOS/Linux CLI 调用有进程开销，错误处理复杂

### 方案 B（推荐）：keyring 库

```python
try:
    import keyring
except ImportError:
    # fallback to EncryptedFileStore
```

**优点**：统一 API，跨平台，社区成熟
**缺点**：需增加 `keyring` 依赖（pip install keyring），Linux 需安装 secretstorage

### 方案 C（保守）：修复当前文件加密

如无法使用 OS 凭据存储，至少将 XOR 升级为 AES-GCM：

```python
from cryptography.fernet import Fernet
```

**优点**：真正的认证加密，代码改动最小
**缺点**：需增加 `cryptography` 依赖，仍是文件级存储

### 推荐组合策略

```
运行时检测优先级（自动降级）:

1. keyring 库可用       → OS Keychain (macOS/Linux) / Credential Manager (Windows)
2. win32cred 可用       → Windows Credential Manager (不依赖 keyring)
3. cryptography 库可用  → AES-GCM 加密文件（带认证）
4. 以上皆不可用         → XOR + Base64 + 明确警告用户"不安全的存储"
```

---

## 4. 兼容性方案

### 向后兼容要求

1. 现有 `~/.zmai/credentials`（XOR+Base64）文件必须可读
2. 现有 `~/.zmai/credentials.key` 必须继续使用
3. `AuthStore` 公开 API 不变
4. `CredentialResolver.get_status()` 返回格式不变

### 读取策略（兼容层）

```
读取时（_load）：
  1. 先用 OS Keychain 读取（如果已迁移）
  2. 如果没有 Keychain 条目：
     a. 尝试 AES-GCM 解密 credentials 文件
     b. 回退 XOR+Base64 解密（V1 兼容）
     c. 解密成功 → 自动迁移到主存储（OS Keychain 或 AES-GCM）

写入时（_save）：
  根据当前主存储写入（OS Keychain > AES-GCM > XOR）
```

### 数据迁移

```
V1 (XOR+Base64)                V2 (AES-GCM)               V3 (OS Keychain)
      │                             │                            │
      │  _try_decode(V1_key)         │  _try_decode_v2()          │
      │  success → 自动升级           │  success → 读取            │
      └──────────→ V2 ─────────────→ │  fail → 回退 V2/V1         │
                                     └──────────────────────────→ │
```

---

## 5. 迁移方案

### 阶段一：修复基础（P0-4 最小修复）

目标：消除"XOR 是加密"的语义错误，添加明确安全警告

1. 更新所有 docstring：XOR → "obfuscation"
2. `_load()` 中添加安全警告日志：`logger.warning("凭据使用 XOR obfuscation，非安全加密")`
3. 修复 `test_auth.py` 的 `_machine_key` 导入错误
4. 确保 `test_auth.py` 可运行

**代码变更**：仅注释 + 日志 + 测试修复

### 阶段二：升级加密算法

目标：将 XOR 替换为认证加密

1. 检测 `cryptography` 库是否可用
2. 可用 → `Fernet` (AES-128-CBC + HMAC-SHA256)
3. 不可用 → 保留 XOR + 明确警告
4. 添加 `_encrypt_v2()` / `_decrypt_v2()` 函数
5. 修改 `_save()` 使用 V2 加密
6. 修改 `_load()` 先试 V2，回退 V1
7. V1 解密成功后自动升级到 V2

**代码变更**：加密核心替换 + 兼容层 + 测试

### 阶段三：OS Keychain 集成

目标：增加 Windows Credential Manager 作为首选存储

1. 实现 `WinCredStore` 类（使用 `win32cred`）
2. 在 `AuthStore.__init__` 中检测 win32cred
3. 可用 → 凭据优先存储到 Windows Credential Manager
4. 不可用 → 回退文件加密
5. 现有文件凭据自动迁移到 Credential Manager

**代码变更**：新增 CredentialStore 抽象 + WinCredStore 实现

### 阶段四：跨平台 Keyring

目标：macOS Keychain + Linux Secret Service

1. 检测 `keyring` 库
2. 检测 `secretstorage`（Linux）
3. 实现 `KeyringStore` 适配器
4. macOS 通过 `security` CLI 实现

**代码变更**：KeyringStore + CLI 后端

---

## 6. 降级策略

| 降级场景 | 行为 | 用户可见提示 |
|---------|------|-------------|
| `cryptography` 不可用 | 保留 XOR | `⚠ ZMAI_INSECURE_CREDENTIAL_STORE=1 警告：凭据存储未加密` |
| OS Keychain 连接失败（headless Linux） | 回退 AES-GCM 文件 | `⚠ Credential Store 不可用，使用加密文件` |
| credentials.key 损坏 | 明确报错，指引用户修复 | `✗ 凭据密钥文件损坏，请删除后重新配置` |
| 旧 V1 文件无法解密 | 回退 MachineGuid / fallback | `ℹ 凭据使用旧密钥解密成功，正在迁移到新密钥` |
| 所有密钥失败 | 报错 | `✗ 凭据解密失败，需重新配置` |

### 安全警告机制

```python
import os
import warnings

INSECURE_STORE = os.environ.get("ZMAI_INSECURE_CREDENTIAL_STORE") == "1"

if not HAS_SECURE_BACKEND and not INSECURE_STORE:
    warnings.warn(
        "ZMAI: 当前系统无可用的安全凭据存储。\n"
        "凭据将以 XOR+Base64 形式存储在磁盘上，这不是安全的加密。\n"
        "要消除此警告，请安装 cryptography：pip install cryptography\n"
        "或设置环境变量 ZMAI_INSECURE_CREDENTIAL_STORE=1\n",
        RuntimeWarning,
    )
```

---

## 7. 测试方案

### 单元测试

| 测试 | 验证内容 | 优先级 |
|------|---------|--------|
| `test_encrypt_decrypt_v2_roundtrip` | AES-GCM 加密→解密得到原文 | P0 |
| `test_encrypt_decrypt_v2_different_key_fails` | 错误密钥无法解密 | P0 |
| `test_encrypt_decrypt_v2_tamper_detected` | 篡改密文抛出错误 | P0 |
| `test_encrypt_decrypt_v2_non_deterministic` | 同一明文两次加密结果不同 | P1 |
| `test_store_migration_v1_to_v2` | V1 文件自动升级到 V2 | P1 |
| `test_wincred_store_write_read` | Windows Credential Manager 写入→读取 | P1 |
| `test_wincred_store_delete` | Windows Credential Manager 删除 | P1 |
| `test_store_fallback_on_keychain_fail` | Keychain 不可用时回退文件 | P2 |

### 集成测试

| 测试 | 验证内容 | 优先级 |
|------|---------|--------|
| `test_auth_store_full_cycle` | 写→读→改→删 完整周期 | P0 |
| `test_auth_store_multi_backend` | 多 Backend 凭据共存 | P0 |
| `test_credential_resolver_no_leak` | to_dict() 不输出完整 Key | P0 |
| `test_credential_resolver_priority` | env > config > store 优先级正确 | P0 |
| `test_credential_resolver_conflict` | 多来源不同 Key 冲突检测 | P1 |
| `test_inject_to_env_does_not_overwrite` | 注入不覆盖已有环境变量 | P1 |

### 安全测试

| 测试 | 验证内容 | 优先级 |
|------|---------|--------|
| `test_encrypt_decrypt_v2_no_known_plaintext` | 已知明文攻击下安全性 | P2 |
| `test_credential_file_permissions` | 凭据文件权限正确（Unix） | P2 |
| `test_inject_to_env_no_leak_to_subprocess` | 子进程无法获取父进程 Key | P2 |

---

## 附录 A：当前代码行数统计

| 文件 | 行数 | 用途 |
|------|------|------|
| `zmai/auth/store.py` | 417 | 加密/解密/存储 |
| `zmai/auth/resolver.py` | 369 | 多来源解析/冲突检测 |
| `zmai/auth/status.py` | 171 | 状态数据类型 |
| `zmai/auth/bundle.py` | 66 | Bundle 数据类型 |
| `zmai/auth/__init__.py` | 14 | 导出 |
| `tests/test_auth.py` | 229 | 测试（已 broken） |

## 附录 B：真实加密与 XOR 对比

| 特性 | XOR+Base64（当前） | AES-GCM（Fernet） | 重要性 |
|------|-------------------|-------------------|--------|
| 机密性 | ⚠️ 弱（可被已知明文攻击） | ✅ 强 | 🔴 |
| 完整性 | ❌ 无 | ✅ HMAC-SHA256 | 🔴 |
| 非确定性 | ❌ 无 IV | ✅ 随机 IV | 🟡 |
| 密钥派生 | SHA-256 | SHA-256 | 🟢 |
| 认证加密 | ❌ 无 | ✅ AEAD | 🔴 |
| 篡改检测 | ❌ 无 | ✅ 签名验证 | 🔴 |
| 算法公开 | ✅ XOR 简单 | ✅ 标准 Fernet | 🟢 |

## 附录 C：平台凭据存储可用性

| 平台 | 原生机制 | Python 访问方式 | 当前可用性 |
|------|---------|----------------|-----------|
| Windows | Credential Manager (DPAPI) | `win32cred` | ✅ 可用 |
| Windows | Credential Manager (DPAPI) | `keyring` | ❌ 未安装 |
| macOS | Keychain | `security` CLI | ❌ 未实现 |
| macOS | Keychain | `keyring` | ❌ 未安装 |
| Linux | GNOME Keyring / KDE Wallet | `secretstorage` | ❌ 未安装 |
| Linux | GNOME Keyring / KDE Wallet | `keyring` | ❌ 未安装 |
| Linux | `secret-tool` CLI | 子进程 | ❌ 未实现 |
| 跨平台 | 加密文件 (AES-GCM) | `cryptography` | ❌ 未安装 |
| 跨平台 | 加密文件 (XOR) | 标准库 | ✅ 当前使用 |
