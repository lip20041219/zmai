# ZMAI Backend System Review

> 审查日期: 2026-07-17
> 范围: Gateway, BackendRegistry, API Key, Provider, Model, Backend Routing
> 原则: 发现所有对 Claude, DeepSeek, OpenAI, Gemini 的硬编码引用
> 禁止: 修改代码 — 仅为审查

---

## 一、硬编码分布总览

| 层级 | 文件 | 硬编码类别 | 严重度 |
|------|------|-----------|--------|
| Runtime 自动注册 | `runtime.py:336-340` | provider 名称、env var 名、模块路径、class 名、默认模型 | **P0** |
| Runtime 自动选择 | `runtime.py:373` | provider 名称、env var 名、选择优先级顺序 | **P0** |
| Runtime 导入 | `runtime.py:15` | 硬编码 import `ClaudeBackend`（未使用） | P2 |
| CLI 初始化向导 | `cli/main.py:92` | env var 名列表 | **P0** |
| CLI 初始化向导 | `cli/main.py:119-124` | provider 名称、显示名、默认模型名 | **P0** |
| CLI 初始化向导 | `cli/main.py:151-156` | 验证 URL、API 版本 header | **P0** |
| CLI 初始化向导 | `cli/main.py:162-163` | provider 特判 (`anthropic` + 特殊 header) | **P0** |
| CLI 凭证注入 | `cli/main.py:197-200` | provider 名称、env var 名 | **P0** |
| CLI 帮助文本 | `cli/main.py:248` | provider 名称列表 | P1 |
| Auth Store | `auth/store.py:182` | provider 名称、env var 名 | **P0** |
| Gateway Backends | `claude.py` 全文件 | API base URL、默认模型名、API 版本、类名 | P1 |
| Gateway Backends | `deepseek.py` 全文件 | API base URL、默认模型名、类名 | P1 |
| Gateway Backends | `backends/__init__.py` | 硬编码 import + export | P2 |
| zmai.json | 项目配置 | `claude` 配置块、默认模型 | P2 |

---

## 二、详细发现

### P0 — 新增 Backend 需要修改 7 处代码

添加一个新的 LLM Provider（如 Ollama）需要同时修改以下所有位置：

| # | 文件:行 | 当前硬编码 | 必须改什么 |
|---|---------|-----------|-----------|
| 1 | `runtime.py:337-340` | `backends_to_check = [("claude", ...), ("deepseek", ...)]` | 追加新 tuple |
| 2 | `runtime.py:373` | `[("deepseek", "DEEPSEEK_API_KEY"), ("claude", "ANTHROPIC_API_KEY")]` | 追加新优先级 |
| 3 | `cli/main.py:92` | `("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", ...)` | 追加新 env var |
| 4 | `cli/main.py:119-124` | `[("deepseek", "DeepSeek", "deepseek-chat"), ...]` | 追加新 backend |
| 5 | `cli/main.py:151-156` | URL 字典 | 追加新验证 URL |
| 6 | `cli/main.py:197-200` | env var 映射 | 追加新映射 |
| 7 | `auth/store.py:182` | `("deepseek", "anthropic", "openai")` | 追加新名称 |

**这是最严重的架构问题** — 数据没有归一化到单一配置源，而是散布在 3 个模块 7 个位置。

---

### P0 — `_register_available_backends()` 使用字符串导入，无法扩展

**文件:** `src/zmai/runtime/runtime.py:336-364`

```python
backends_to_check = [
    ("claude",   "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
     "zmai.gateway.backends.claude", "ClaudeBackend", "claude-sonnet-4-6"),
    ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
     "zmai.gateway.backends.deepseek", "DeepSeekBackend", "deepseek-chat"),
]
...
mod = importlib.import_module(mod_path)
cls = getattr(mod, cls_name)
self._gateway.register(name, cls, config={"model": model})
```

问题：

- **模块路径硬编码**：`"zmai.gateway.backends.claude"` — 不能通过配置添加
- **类名硬编码**：`"ClaudeBackend"` — 必须是字符串，无法静态类型检查
- **默认模型硬编码**：`"claude-sonnet-4-6"` — 即使模型名来自配置或 env var，默认值仍然钉死
- **env var 名硬编码**：`"ANTHROPIC_API_KEY"` — 成为 config 规范的一部分而非实现细节
- **仅限 2 个 provider**：deepseek + claude，openai 和 gemini 的 env var 在 CLI 中定义（行 199-200）但不在 auto-register 中检查

---

### P0 — `_auto_select_default_backend()` 有硬编码优先级顺序

**文件:** `src/zmai/runtime/runtime.py:366-379`

```python
def _auto_select_default_backend(self) -> None:
    cfg = self._config.get("gateway.default_backend", "auto")
    ...
    for name, var in [("deepseek", "DEEPSEEK_API_KEY"), ("claude", "ANTHROPIC_API_KEY")]:
        if name in self._gateway.list() and _os.environ.get(var):
            self._gateway.set_default(name)
            return
    if self._gateway.list():
        self._gateway.set_default(self._gateway.list()[0])
```

问题：

- **回退优先级 DeepSeek > Claude** — 硬编码决定了当两者都有环境变量时 DeepSeek 优先
- **只检查 2 个 provider** — OpenAPI 和 Gemini 的环境变量被忽略
- **列表顺序即优先级** — 添加新 provider 者必须理解这个隐含约定
- **没有 config 驱动的优先级** — 配置 `gateway.default_backend = "auto"` 的语义隐藏在代码逻辑中

---

### P0 — CLI 初始化向导深度硬编码 4 个 Backend

**文件:** `src/zmai/cli/main.py:119-163`

```python
backends = [
    ("deepseek", "DeepSeek", "deepseek-chat"),
    ("anthropic", "Claude", "claude-sonnet-4-6"),
    ("openai", "OpenAI", "gpt-4o"),
    ("gemini", "Gemini", "gemini-2.0-flash"),
]

urls = {
    "deepseek": "https://api.deepseek.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/models",
    "gemini": f"https://generativelanguage.googleapis.com/v1/models?key={key}",
}
```

问题：

- **4 个 Backend 的名称、显示名、默认模型全部硬编码** — 不可配置
- **验证 URL 硬编码** — 如果 API 迁移或版本升级，必须改源码
- **Provider 特判**：`if name == "anthropic": req.add_header("anthropic-version", ...)` — 验证逻辑因 provider 不同而需要特殊处理，但硬编码 if/else 无法扩展
- **模型版本钉死**：`gpt-4o`、`gemini-2.0-flash`、`claude-sonnet-4-6` — 会随时间过时
- **"默认第 1 个"**：`sel = input().strip() or "1"` — 默认指向 DeepSeek，因为它是第一个

---

### P0 — CLI `_inject_auth_credentials()` 硬编码 4 个 Backend

**文件:** `src/zmai/cli/main.py:194-211`

```python
def _inject_auth_credentials() -> None:
    for name, env_key, env_model in [
        ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"),
        ("anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"),
        ("openai", "OPENAI_API_KEY", "OPENAI_MODEL"),
        ("gemini", "GEMINI_API_KEY", "GEMINI_MODEL"),
    ]:
```

认证凭据注入逻辑对每个 Backend 使用固定的 env var 名称三元组。
新增 Backend 时必须同时修改此列表。

---

### P0 — AuthStore._detect_env_backend() 硬编码 3 个 Provider

**文件:** `src/zmai/auth/store.py:180-186`

```python
def _detect_env_backend(self) -> str:
    for name in ("deepseek", "anthropic", "openai"):
        key = name.upper() + "_API_KEY"
        if os.environ.get(key):
            return name
    return ""
```

问题：

- 枚举了 3 个名称，**缺少 Gemini**
- 命名约定 `{NAME}_API_KEY` 是硬编码推导规则，不是所有 provider 都使用此模式
- 返回第一个匹配，但顺序（deepseek → anthropic → openai）是硬编码的优先级
- 此函数从 AuthStore 查询，但检测逻辑与 Registry 无关 — AuthStore 不感知哪些 Backend 已注册

---

### P0 — CLI `--backend` 帮助文本硬编码列表

**文件:** `src/zmai/cli/main.py:248`

```python
p.add_argument("--backend", help="backend name (claude, deepseek, openai, gemini)")
```

帮助文本中的 Backend 列表会随源码同步更新，但用户看不到动态注册的 Backend。
卸载 claude 后帮助仍会列出 claude。

---

### P1 — Backend 实现层包含 Provider 特定的硬编码

**文件:** `src/zmai/gateway/backends/claude.py`

```python
CLAUDE_API_BASE = "https://api.anthropic.com/v1"
CLAUDE_DEFAULT_MODEL = "claude-sonnet-4-6"
CLAUDE_API_VERSION = "2023-06-01"
```

```python
class ClaudeBackend(Backend):
    name: str = "claude"
    provider: str = "anthropic"
```

每个 Backend 实现必然包含其对应的 API 端点、模型默认值、请求格式。这些硬编码是**必要的**，
因为每个 API 不同。问题在于：

- **没有配置覆盖机制**：`base_url` 和 `model` 虽然可以通过 config 覆盖，但 `api_version` 和 `max_retries`
  等也是硬编码
- **超时和时间常数的含义模糊**：`max_retries: int = int(self._config.get("max_retries", 3))` —
  3 次重试对所有 provider 都合理吗？
- **`capabilities` 集完全取决于实现者的判断**：无配置化能力

---

### P1 — `backends/__init__.py` 硬编码只导出 2 个 Backend

**文件:** `src/zmai/gateway/backends/__init__.py:3-6`

```python
from zmai.gateway.backends.claude import ClaudeBackend
from zmai.gateway.backends.deepseek import DeepSeekBackend

__all__ = ["ClaudeBackend", "DeepSeekBackend"]
```

每新增一个 Backend 实现，这个文件就必须修改。更好的做法是自动发现
`backends/` 目录下所有 `Backend` 子类，或从注册表动态导出。

---

### P1 — 错误消息中包含 Provider 名称

**文件:** `src/zmai/gateway/backends/claude.py:100,110,204,208,267,271`

```python
raise BackendError(f"Claude API 调用失败: {last_error}")
raise BackendError(f"Claude API 网络错误: {e}")
```

`ClaudeBackend` 的错误消息中硬编码了 `"Claude"` 字符串。如果 `name` 属性
改为其他值（如子类化后改名为 `"my-claude"`），错误消息仍然显示 `"Claude"`。
应使用 `self.name` 或 `self.provider` 动态生成。

---

### P1 — `BackendRegistry` 不提供插件式注册入口

**文件:** `src/zmai/gateway/registry.py`

```python
def register(self, name, backend_cls, *, default=False, config=None):
    ...
    self._backends[name] = backend_cls
    self._configs[name] = config or {}
```

`BackendRegistry` 接口本身是 provider-agnostic，设计良好。但它没有提供
**从配置动态加载 Backend 的机制**。`_register_available_backends()` 必须
显式调用 `register()` 注册每个 Backend。

---

### P2 — `Runtime.__init__` 有未使用的 `ClaudeBackend` import

**文件:** `src/zmai/runtime/runtime.py:15`

```python
from zmai.gateway.backends import ClaudeBackend
```

`ClaudeBackend` 在 runtime.py 中未直接使用（自动注册使用字符串导入）。
这是旧代码的残留 import。

---

### P2 — Gateway `__init__.py` 导出后 Backend 可被直接 import 无约束

`zmai.gateway.__init__` 导出 `BackendRegistry` 但不导出具体 Backend 类。
但 Python 不阻止 `from zmai.gateway.backends import ClaudeBackend`。
当前 runtime.py:15 就是一个反例。

---

### P2 — zmai.json 项目配置中硬编码了 claude 配置块

**文件:** `zmai.json:10-17`

```json
"gateway": {
    "default_backend": "auto",
    "backends": {
        "claude": {
            "api_key": "${ANTHROPIC_API_KEY}",
            "model": "claude-sonnet-4-6",
            "max_tokens": 4096
        }
    }
}
```

虽然 JSON 配置本身就是可扩展的，但提交到仓库的默认配置包含了
特定的 provider 名称和模型版本。

---

## 三、架构影响分析

### 3.1 添加新 Provider 的改动点

按当前设计，添加一个 Ollama Backend 需要修改：

```
src/zmai/
├── runtime/runtime.py          # 2 处 (+1 tuple, +1 优先级)
├── cli/main.py                 # 4 处 (wizard, URL, inject, help)
├── auth/store.py               # 1 处 (_detect 列表)
├── gateway/backends/
│   ├── __init__.py             # 1 处 (import + export)
│   └── ollama.py               # 新文件
```

共 **至少 9 个改动点**，其中 8 处是修改现有文件。

### 3.2 配置驱动的理想设计

```
gateway:
  backends:
    ollama:
      type: openai_compatible          # 复用 OpenAI 兼容格式的 Backend 实现
      base_url: http://localhost:11434
      model: llama3
      api_key_env: OLLAMA_API_KEY      # 可选
```

在这种设计下，`runtime.py` 和 `cli/main.py` 不需要知道任何 provider 名称。
Backend 实现可以根据 `type` 字段按需加载，配置来源可以是文件、环境变量或注册表。

### 3.3 API Key 命名约定的假设

当前代码假设所有 provider 的 API Key 环境变量遵循 `{NAME}_API_KEY` 模式：

```
DEEPSEEK_API_KEY   → deepseek
ANTHROPIC_API_KEY  → anthropic
OPENAI_API_KEY     → openai
GEMINI_API_KEY     → gemini
```

这个约定在 `auth/store.py:182` 和 `cli/main.py:197-200` 中被硬编码推导。
如果某个 provider 使用不同的命名（如 `AZURE_OPENAI_KEY`），当前逻辑不支持。

---

## 四、汇总

| 严重度 | 数量 | 主要问题 |
|--------|------|---------|
| **P0** | 8 | 添加新 provider 需改 7-9 处文件；自动注册和 CLI 向导深度硬编码 4 个 provider；AuthStore 缺少 Gemini；帮助文本钉死列表 |
| **P1** | 5 | Backend 实现层必要的硬编码缺少配置覆盖；错误消息用字面量而非动态名称；`__init__.py` 硬编码导出；无配置驱动加载 |
| **P2** | 3 | 未使用的 import；zmai.json 默认配置包含特定 provider |

### 最需要优先处理的 3 个问题

1. **添加 Provider 需要修改 7 处代码** (P0) — 数据未归一化到单一配置源
2. **`_register_available_backends()` 和 `_auto_select_default_backend()` 全硬编码** (P0) — 自动发现和自动选择都无法通过配置扩展
3. **CLI 初始化向导钉死 4 个 Provider** (P0) — 新 provider 的用户无法使用向导
