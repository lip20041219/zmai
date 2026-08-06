# Credentials Encryption Review

> 审查日期：2026-07-18
> 范围：`src/zmai/auth/store.py` — 完整加密/解密流程 + 修复方案

---

## 目录

1. [当前加密流程](#1-当前加密流程)
2. [当前解密流程](#2-当前解密流程)
3. [密钥来源分析](#3-密钥来源分析)
4. [Fallback 触发条件](#4-fallback-触发条件)
5. [解密失败后果（调用方视角）](#5-解密失败后果调用方视角)
6. [设计目标](#6-设计目标)
7. [数据迁移方案](#7-数据迁移方案)
8. [最小修复方案](#8-最小修复方案)
9. [安全分析](#9-安全分析)

---

## 1. 当前加密流程

### 1a. 调用链

```
AuthStore.set_backend(name, api_key, ...)      store.py:136
  → self._save()                                 store.py:93
    → json.dumps(self._data, ensure_ascii=False)  # 序列化
    → _encrypt(plain, self._key)                  # 加密
      → plain.encode()                            # UTF-8 bytes
      → XOR(data[i] ^ key[i % len(key)])          # 逐字节 XOR
      → base64.b64encode(encrypted)               # 编码为 ASCII
    → CREDENTIALS_FILE.write_text(cipher)          # 写入文件
```

### 1b. 数据结构（加密前）

```json
{
  "version": 1,
  "active_backend": "deepseek",
  "backends": {
    "deepseek": {
      "api_key": "sk-...",
      "model": "deepseek-chat",
      "base_url": "https://api.deepseek.com/v1",
      "timeout": 120,
      "max_tokens": 4096,
      "temperature": 0.7,
      "verified_at": "",
      "created_at": "2026-07-18T00:00:00Z"
    }
  }
}
```

### 1c. 加密后外观

纯 base64 字符串（当前示例 408 字节）：

```
4m3wPZWCD8ekZdBYoyAIIj4H7Z+2vFp6Nni9U2zFrEm5beI9goEVza8syFSyLkphPA/8mKSqJyJ3YPRSZ8T+APwq7Xrd0R2KqzeDJ/lpUSJlRLuFq/RheWN9sgQxlrkWoC6yO4TEBMuoJdxApmkbOD1W+8P34Sc0dzm7WWbE4lGjb6Q8gpQW268igVXxZEl0fUi51KK4dn0IbqRaIJuuUfE78iiUy0mHqzeDVvZpTXAsAfyd7rpqdXht5xQugawH8CLjN5KFRJLqdthIviwKbT4cxoKvsmB2JDnsFjaRt0W1b6QsgpwWzbgmng3gaQo6f1S3wez5J24yab9Qa8TqLPg7pGLH00SE6mWJCvdtXGU7O/iC4uMlOmUr5AAvkbleqHjSadLLVJrwc98isHFVfQ==
```

### 1d. 加密参数

| 参数 | 值 |
|------|-----|
| 加密算法 | XOR（对称流密码） |
| 密钥长度 | 32 字节（SHA256 输出） |
| 编码 | base64 |
| 明文编码 | UTF-8 |
| 文件编码 | UTF-8 |
| 完整性校验 | 无 |
| 防篡改 | 无 |
| 非对称性 | 无（纯对称） |

---

## 2. 当前解密流程

### 2a. 代码路径

```python
# store.py:73-91
def _load(self) -> dict[str, Any]:
    # 步骤 0: 文件存在性检查
    if not CREDENTIALS_FILE.exists():
        return {"version": 1, "active_backend": "", "backends": {}}

    # 步骤 1: 主解密尝试
    try:
        cipher = CREDENTIALS_FILE.read_text(encoding="utf-8").strip()
        if not cipher:
            return {"version": 1, "active_backend": "", "backends": {}}

        plain = _decrypt(cipher, self._key)
        #          ↓
        #   base64.b64decode(cipher)       ← 可能失败：文件损坏
        #   XOR 解密                        ← 可能失败：密钥不匹配 → 非 UTF-8
        #   .decode()                       ← 可能失败：非 UTF-8 字节序列
        #          ↓
        return json.loads(plain)            ← 可能失败：解密后内容非 JSON

    # 步骤 2: 静默降级（所有异常 → 静默捕获）
    except Exception:
        # ↓ 尝试明文读取（历史兼容）
        try:
            data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            #   加密文件是 base64，不是合法 JSON → 此步几乎必失败
            self._data = data
            self._save()                   # 立即加密保存
            return data
        except Exception:
            # 终极兜底：返回空数据 🔴
            return {"version": 1, "active_backend": "", "backends": {}}
```

### 2b. 故障点矩阵

| 故障点 | 异常类型 | 降级结果 | 用户感知 |
|--------|---------|---------|---------|
| 文件不存在 | `FileNotFoundError`（在 if 检查前） | 返回空 dict | 进入向导 |
| 文件存在但为空 | `if not cipher` | 返回空 dict | 进入向导 |
| base64 解码失败 | `binascii.Error` | 明文降级 → 失败 → 空数据 | "未配置" |
| 密钥不匹配 → 非 UTF-8 | `UnicodeDecodeError` | 明文降级 → 失败 → 空数据 | "未配置" |
| 密钥不匹配 → UTF-8 但非 JSON | `json.JSONDecodeError` | 明文降级 → 失败 → 空数据 | "未配置" |
| 密钥不匹配 → 恰好是 JSON | 无异常（极低概率） | 返回**错误**数据 | 行为异常 |
| 文件权限错误 | `PermissionError` | 明文降级 → 失败 → 空数据 | "未配置" |
| 明文降级成功（旧版） | 无异常 | 重加密 + 返回数据 | ✅ 正常 |

---

## 3. 密钥来源分析

### 3a. `_machine_key()` 完整实现

```python
# store.py:21-37
def _machine_key() -> bytes:
    # ── Layer 1: Windows MachineGuid ──────────────────────
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography"
            ) as k:
                guid, _ = winreg.QueryValueEx(k, "MachineGuid")
                return hashlib.sha256(guid.encode()).digest()
        except Exception:          # ← 捕获所有异常，无日志
            pass

    # ── Layer 2: Linux machine-id ─────────────────────────
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            return hashlib.sha256(Path(p).read_bytes().strip()).digest()
        except Exception:          # ← 再次静默
            pass

    # ── Layer 3: 硬编码兜底 ──────────────────────────────
    return hashlib.sha256(b"zmai-fallback-key").digest()
```

### 3b. 三层密钥对比

| 层级 | 来源 | 稳定？ | Windows | Linux | macOS |
|------|------|--------|---------|-------|-------|
| Layer 1 | `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` | ✅ 稳定（除非重装 OS） | ✅ 可用 | ❌ N/A | ❌ N/A |
| Layer 2 | `/etc/machine-id` | ✅ 稳定 | ❌ 不存在 | ✅ 可用 | ❌ 不存在 |
| Layer 3 | 硬编码 `"zmai-fallback-key"` | ✅ 始终一致 | ✅ | ✅ | ✅ |

### 3c. 关键缺陷：层间跳转无状态缓存

`_machine_key()` 是**纯函数**，每次调用都重新走一遍 fallback 链：

```python
# 问题：同一台机器、同一进程内的两次调用可能返回不同结果
# 场景：某次调用中 MachineGuid 读取临时失败

AuthStore.__init__()
  → self._key = _machine_key()

  # 调用 #1: MachineGuid=XXXX → Key A   (写入)
  # 调用 #2: MachineGuid 读取失败 → Key B (读取，解密失败)
  # 调用 #3: MachineGuid=XXXX → Key A   (写入，重新配置)
```

**根本缺陷**：密钥不是稳定的——它取决于每次调用时 `MachineGuid` 的可访问性。

### 3d. 实际触发条件

| 场景 | 写入时密钥 | 读取时密钥 | 是否一致 |
|------|-----------|-----------|---------|
| 普通 Windows 桌面 | MachineGuid | MachineGuid | ✅ |
| 受限进程（如 Windows 服务、Scheduler） | 可能 fallback | 可能 MachineGuid | ❌ |
| Python 沙箱/Embedded Python | 可能 fallback | 可能 MachineGuid | ❌ |
| WSL1 | MachineGuid（Win 注册表可访问）| MachineGuid | ✅ 部分 |
| WSL2 | MachineGuid 不可访问 → fallback | fallback | ✅（都 fallback） |
| Docker 容器 | fallback | fallback | ✅（都 fallback） |
| 跨 Windows→WSL 使用 | MachineGuid | fallback | ❌ |
| 注册表权限被修改 | MachineGuid → fallback | fallback | ❌（同进程！） |

---

## 4. Fallback 触发条件

### 4a. `winreg.OpenKey` 失败触发场景

```
HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid
```

此键对以下环境**不可读**：

| 环境 | 原因 |
|------|------|
| 32-bit Python 在 64-bit 系统 | Registry Redirector 不影响此路径 |
| **Windows 容器** | 无注册表访问 |
| **WSL2** | sys.platform="linux"，不走 winreg |
| **受限用户令牌** | 某些企业策略限制 HKLM 读取 |
| **Proton/Wine** | 无 MachineGuid |
| **Numpy/Conda 沙箱** | 注册表访问可能被 hook |
| **Windows PE (WinPE)** | 注册表可能不存在 |
| **最小安装 Docker 镜像** | 基于 Windows Server Core / Nano |
| **System 账户 / LOCAL SERVICE** | 可能无注册表访问权限 |

### 4b. 严重性评估

**概率**：虽不常见，但在特定环境中是**必然发生**的。

**影响**：全部数据不可逆丢失（从用户角度）。

**检测**：目前零检测手段——解密失败静默返回空数据。

---

## 5. 解密失败后果（调用方视角）

### 5a. 调用链扩散

```
AuthStore._load() 返回空数据
  │
  ├── AuthStore.__init__() → self._data = {}
  │
  ├── AuthStore.list_backends() → []       → _should_show_wizard() → True → 强制向导
  │
  ├── AuthStore.get_backend("deepseek") → None
  │     ├── CredentialResolver.resolve("deepseek") → 无文件数据
  │     │     └── 检查 env var → 也无 → 返回空 Bundle
  │     │           ├── Preflight check → API_KEY_MISSING → 拒绝运行
  │     │           ├── PluginRegistry._build_config → 无 api_key → fallback
  │     │           ├── zmai auth status → "Configured: No"
  │     │           └── zmai doctor → "Missing Key"
  │     │
  │     └── AuthStore.get_active_backend() → ""
  │           └── PluginRegistry._auto_select_default()
  │                 ├── 拿不到 AuthStore 活跃 backend
  │                 ├── config.default_backend → "deepseek"（如果 config 有效）
  │                 └── → 选了 deepseek → Preflight 发现无 Key → fallback 到 claude（如果 claude 有 Key）
  │
  └── AuthStore.set_backend(name, key, ...)  → 写入空数据
        └── 向导中用户重新配置 → 覆盖原加密文件
              └── 用当前 _machine_key() 加密 → 可能又是新密钥
```

### 5b. 当前用户可见症状

```
$ zmai doctor
  DeepSeek  Missing Key     ← 文件解密失败

$ zmai auth status
  DeepSeek  Configured : No  ← 同

$ zmai "task"
  Preflight Check 失败
  未检测到 DEEPSEEK_API_KEY。

$ zmai
  → 首次配置向导自动弹出       ← _should_show_wizard() 返回 True
```

### 5c. 数据丢失风险

用户看到向导 → 重新输入 key → `_save()` 用**当前** `_machine_key()` 加密写入

→ 原文件被覆盖 → 即使 MachineGuid 恢复可读，原数据也丢失了。

---

## 6. 设计目标

### 6a. 约束条件

| # | 约束 | 说明 |
|---|------|------|
| 1 | 免外部依赖 | 不使用 `cryptography`、`pycryptodome` 等第三方库 |
| 2 | 跨平台 | 同时支持 Windows / Linux / macOS |
| 3 | 前向兼容 | 现有 `~/.zmai/credentials` 文件必须可读 |
| 4 | 自动迁移 | 升级后首次读取时自动迁移到新方案 |
| 5 | 不可逆降级 | 不允许从稳定密钥自动切到不稳定密钥 |

### 6b. 安全目标

| # | 目标 | 说明 |
|---|------|------|
| 1 | 防意外泄露 | 仅该用户可读（文件权限 600） |
| 2 | 机器绑定 | 数据不能在其他机器解密 |
| 3 | 无明文持久化 | 磁盘上始终加密存储 |
| 4 | 密钥稳定 | 同一台机器上多次调用返回相同密钥 |

### 6c. 不在此范围内的安全目标

- 防有权限的攻击者（如果攻击者能读 `~/.zmai/`，也能读 key 文件）
- 防内存 dump（Key 仍在进程内存中）
- 防逆向工程（XOR 加密并非强加密）

---

## 7. 数据迁移方案

### 7a. 新旧密钥共存策略

```
迁移前状态:
  ~/.zmai/credentials          ← 用旧 Key 加密
  （无密钥文件）

迁移后状态:
  ~/.zmai/credentials          ← 用新 Key 加密
  ~/.zmai/credentials.key      ← 新稳定密钥（首次生成后不变）
```

### 7b. 迁移过程（逐次尝试）

```
_load() 流程（迁移版）:
  │
  ├── 1. 新密钥文件存在？
  │     ├── 是 → 读取 stable_key → 尝试解密
  │     │         ├── 成功 → 返回数据（正常路径）
  │     │         └── 失败 → 继续步骤 2
  │     └── 否 → 继续步骤 2（新系统首次运行）
  │
  ├── 2. 旧 MachineGuid 密钥存在且有效？
  │     ├── 是 → 尝试解密
  │     │         ├── 成功 → 写入密钥文件（首次创建）
  │     │         │        → 用新 key 重加密保存
  │     │         │        → 返回数据
  │     │         └── 失败 → 继续步骤 3
  │     └── 否 → 继续步骤 3
  │
  ├── 3. 旧 fallback 密钥（硬编码）尝试？
  │     ├── 是 → 尝试解密
  │     │         ├── 成功 → 写入密钥文件 + 重加密保存
  │     │         │        → 返回数据
  │     │         └── 失败 → 继续步骤 4
  │     └── 否 → 继续步骤 4（旧 fallback 一直可用）
  │
  ├── 4. 所有密钥尝试失败
  │     └── → 返回结构化错误（区分损坏/不匹配/格式错误）
  │
  └── 5. 首次运行（文件不存在）
        └── → 返回空 dict + 生成密钥文件（懒创建）
```

### 7c. 迁移时序

```
版本升级前:
  credentials   ← 用 MachineGuid 或 fallback 加密

首次运行新版本:
  1. 尝试 stable_key 解密 → 失败（key 文件不存在）
  2. 尝试 MachineGuid 解密 → 成功（当前环境可读 MachineGuid）
  3. 生成 credentials.key ← 写入稳定密钥
  4. 用 stable_key 重加密 credentials ← 覆盖原文件
  5. 提示: "凭据已迁移到新的加密系统"

后续运行（同一台机器）:
  1. 读取 credentials.key → 得到 stable_key
  2. 用 stable_key 解密 → 成功 ✅
  3. 不再依赖 MachineGuid
```

### 7d. 迁移回滚

如果需要回滚到旧版本：
- 删除 `~/.zmai/credentials.key`
- 旧版本继续使用 `_machine_key()` 解密（回退到 MachineGuid 或 fallback）
- 数据完整性不受影响

---

## 8. 最小修复方案

### 8a. 新密钥策略：文件式稳定密钥

```
~/.zmai/credentials.key  ← 32 字节随机密钥（base64 编码）
```

**生成时机**：第一次需要加密或解密时

**生成方式**：
```python
KEY_FILE = AUTH_DIR / "credentials.key"

def _load_or_create_key() -> bytes:
    """读取或生成稳定密钥。"""
    if KEY_FILE.exists():
        raw = KEY_FILE.read_text(encoding="utf-8").strip()
        return hashlib.sha256(base64.b64decode(raw)).digest()
    # 首次运行：生成 32 字节随机密钥
    new_key = os.urandom(32)
    KEY_FILE.write_text(base64.b64encode(new_key).decode(), encoding="utf-8")
    try:
        KEY_FILE.chmod(0o600)
    except Exception:
        pass
    return hashlib.sha256(new_key).digest()
```

**稳定性保证**：文件不删除 → 密钥不变。与 MachineGuid / 注册表 / machine-id 完全解耦。

### 8b. `AuthStore` 修改方案

```python
class AuthStore:
    def __init__(self) -> None:
        AUTH_DIR.mkdir(parents=True, exist_ok=True)
        self._key: bytes = self._resolve_key()      # ← 新方法
        self._data: dict[str, Any] = self._load()

    @staticmethod
    def _resolve_key() -> bytes:
        """密钥策略（2026-07 升级）：
        1. 优先使用 credentials.key 稳定密钥文件
        2. 不存在则尝试旧 MachineGuid/fallback（迁移兼容）
        3. 首次运行则创建新密钥文件
        """
        key_file = AUTH_DIR / "credentials.key"
        
        # ── 1. 稳定密钥文件已存在 → 直接使用 ──
        if key_file.exists():
            try:
                raw = key_file.read_text(encoding="utf-8").strip()
                return hashlib.sha256(base64.b64decode(raw)).digest()
            except Exception as e:
                # 密钥文件损坏 → 严重错误，不静默降级
                raise RuntimeError(
                    f"凭据密钥文件损坏: {e}\n"
                    f"  请删除 {key_file} 后重试。"
                )
        
        # ── 2. 首次运行 / 迁移场景 ─────────────
        # 生成新密钥
        new_key = os.urandom(32)
        key_file.write_text(base64.b64encode(new_key).decode(), encoding="utf-8")
        try:
            key_file.chmod(0o600)
        except Exception:
            pass
        return hashlib.sha256(new_key).digest()
```

### 8c. `_load()` 错误处理改造

```python
def _load(self) -> dict[str, Any]:
    """加载并解密凭据文件。失败时抛出明确异常。"""
    
    # ── 文件不存在：首次运行 ──────────────────
    if not CREDENTIALS_FILE.exists():
        return {"version": 1, "active_backend": "", "backends": {}}
    
    cipher = CREDENTIALS_FILE.read_text(encoding="utf-8").strip()
    if not cipher:
        # 文件存在但为空 → 损坏
        raise CredentialError(
            "凭据文件为空，可能已损坏。\n"
            "  请删除 ~/.zmai/credentials 后重新配置。"
        )
    
    # ── 用当前密钥尝试解密 ────────────────────
    plain = self._try_decode(cipher, self._key)
    if plain is not None:
        return plain
    
    # ── 迁移兼容：用旧密钥尝试 ────────────────
    # ...（见迁移方案章节）
    
    # ── 所有尝试失败 → 明确错误 ────────────────
    raise CredentialError(
        "凭据文件解密失败：密钥不匹配或文件损坏。\n"
        "  加密密钥与此机器不匹配。\n"
        "  请运行 zmai auth update <backend> 重新配置。"
    )

def _try_decode(self, cipher: str, key: bytes) -> dict | None:
    """尝试用指定密钥解密，返回 None 而非异常。"""
    try:
        encrypted = base64.b64decode(cipher.encode())
    except Exception:
        return None  # base64 损坏
    
    try:
        decrypted = bytes(
            encrypted[i] ^ key[i % len(key)]
            for i in range(len(encrypted))
        )
        plain = decrypted.decode("utf-8")
    except UnicodeDecodeError:
        return None  # 密钥不匹配
    
    try:
        return json.loads(plain)
    except json.JSONDecodeError:
        return None  # 解密后格式错误
```

### 8d. 错误类型区分

| 条件 | 错误类型 | 用户消息 |
|------|---------|---------|
| 文件不存在 | 无（空 dict） | （首次运行，进入向导） |
| 文件存在，为空 | `CredentialError` | "凭据文件为空，可能已损坏" |
| base64 解码失败 | `CredentialError` | "凭据文件损坏：base64 解码失败" |
| 解密后非 UTF-8 | `CredentialError` | "凭据解密失败：加密密钥不匹配" |
| 解密后非 JSON | `CredentialError` | "凭据文件格式错误" |
| 旧密钥也无法解密 | `CredentialError` | "凭据解密失败：密钥不匹配或文件损坏" |
| 密钥文件损坏 | `RuntimeError` | "凭据密钥文件损坏" |

### 8e. 新增异常类

```python
# 在 errors/__init__.py 或 auth/store.py 中


class CredentialError(Exception):
    """凭据操作失败（用户可见错误消息）。"""
    pass
```

### 8f. 调用方处理

所有 `AuthStore()` 的调用方需要处理 `CredentialError`：

| 调用方 | 文件 | 当前处理 | 修改 |
|--------|------|---------|------|
| `_should_show_wizard()` | `cli/main.py:127` | `except Exception: pass` | 明确捕获 `CredentialError` 并输出 |
| `_first_run_wizard()` | `cli/main.py:142` | 创建新 AuthStore | 加 try/except 输出错误 |
| `CredentialResolver.resolve()` | `auth/resolver.py:62` | `except Exception: pass` | 捕获 `CredentialError` 并传递 |
| `Doctor._find_key()` | `cli/doctor.py:91` | `except Exception: pass` | 区分"文件不存在"和"损坏" |
| `_auto_select_default()` | `gateway/plugin.py:377` | `except Exception: pass` | 同 |

---

## 9. 安全分析

### 9a. XOR 加密强度说明

XOR + base64 **不是强加密**。已知攻击：

| 攻击 | 可行性 | 说明 |
|------|--------|------|
| Known-plaintext | 容易 | 已知 JSON 结构 → 可恢复密钥 |
| 频率分析 | 可行 | JSON 格式固定，字节频率可预测 |
| 如果 key 文件泄露 | 完全破解 | key 文件是唯一保护 |

**当前 XOR 方案的安全性等价于：**
- 密钥文件不泄露 → 一定保护
- 密钥文件泄露 → 无保护

这与 `_machine_key()` 方案的安全级别相同（MachineGuid 并非秘密）。

### 9b. 修复方案安全对比

| 指标 | 当前（MachineGuid） | 修复后（文件密钥） |
|------|-------------------|-------------------|
| 防意外泄露 | ✅ 文件权限 600 | ✅ 文件权限 600 |
| 机器绑定 | ✅ MachineGuid 唯一 | ⚠️ 文件可复制（但需文件权限） |
| 稳定性 | ❌ 跨调用不一致 | ✅ 文件不变即稳定 |
| 外部依赖 | ❌ 依赖注册表 | ❌ 无依赖 |
| 密钥轮换 | ❌ 不支持 | ✅ 删除 key 文件即可轮换 |

实际安全性**不变**：文件密钥与 MachineGuid 都只能防止**非该用户的意外访问**。有权限的攻击者都能读取两者。

### 9c. 加密方案升级路径

如果未来需要更强的加密，最小改动路径：
1. 将 `_encrypt`/`_decrypt` 的算法参数化为策略模式
2. 在 credentials 文件中加入 `encryption_scheme` 字段
3. 新增 AES-GCM 方案作为 v2
4. 读取时按 scheme 字段选择解密器

当前改为文件密钥后，密钥派生与加密算法解耦，升级路径更清晰。

---

## 附录 A：与替代方案的对比

| 方案 | 优点 | 缺点 | 推荐？ |
|------|------|------|--------|
| **文件密钥（本方案）** | 最简单、跨平台、稳定 | 密钥文件可被复制 | ✅ **推荐** |
| Windows DPAPI (CryptProtectData) | OS 级保护，自动绑定用户+机器 | Windows 独占，需 ctypes | ❌ 不跨平台 |
| macOS Keychain | OS 级保护 | macOS 独占 | ❌ 不跨平台 |
| libsecret (Linux) | OS 级保护 | Linux 独占，需 DBus | ❌ 不跨平台 |
| 无加密（纯文件权限） | 最简单 | 明文存储 | ❌ 安全不达标 |
| 口令派生密钥 | 无存储密钥文件 | 需用户记忆口令 | ❌ UX 太差 |
| TPM/HSM | 硬件级保护 | 不可移植 | ❌ 过度设计 |

## 附录 B：文件布局（修复后）

```
~/.zmai/
  ├── credentials              ← 加密凭据（XOR + base64，用稳定密钥）
  ├── credentials.key          ← 稳定密钥文件（32 字节随机，base64）
  ├── config.json              ← 非敏感配置
  ├── sessions/                ← 会话记录
  ├── backends/                ← 第三方 Backend 插件
  └── memory/                  ← 长期记忆
```

`credentials.key` 生成一次后不再变更。如果删除，下次运行时会生成新密钥，但旧 credentials 将无法解密（需重新配置）。

## 附录 C：测试场景清单

| # | 场景 | 期望行为 |
|---|------|---------|
| 1 | 全新安装，首次运行 | 生成 credentials.key，正常使用 |
| 2 | 已有 MachineGuid 加密文件 | 自动迁移到文件密钥，重加密保存 |
| 3 | 已有 fallback 加密文件 | 自动迁移到文件密钥，重加密保存 |
| 4 | 已有文件密钥加密文件 | 直接使用，无需迁移 |
| 5 | 文件密钥被删除 | 视为新密钥，旧凭据无法解密 → 报错 |
| 6 | credentials 文件损坏 | 明确报错 "凭据文件损坏" |
| 7 | credentials.key 文件损坏 | 明确报错 "凭据密钥文件损坏" |
| 8 | 跨机器复制 ~/.zmai/ | 解密失败 → 密钥不匹配错误 |
| 9 | 回滚到旧版本 | 删除 credentials.key，旧版本可读 |
| 10 | 同时有多个 backend | 全部迁移 |

---

*报告自动生成于 2026-07-18 · 基于源码 `src/zmai/auth/store.py` 分析*
