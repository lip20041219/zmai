# Credential Resolver Design

> API 定义、数据流、模块接口
> 日期：2026-07-18

---

## 目录

1. [CredentialResolver API](#1-credentialresolver-api)
2. [CredentialStatus 结构](#2-credentialstatus-结构)
3. [优先级策略](#3-优先级策略)
4. [冲突检测](#4-冲突检测)
5. [模块接口](#5-模块接口)
6. [Auth CLI 命令](#6-auth-cli-命令)
7. [显示规范](#7-显示规范)
8. [错误码](#8-错误码)
9. [国际化消息](#9-国际化消息)

---

## 1. CredentialResolver API

### 1a. 唯一入口

```python
class CredentialResolver:
    """统一凭据解析器。

    所有凭据读取必须通过此类的 get_status() 方法。
    不接收外部 config — 内部自动加载所有标准配置来源。

    优先级（低 → 高）:
      1. ZMAI Credential Store  — ~/.zmai/credentials
      2. Config File            — zmai.json / ~/.zmai/config.json
      3. Environment Variable   — DEEPSEEK_API_KEY

    原则:
      - 永不修改环境变量
      - 永不抛出异常（错误在 status.error 中）
      - 所有消费者得到相同结果
    """

    def get_status(self, provider: str) -> CredentialStatus:
        """获取指定 Backend 的完整认证状态。

        Args:
            provider: Backend 名称（"deepseek", "claude", "openai", "gemini"）

        Returns:
            CredentialStatus — 永不抛出异常
        """
        ...

    def inject_to_env(self, provider: str | None = None) -> None:
        """将凭据注入进程环境变量（仅内部使用）。

        仅用于 _inject_auth_credentials 在 main() 启动时的后台注入。
        不覆盖已存在的环境变量。
        不修改 Shell 配置文件。
        """
        ...
```

### 1b. 禁止的调用模式

```python
# ❌ 禁止：直接读环境变量
os.environ.get("DEEPSEEK_API_KEY")

# ❌ 禁止：直接读凭据文件
AuthStore().get_backend("deepseek")

# ❌ 禁止：用不同的参数创建 Resolver
CredentialResolver(config=...).resolve(name)

# ✅ 正确：唯一方式
CredentialResolver().get_status("deepseek")
```

---

## 2. CredentialStatus 结构

### 2a. 定义

```python
@dataclass
class CredentialStatus:
    """Backend 凭据统一状态报告。

    所有消费者通过此结构获取凭据信息。
    永不抛出异常 — 错误在 error 字段中。
    """

    # ── 身份 ──────────────────────────────────────────
    provider: str = ""              # "deepseek"

    # ── 凭据概要 ──────────────────────────────────────
    configured: bool = False        # 是否存在有效 Key
    source: str = "missing"         # 最终生效来源
    # "credential_store" | "config_file" | "environment" | "cli" | "missing"

    # ── Key 详情 ──────────────────────────────────────
    key_present: bool = False       # Key 不为空
    key_mask: str = ""              # 前 7 位 + "****"（显示用）
    key_valid_format: bool = False  # 格式校验

    # ── 各来源状态 ─────────────────────────────────────
    credential_store_status: str = "not_found"
    # "ok" | "not_found" | "not_configured"
    # | "empty" | "corrupted" | "key_mismatch" | "format_error"

    config_file_status: str = "not_found"
    # "ok" | "not_found" | "no_key" | "error"

    env_var_status: str = "not_found"
    # "ok" | "not_found" | "empty"

    env_var_name: str = ""          # 环境变量名（显示用）

    # ── 冲突 ──────────────────────────────────────────
    conflict: bool = False
    conflict_details: list[ConflictDetail] = field(default_factory=list)

    # ── 最终值（内部使用，不给用户看） ─────────────────
    api_key: str = ""               # 完整 Key（传给 Backend config）
    model: str = ""
    base_url: str = ""
    timeout: int = 0
    max_tokens: int = 0
    temperature: float = 0.0

    # ── 错误 ──────────────────────────────────────────
    error: str = ""                 # 错误码，空=无错误
    error_message: str = ""         # 用户可读的错误消息

    # ── 验证 ──────────────────────────────────────────
    verification: str = "unknown"
    # "unknown" | "valid" | "invalid" | "network_error" | "rate_limited"


@dataclass
class ConflictDetail:
    source: str          # "credential_store" | "config_file" | "environment"
    label: str           # 人类可读标签
    key_hash: str        # SHA256 前 8 位
```

### 2b. 序列化输出（给 UI 用）

```json
{
  "provider": "deepseek",
  "configured": true,
  "source": "environment",
  "key_mask": "sk-da4f****",
  "key_valid_format": true,
  "credential_store_status": "ok",
  "config_file_status": "no_key",
  "env_var_status": "ok",
  "env_var_name": "DEEPSEEK_API_KEY",
  "conflict": true,
  "conflict_details": [
    {"source": "credential_store", "label": "Credential Store", "key_hash": "a1b2c3d4"},
    {"source": "environment", "label": "Environment Variable", "key_hash": "e5f6g7h8"}
  ],
  "error": "",
  "error_message": ""
}
```

---

## 3. 优先级策略

### 3a. 优先级（低 → 高）

```
  优先级 1（最低）:
    来源: ZMAI Credential Store
    路径: ~/.zmai/credentials
    用户操作: zmai auth update <provider>
    特点: 加密存储，机器绑定

  优先级 2:
    来源: Config File
    路径: zmai.json / ~/.zmai/config.json
    用户操作: 手动编辑
    特点: 适合团队项目统一配置

  优先级 3（最高）:
    来源: Environment Variable
    变量: DEEPSEEK_API_KEY
    用户操作: export DEEPSEEK_API_KEY=...
    特点: 临时覆盖，CI/CD 友好
```

### 3b. 环境变量命名

| Provider | Environment Variable |
|----------|---------------------|
| `deepseek` | `DEEPSEEK_API_KEY` |
| `claude` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `gemini` | `GEMINI_API_KEY` |

### 3c. 合并逻辑

```python
def _merge(self, status: CredentialStatus) -> None:
    """按优先级合并各来源的值。"""
    # 1. 从 credential_store 加载（最低优先级）
    if status.credential_store_status == "ok":
        # 设置 status.api_key, model, base_url, ...
        # status.source = "credential_store"

    # 2. 从 config_file 加载（覆盖 Store）
    if status.config_file_status == "ok":
        # 覆盖 status.api_key, model, ...
        # status.source = "config_file"

    # 3. 从 env_var 加载（最高优先级）
    if status.env_var_status == "ok":
        # 覆盖 status.api_key, model, ...
        # status.source = "environment"

    # 4. 设置最终标记
    status.configured = bool(status.api_key)
    if status.api_key:
        status.key_mask = status.api_key[:7] + "****"
    else:
        status.source = "missing"
```

---

## 4. 冲突检测

### 4a. 定义

冲突 = 多个来源同时包含 Key，且 Key 不同。

| 场景 | 来源 A | 来源 B | 结果 |
|------|--------|--------|------|
| 无冲突 | (无) | (无) | `conflict=False` |
| 无冲突 | store: sk-A | (无) | `conflict=False` |
| 无冲突 | store: sk-A | env: sk-A (相同) | `conflict=False` |
| **冲突** | **store: sk-A** | **env: sk-B** | **`conflict=True`** |
| **冲突** | **store: sk-A** | **config: sk-B** | **`conflict=True`** |
| **冲突** | **store: sk-A** | **config: sk-B, env: sk-C** | **`conflict=True`**（三源） |

### 4b. 冲突检测算法

```python
def _detect_conflict(self, status: CredentialStatus) -> None:
    """检测多来源 Key 冲突。"""
    source_keys: list[tuple[str, str]] = []

    if status.credential_store_status == "ok":
        store = AuthStore()
        data = store.read(status.provider)
        if data and data.get("api_key"):
            source_keys.append(("credential_store", data["api_key"]))

    if status.config_file_status == "ok":
        self._ensure_config_loaded()
        k = self._config.get(f"backends.{status.provider}.api_key", "")
        if k:
            source_keys.append(("config_file", k))

    if status.env_var_status == "ok":
        k = os.environ.get(status.env_var_name, "")
        if k:
            source_keys.append(("environment", k))

    if len(source_keys) < 2:
        return

    # 用 SHA256 比较
    unique: dict[str, str] = {}
    for source, key in source_keys:
        h = hashlib.sha256(key.encode()).hexdigest()[:8]
        if h not in unique:
            unique[h] = source

    if len(unique) > 1:
        status.conflict = True
        for h, src in unique.items():
            status.conflict_details.append(ConflictDetail(
                source=src,
                label=_source_label(src),
                key_hash=h,
            ))
```

### 4c. 消费者如何处理冲突

```python
# 在 Preflight Check 中:
status = CredentialResolver().get_status("deepseek")

if status.conflict:
    print("  ⚠ 检测到多个不同的 API Key")
    for d in status.conflict_details:
        print(f"    {d.label}: hash={d.key_hash}")
    print(f"  当前使用: {status.source}")
    print()
    # 不阻塞运行，但显示警告

if not status.configured:
    print("  未检测到有效 API Key")
    print(f"  来源: {status.source}")
    sys.exit(1)
```

---

## 5. 模块接口

### 5a. Preflight Check

```python
# src/zmai/runtime/preflight.py

def check(backend_name, gateway, config) -> PreflightResult:
    """运行前检查。"""

    # 1. 解析当前 Backend
    resolved = backend_name or gateway.default_name
    if not resolved:
        return PreflightResult(passed=False, reason="NO_BACKEND_SELECTED")

    # 2. 统一凭据检查
    from zmai.auth.resolver import CredentialResolver
    status = CredentialResolver().get_status(resolved)

    # 3. 冲突警告
    if status.conflict:
        logger.warning(
            "Backend %s: credential conflict detected, using %s",
            resolved, status.source,
        )

    # 4. API Key 缺失
    if not status.configured:
        return PreflightResult(
            passed=False,
            reason="API_KEY_MISSING",
            message=(
                f"当前 Backend: {resolved}\n"
                f"未检测到有效 API Key。\n"
                f"\n"
                f"请执行:\n"
                f"  zmai auth update {resolved}\n"
                f"或者设置环境变量:\n"
                f"  {status.env_var_name}"
            ),
            backend_name=resolved,
        )

    return PreflightResult(passed=True, backend_name=resolved)
```

### 5b. PluginRegistry._build_config

```python
# src/zmai/gateway/plugin.py

def _build_config(self, plugin: BackendPlugin) -> dict[str, Any]:
    """按优先级合并配置。"""
    cfg = {
        "model": plugin.default_model,
        "base_url": plugin.default_base_url,
        "timeout": plugin.default_timeout,
        "max_tokens": plugin.default_max_tokens,
        "temperature": plugin.default_temperature,
    }

    status = CredentialResolver().get_status(plugin.name)
    if status.configured:
        cfg["api_key"] = status.api_key
        cfg["model"] = status.model or cfg["model"]
        cfg["base_url"] = status.base_url or cfg["base_url"]
        cfg["timeout"] = status.timeout or cfg["timeout"]
        cfg["max_tokens"] = status.max_tokens or cfg["max_tokens"]
        cfg["temperature"] = status.temperature or cfg["temperature"]

    cfg["backend"] = plugin.name

    if status.conflict:
        logger.warning(
            "Backend %s: credential conflict, using %s",
            plugin.name, status.source,
        )

    return cfg
```

### 5c. Backend 实现

```python
# src/zmai/gateway/backends/deepseek.py

class DeepSeekBackend(Backend):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        c = self._config
        # api_key 由 PluginRegistry._build_config 通过 CredentialResolver 装配
        # Backend 绝不自行读取环境变量或凭据文件
        self._api_key: str = c.get("api_key", "")
        # ... 其他配置
```

### 5d. `zmai auth update` — 不修改 Shell 环境变量

```python
# src/zmai/cli/main.py

def _run_auth_update(argv):
    """更新凭据。只写文件，不修改环境变量。"""
    name = argv[1]
    key = argv[2] if len(argv) > 2 else ""

    if not key:
        key = input().strip()

    if not key:
        print("API Key required", file=sys.stderr)
        sys.exit(1)

    store = AuthStore()
    store.set_backend(name, key, ..., make_active=True)

    # 不修改环境变量
    # 不修改 Shell 配置

    print(f"{name} saved to credential store")
    print()
    print(f"提示: 如果设置了环境变量 {env_key}，")
    print(f"      环境变量的优先级高于凭据文件。")
    print(f"      如需使用刚保存的 Key:")
    print(f"        unset {env_key}")
```

---

## 6. Auth CLI 命令

### 6a. 完整命令集

```
zmai auth                     → 显示认证状态摘要
zmai auth status              → 显示详细认证状态
zmai auth setup               → 首次配置向导
zmai auth update <provider>   → 更新凭据（只写文件）
zmai auth test <provider>     → 测试 API Key 有效性
zmai auth list                → 列出已保存的凭据
zmai auth switch <provider>   → 切换默认 Backend
zmai auth remove <provider>   → 删除凭据
```

### 6b. `zmai auth status` 显示

```
  Authentication Status
  ────────────────────────

  DeepSeek
    Configured : Yes
    Source     : Credential Store
    Key        : sk-da4f****
    Verified   : 2026-07-18
    ⚠ Environment variable DEEPSEEK_API_KEY exists with a different key.

  Claude (Anthropic)
    Configured : No
    Source     : None
    Key        : -

  OpenAI
    Configured : No
    Source     : None
    Key        : -

  Gemini (Google)
    Configured : Yes
    Source     : Environment Variable (GEMINI_API_KEY)
    Key        : AIza****
    Verified   : -
```

### 6c. `zmai auth test deepseek` 输出

```
  Testing DeepSeek...
  ────────────────────────

  Resolving credentials...
    Source : Credential Store
    Key    : sk-da4f****

  Sending API request...
    URL    : https://api.deepseek.com/v1/models
    Method : GET

  Result : ✅ PASS
  Model  : deepseek-chat
  Detail : API Key is valid, service responding.

  ────────────────────────
  Ready to use.
```

失败时：

```
  Testing DeepSeek...
  ────────────────────────

  Resolving credentials...
    Source : Environment Variable (DEEPSEEK_API_KEY)
    Key    : sk-ca40****

  Sending API request...
    URL    : https://api.deepseek.com/v1/models
    Method : GET

  Result : ❌ FAIL
  Status : 401 Unauthorized
  Reason : API Key is invalid.

  ────────────────────────
  Please run `zmai auth update deepseek`
  or set a valid DEEPSEEK_API_KEY.
```

### 6d. `zmai auth setup` 向导

```
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ZMAI — Setup

  No API providers configured yet.

  Choose a provider:
    [1] DeepSeek      (Recommended)
    [2] Claude        (Anthropic)
    [3] OpenAI
    [4] Gemini        (Google)

  Enter number [1-4]: 1
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Enter your DeepSeek API Key:
  (Paste your key, press Enter to confirm)

  > sk-********************************

  Testing API Key...

  ✅ DeepSeek API Key is valid.
  Model: deepseek-chat

  Configuration saved.

  Try it now:
    zmai "write a Python script"
```

---

## 7. 显示规范

### 7a. Key 掩码规则

| Key 长度 | 显示 | 示例 |
|----------|------|------|
| < 7 | `****` | `****` |
| ≥ 7 | 前 7 位 + `****` | `sk-da4f****` |
| 空 | `-` | `-` |

```python
def mask_key(key: str) -> str:
    """对 API Key 做掩码处理，仅用于显示。"""
    if not key:
        return "-"
    if len(key) < 7:
        return "****"
    return key[:7] + "****"
```

### 7b. 来源标签

| 来源标识 | 显示文本 |
|---------|---------|
| `credential_store` | "Credential Store" |
| `config_file` | "Config File" |
| `environment` | "Environment Variable (DEEPSEEK_API_KEY)" |
| `cli` | "CLI Argument" |
| `missing` | "None" |

### 7c. 来源状态标签

| 状态值 | 显示文本 |
|--------|---------|
| `ok` | "OK" |
| `not_found` | "Not Found" |
| `not_configured` | "Not Configured" |
| `empty` | "Empty" |
| `corrupted` | "Corrupted" |
| `key_mismatch` | "Key Mismatch" |
| `format_error` | "Format Error" |
| `no_key` | "No Key" |
| `error` | "Error" |

---

## 8. 错误码

### 8a. 完整错误码表

| 错误码 | HTTP 状态 | 用户消息 | 原因 |
|--------|----------|---------|------|
| `KEY_MISSING` | - | "未检测到 API Key" | 所有来源均无 Key |
| `KEY_EMPTY` | - | "API Key 为空字符串" | env 设为空 |
| `KEY_INVALID` | 401 | "API Key 无效" | 服务端拒绝 |
| `KEY_EXPIRED` | 403 | "API Key 已过期" | 权限拒绝 |
| `RATE_LIMITED` | 429 | "请求过于频繁" | 限流 |
| `NETWORK_ERROR` | - | "无法连接到服务器" | DNS/代理 |
| `CREDENTIALS_CORRUPTED` | - | "凭据文件损坏" | 解密失败 |
| `KEY_FILE_CORRUPTED` | - | "密钥文件损坏" | credentials.key 损坏 |
| `FORMAT_ERROR` | - | "凭据文件格式错误" | JSON 解析失败 |
| `CONFIG_ERROR` | - | "配置文件错误" | zmai.json 格式错误 |
| `UNKNOWN_ERROR` | - | "未知错误" | 未归类异常 |

### 8b. Backend HTTP 错误映射

```python
# src/zmai/gateway/errors.py

def friendly_http_error(
    http_code: int,
    provider: str,
    model: str,
    env_key: str,
) -> str:
    """将 HTTP 错误映射为用户可理解的错误消息。"""
    if http_code == 401:
        return (
            f"[KEY_INVALID] {provider}: API Key 无效。\n"
            f"请运行 `zmai auth update {provider}`\n"
            f"或设置环境变量 {env_key}"
        )
    if http_code == 403:
        return (
            f"[KEY_EXPIRED] {provider}: API Key 可能已过期。\n"
            f"请检查你的 API Key 是否仍然有效。"
        )
    if http_code == 429:
        return (
            f"[RATE_LIMITED] {provider}: 请求过于频繁。\n"
            f"请稍后重试。"
        )
    return (
        f"[BACKEND_ERROR] {provider} 返回错误 (HTTP {http_code})\n"
        f"请稍后重试，或检查 API 状态。"
    )
```

---

## 9. 国际化消息

### 9a. 消息注册表（初始中英文）

```python
MESSAGES: dict[str, dict[str, str]] = {
    # ── Key 状态 ──────────────────────────────────
    "KEY_MISSING": {
        "en": "No valid API Key detected for {provider}.",
        "zh": "未检测到 {provider} 的有效 API Key。",
    },
    "KEY_INVALID": {
        "en": "{provider}: API Key is invalid (HTTP 401).",
        "zh": "{provider}: API Key 无效（HTTP 401）。",
    },
    "KEY_CONFLICT": {
        "en": "Multiple different API Keys detected for {provider}.",
        "zh": "检测到 {provider} 的多个不同 API Key。",
    },
    # ── 来源 ──────────────────────────────────────
    "SOURCE_CREDENTIAL_STORE": {
        "en": "Credential Store",
        "zh": "凭据存储",
    },
    "SOURCE_ENVIRONMENT": {
        "en": "Environment Variable ({name})",
        "zh": "环境变量（{name}）",
    },
    "SOURCE_CONFIG_FILE": {
        "en": "Config File",
        "zh": "配置文件",
    },
    # ── 操作提示 ──────────────────────────────────
    "ACTION_UPDATE": {
        "en": "Run `zmai auth update {provider}`",
        "zh": "请执行 `zmai auth update {provider}`",
    },
    "ACTION_SET_ENV": {
        "en": "Or set environment variable: {name}",
        "zh": "或者设置环境变量：{name}",
    },
}

def msg(code: str, **kwargs) -> str:
    """获取消息（当前始终返回中文）。"""
    return MESSAGES[code]["zh"].format(**kwargs)
```

---

*本文件定义了 CredentialResolver 的完整 API 设计。*
*迁移计划见 `../security/CREDENTIAL_MIGRATION_PLAN.md`。*
