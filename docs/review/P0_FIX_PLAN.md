# P0 Fix Plan — 开源前阻塞性问题修复方案

> 生成日期：2026-07-22
> 状态：待审批

---

## P0-1: Config(sources=\[\]) Falsy Trap

### 根因

```python
# config.py:30
self._sources = sources or [
    FileSource("zmai.json"),
    FileSource(str(Path.home() / ".zmai" / "config.json")),
    EnvSource(),
    CLISource(),
]
```

`sources or [...]` 使用 `or` 运算符。当调用方传入 `sources=[]` 时，空列表 `[]` 在 Python 中是 falsy，`or` 会短路到默认列表。**调用方以为传了空列表就无源加载，实际加载了全部默认源。**

### 影响范围

- **17 处测试代码**使用了 `Config(sources=[]`，意图是"创建一个空配置"
- 其中 `test_config.py:201` 已有注释明确标记此 bug，并用 `cfg._data = {}` 后续清空作为 workaround
- 生产代码中 `Runtime.__init__` 使用 `config or Config()`，未暴露问题

### 现有行为

```python
Config(sources=[])  # 用户期望：空配置。实际：加载全部默认源
```

### 兼容性风险

**低。** 修正后 `sources=[]` 将按期望语义返回空配置。现有用 `sources=[]` 的测试依赖当前 bug 行为，需要同步更新。

### 推荐修复方案

将 `sources or [...]` 改为 `sources if sources is not None else [...]`：

```python
# 修复后
self._sources = sources if sources is not None else [
    FileSource("zmai.json"),
    FileSource(str(Path.home() / ".zmai" / "config.json")),
    EnvSource(),
    CLISource(),
]
```

### 需要同步修改的文件

| 文件 | 处数 | 原因 |
|------|------|------|
| `tests/test_runtime.py` | 13 | `Config(sources=[])` 意图创建空 cfg。修复后行为变为预期 |
| `tests/test_mocks.py` | 1 | 同上 |
| `tests/test_config.py` | 2 | `_empty_cfg()` 的 workaround 可以简化 |

### 测试计划

1. `test_config_empty_sources()` — `Config(sources=[])` → `_data` 为空
2. `test_config_default_sources()` — `Config()` → 加载默认源（当前行为不变）
3. `test_config_none_sources()` — `Config(sources=None)` → 加载默认源
4. 验证 `test_config.py:201` 的 `_empty_cfg()` workaround 可简化为直接使用 `Config(sources=[])`

---

## P0-2: Runtime._execute_task() 死代码

### 根因

`_execute_task()` 是 Runtime 的**旧版任务执行路径**，在 SWE Agent 架构引入后被 `Runtime.run()` 中的新路径（`SWEAgent.step()` → `AgentContext`）完全替代。新旧路径功能等价的，但新路径使用 `SWEAgent` 并支持 lifecycle/memory/persist。

### 影响范围

- **调用方：0 处**。`_execute_task` 在 `master` 分支上无处被调用
- `_make_tool_context`（被 `_execute_task` 调用，也被其他地方使用）

### 现有行为

死代码随 `Runtime` 实例加载而存在，77 行源码 + 5 处 import，不执行。

### 兼容性风险

**极低。** 私有方法（`_` 前缀），无外部调用。但需确认：
1. 子类是否覆写了 `_execute_task`（搜索：无，Runtime 无子类）
2. 是否在 pickle/serialization 上下文中引用（无）
3. `_make_tool_context` 同时被 `_execute_task` 和 `cancel` 路径使用 → 保留 `_make_tool_context`

### 推荐修复方案

1. 直接删除 `_execute_task()` 方法
2. 保留 `_make_tool_context()`（被 `_execute_task` 调用，但也在其他路径使用... 需确认）

实际上，`_make_tool_context` 在目前代码中只被 `_execute_task` 调用：

```python
ctx = self._make_tool_context(agent_id, workspace, config)
```

删除 `_execute_task` 后 `_make_tool_context` 也无调用方。但如果后续需要 tool context 创建逻辑，保留它无害。

### 测试计划

1. `test_runtime_execute_task_removed()` — 确认 `hasattr(Runtime, '_execute_task')` 为 False
2. 确保 `test_runtime.py` 中 `TestRuntimeRun` 全部通过（确认新路径正常工作）
3. 确认 `_make_tool_context` 的保留不产生 lint warning

---

## P0-3: 路径穿越检查可绕过

### 根因

```python
# workspace.py:862
if not str(target).startswith(str(agent_path)):
    raise WorkspaceError(...)
```

`str.startswith()` 是**字符串前缀匹配**，不是**路径边界匹配**。

当 `agent_path = /workspace/agent_1` 时，以下路径可绕过检查：

| target | startswith 结果 | 实际路径关系 |
|--------|----------------|-------------|
| `/workspace/agent_1/file.txt` | ✅ 通过 | 合法 |
| `/workspace/agent_1-secret/data` | ✅ **通过** | **穿越！** agent_1-secret 不是 agent_1 |
| `/workspace/agent_1/../../etc/passwd` | `resolve()` 后为 `/etc/passwd` | ❌ 被拦截（resolve 已展平 `..`） |

实际风险中等：`resolve()` 已展平 `..` 穿越。但 `agent_1-secret` 类攻击向量未被覆盖。

### 影响范围

- **所有 `Workspace.read/write/delete/exists` 方法**都调用 `_validate_path`
- `_agent_path()` 方法也做了简单的 agent_id 校验（阻止 `../` 字符），但同样基于字符串操作

### 现有行为

```python
# workspace.py:832-842 _agent_path
if ".." in agent_id or "/" in agent_id or "\\" in agent_id:
    raise WorkspaceError(...)
return self._root / agent_id
```

Agent ID 的 `..` `/` `\` 检查阻断了明显穿越。同时路径 `..` 段落被 `resolve()` 展平。因此：
- `agent_id = "../root"` → 被 `_agent_path` 拦截
- `path = "../../etc/passwd"` → 被 `resolve()` 展平后 `startswith` 拦截
- `agent_id = "agent_1"`, `path = "x/../../etc/passwd"` → `resolve()` → `/etc/passwd` → `startswith` 拦截
- **`agent_id = "agent_1"`, agent_1 的路径为 `/ws/agent_1`，`target = /ws/agent_1-extra/file` → startswith 放行**

最后的场景是唯一实际可被利用的向量：当 workspace 目录命名与其他目录相邻且前缀相同时。

### 兼容性风险

**低。** 用 `Path.relative_to()` 替换 `str.startswith()` 语义更严格，只有前缀相邻但实际不同目录的场景行为会变。

### 推荐修复方案

```python
# 修复前
if not str(target).startswith(str(agent_path)):

# 修复后
try:
    target.relative_to(agent_path)
except ValueError:
    raise WorkspaceError(f"路径穿越被拒绝: {path}", path=str(target))
```

`Path.relative_to()` 在 target 不在 agent_path 下时抛出 `ValueError`，且按路径组件比较而非字符串前缀。

### 同时需要修 `agent_path` 在 root 下的检查

```python
# 修复前（line 869）
if not str(agent_path).startswith(str(self._root)):

# 修复后
try:
    agent_path.relative_to(self._root)
except ValueError:
    raise WorkspaceError(...)
```

### 测试计划

1. `test_path_traversal_prefix_bypass()` — agent_path=`/ws/a`, target=`/ws/a-extra/file` → 拒绝
2. `test_path_traversal_double_dot()` — `..` 穿越 → 拒绝（当前已工作，确保回归）
3. `test_path_traversal_legitimate()` — 合法路径 → 通过
4. `test_path_traversal_agent_id_dots()` — agent_id 含 `..` → 拒绝（当前已工作）
5. `test_agent_path_root_traversal()` — agent_path 超出 root → 拒绝

---

## P0-4: XOR + Base64 被称为"加密"

### 根因

```python
# store.py:120-133
def _encrypt(plain: str, key: bytes) -> str:
    """XOR + base64 简单加密（对称）。"""
    data = plain.encode()
    encrypted = bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
    return base64.b64encode(encrypted).decode()

def _decrypt(cipher: str, key: bytes) -> str:
    """XOR + base64 解密。"""
    encrypted = base64.b64decode(cipher.encode())
    decrypted = bytes(encrypted[i] ^ key[i % len(key)] for i in range(len(encrypted)))
    return decrypted.decode()
```

XOR 流加密 + 固定 32 字节密钥 = **同态异或密码流**。具体弱点：
1. **密钥重用攻击**：同一密钥加密多个凭据 → XOR 两个密文抵消密钥 → 可推导原文
2. **已知明文攻击**：API Key 格式已知（`sk-` 开头）→ 可恢复密钥片段
3. **无认证加密**：无 MAC/签名，攻击者可篡改密文
4. **无 IV/Nonce**：同一密钥下的加密总是产生相同密文（确定性加密）

### 影响范围

- `AuthStore._save()` 使用 `_encrypt()` 写 `~/.zmai/credentials`
- `AuthStore._load()` 使用 `_try_decode()` 读 `~/.zmai/credentials`
- `rotate_key()` 重加密使用相同算法
- 约 7 个测试文件间接依赖此加密行为

### 现有行为

代码注释已存在矛盾：
- `__init__.py` docstring：`3. 凭据加密存储（XOR + base64）`
- `_encrypt()` docstring：`XOR + base64 简单加密`  
- `_resolve_key()` docstring：`获取稳定的加密密钥`

问题不在于实现有安全漏洞（XOR 对本地凭据文件提供的是 obfuscation-level 保护），而在于**术语误导**。开源后安全审查者会认为这是 amateur cryptography。

### 兼容性风险

**中。** 更换加密算法意味着：
1. 旧凭据文件无法由新代码解密 → 需要迁移路径
2. 现有的 `_try_decode()` 多密钥回退逻辑需要保留
3. `KEY_FILE` 中存储的密钥本身也需要更新格式

### 推荐修复方案

**方案 A（推荐 — 最小侵入）**：升级为 `cryptography.Fernet`（AES-128-CBC + HMAC-SHA256）

```python
from cryptography.fernet import Fernet

def _encrypt(plain: str, key: bytes) -> str:
    f = Fernet(base64.urlsafe_b64encode(key[:32]))
    return f.encrypt(plain.encode()).decode()

def _decrypt(cipher: str, key: bytes) -> str:
    f = Fernet(base64.urlsafe_b64encode(key[:32]))
    return f.decrypt(cipher.encode()).decode()
```

- 认证加密（AES-128-CBC + HMAC-SHA256）
- 内置时间戳防止重放
- 非确定性（每次加密结果不同）
- 标准库不可用，需添加 `cryptography` 依赖

**方案 B（零依赖）**：使用 Python 标准库 `hashlib.pbkdf2_hmac` + `AES` 的 `Crypto.Cipher` 不可用，所以标准库只有 `hashlib` 可用。

实际上 Python 3.13 没有内置 AES。标准方式是用 `hashlib.scrypt` 做 KDF + XOR 可达到 KDF 级别的安全性... 但最终还是 XOR。

**方案 C（推荐的零依赖替代）**：使用 `base64` + 带 salt 的 `hashlib.scrypt` 派生密钥，配合 `os.urandom` 做初始化向量，但底层仍是 XOR。

最现实的零依赖选择是加一个 HMAC 签名来防止篡改：

```python
def _encrypt(plain: str, key: bytes) -> str:
    import hmac
    iv = os.urandom(16)
    data = plain.encode()
    encrypted = bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
    # 加 HMAC 签名防止篡改
    sig = hmac.new(key, iv + encrypted, hashlib.sha256).hexdigest()[:16]
    return base64.b64encode(iv + sig.encode() + encrypted).decode()
```

但这仍然不是真正的加密。**推荐方案 A**。

### 键的变更

当前密钥处理：
```python
# store.py:47-50
key_bytes = base64.b64decode(raw)  # 32 bytes from file
return hashlib.sha256(key_bytes).digest()  # sha256 → 32 bytes for XOR
```

Fernet 需要 32 字节 URL-safe base64 key。刚好与现有 SHA-256 输出兼容。

### 迁移方案

1. 保留 `_legacy_machine_keys()` + `_try_decode()` 回退逻辑
2. 添加新 `_encrypt_v2()` / `_decrypt_v2()` 
3. 在 `_save()` 中用新加密写
4. 在 `_load()` 中先尝试新解密，回退旧解密
5. 成功旧解密后自动用新加密重写（如同当前 MachineGuid → KEY_FILE 迁移）
6. 版本标记写入凭据文件头：`{"version": 2, ...}`

### 测试计划

1. `test_encrypt_decrypt_roundtrip()` — 加密后解密得到原文
2. `test_encrypt_decrypt_v2_compat()` — v2 加密可被 v2 解密
3. `test_encrypt_migration_from_v1()` — v1 加密数据可被 v2 读取并升级
4. `test_encrypt_tamper_detection()` — 篡改密文后抛出 CredentialError
5. `test_encrypt_deterministic()` — 同一明文两次加密结果不同（非确定性验证）

---

## 可能影响的 API

| Issue | API | 影响类型 | 程度 |
|-------|-----|----------|------|
| P0-1 | `Config(sources=[])` | 行为变更 | 测试代码大量使用，生产代码不受影响 |
| P0-2 | `Runtime._execute_task` | 删除私有方法 | 无外部调用，不影响 |
| P0-3 | `Workspace._validate_path` | 行为收紧 | 仅影响路径前缀相邻的伪造 Agent ID 场景 |
| P0-4 | `AuthStore._encrypt/_decrypt` | 算法替换 | 兼容迁移层确保 V1 数据可读 |

## 向后兼容性风险

| Issue | 风险等级 | 说明 |
|-------|---------|------|
| P0-1 | 🟢 低 | 仅改变 `sources=[]` 的语义，从"加载默认"变为"加载空"。测试代码占所有使用方。 |
| P0-2 | 🟢 低 | 删除无调用的私有方法。0 个引用。 |
| P0-3 | 🟢 低 | 新检查比旧检查严格，不会误放行。极少数 edge case 行为会变。 |
| P0-4 | 🟡 中 | 需要保留旧解密路径。加密文件自动升级，但降级老版本会无法读取新格式。 |

## 数据迁移风险

| Issue | 风险等级 | 说明 |
|-------|---------|------|
| P0-1 | 🟢 无 | 纯逻辑变更，无数据 |
| P0-2 | 🟢 无 | 纯代码删除，无数据 |
| P0-3 | 🟢 无 | 纯逻辑变更，无数据 |
| P0-4 | 🟡 低 | 凭据文件自动重新加密，理论上数据无损。但降级有风险。 |

## 推荐修复顺序

```
第一批（独立、无风险、快速见效）:
  └─ P0-2: 删除 _execute_task 死代码
     理由: 纯删除，无副作用。验证只需 grep 确认无调用方。

第二批（简单逻辑修复，需调整测试）:
  └─ P0-1: 修正 Config falsy trap
     理由: 一行代码变更，但需更新 17 处测试代码。

第三批（安全加固，需测试边界）:
  └─ P0-3: 修复路径穿越检查
     理由: 实际利用难度高（需控制 agent_id），但修复简单且无风险。

第四批（核心加密升级，需要依赖管理）:
  └─ P0-4: 升级 XOR 到 Fernet
     理由: 涉及添加依赖、加密迁移层、全面测试，影响面最广。
```

---

## 总结

| P0 | 问题 | 修复难度 | 风险 | 代码行变更 | 测试变更 |
|----|------|---------|------|-----------|---------|
| 1 | Config falsy trap | ⭐ | 🟢 低 | 1 行 | ~17 处 |
| 2 | 死代码 | ⭐ | 🟢 低 | 删除 ~80 行 | 1 处新增 |
| 3 | 路径穿越 | ⭐ | 🟢 低 | 4 行 | 5 处新增 |
| 4 | XOR 伪加密 | ⭐⭐⭐ | 🟡 中 | ~30 行 + 依赖 | 5 处新增 |
