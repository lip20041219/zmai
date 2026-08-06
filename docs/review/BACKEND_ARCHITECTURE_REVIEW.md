# Backend Architecture Review

**Date**: 2026-07-17  
**Scope**: Full review of Backend abstraction layer — Interface, Gateway, Registry, Config, Provider  
**Goal**: Ensure Runtime depends only on `Backend` Interface, never on concrete providers

---

## 1. Backend Interface — 部分统一

### ✅ 良好设计

`Backend(ABC)` in `gateway/base.py` provides a clean abstract contract:

| Element | Type | Status |
|---|---|---|
| `Backend.invoke()` | `@abstractmethod` | ✅ Unified |
| `Backend.stream()` | `@abstractmethod` | ✅ Unified |
| `Backend.capabilities` | `@abstractmethod @property` | ✅ Unified |
| `BackendRequest` | dataclass | ✅ Unified |
| `BackendResponse` | dataclass | ✅ Unified |
| `BackendEvent` | dataclass | ✅ Unified |
| `TokenUsage` | dataclass | ✅ Unified |

### ❌ 问题

1. **`Backend.model` property default returns `""`** (`gateway/base.py:137-139`)  
   基类不强制子类提供模型名称，子类需要自行覆盖。这不是严格的设计问题，但缺少契约约束。

2. **`Backend.provider` property 默认返回 `self.name`** (`gateway/base.py:142-144`)  
   `ClaudeBackend` 覆盖为 `provider: str = "anthropic"` (`claude.py:48`)，这是好的做法。但 `DeepSeekBackend` 覆盖为 `"deepseek"` (`deepseek.py:40`)，其实与 `name` 相同。接口层面没有约定 provider 值的语义规范。

---

## 2. Gateway — 统一

### ✅ 良好设计

`gateway/__init__.py` 作为统一的导出门户，只暴露抽象类型：
- `Backend`, `BackendRegistry`, `BackendCapability`
- `BackendEvent`, `BackendRequest`, `BackendResponse`, `TokenUsage`
- `MCPClient`, `ToolRouter`

`ToolRouter` (`gateway/tool_router.py`) 完全与 Backend 无关，只依赖 `ToolRegistry` 和 `ToolCall`。

`MCPClient` (`gateway/mcp.py`) 完全独立，不引用任何 Backend 类型。

**结论**: Gateway 层没有 provider 泄漏。

---

## 3. BackendRegistry — 统一

### ✅ 良好设计

`BackendRegistry` (`gateway/registry.py`) 是纯抽象的注册表：
- 只依赖 `Backend(ABC)`
- 无任何 provider 名称硬编码
- 实例按需创建并缓存
- 支持默认 Backend 设置

### ❌ 问题 — 注册数据源不统一

Registry 本身是干净的，但 **注册的来源 (`Runtime._register_available_backends`)** 依赖硬编码的数据源：

**`gateway/backends/__init__.py:17-39`** — `BACKEND_METADATA` 硬编码字典：

```python
BACKEND_METADATA: dict[str, dict] = {
    "claude": {
        "label": "Claude (Anthropic)",
        "default_model": "claude-sonnet-4-6",
        "env_api_key": "ANTHROPIC_API_KEY",
        "env_model": "ANTHROPIC_MODEL",
        "module": "zmai.gateway.backends.claude",
        "class": "ClaudeBackend",
        "verify_url": "https://api.anthropic.com/v1/messages",
        "verify_method": "POST",
        "verify_headers": {"anthropic-version": "2023-06-01"},
    },
    "deepseek": { ... },
}
```

**问题**: 
- 新增 Backend 需要修改此字典及 `__init__.py` 中的 import 语句
- **Runtime 的注册逻辑依赖此字典** (`runtime.py:344-366`): `_register_available_backends()` 读取 `BACKEND_METADATA` 来决定注册哪些 backends
- **AuthStore 依赖此字典** (`auth/store.py:182-185`): `_detect_env_backend()` 遍历它来检测环境变量
- **CLI 依赖此字典** (`cli/main.py:92-206`): 向导、credential 注入都遍历它
- **Doctor 依赖此字典** (`cli/doctor.py:175`): 遍历它做注册测试

**应该改为**: 插件发现机制（如 entry points、config 配置、或扫描 `backends/` 目录）。

---

## 4. Config — 统一

### ✅ 良好设计

`Config` (`config/config.py`) 完全 provider-agnostic：
- 多源合并 (file → env → CLI)
- 无任何 Backend 相关代码
- `zmai.json` 中 `gateway.default_backend: "auto"` 是名称字符串，不是 provider 特定的

`ConfigSource` 体系 (`config/sources.py`) 也是完全通用的。

---

## 5. Provider — 不统一 ⚠️

### 核心问题: `BACKEND_METADATA` 作为 provider 的耦合中心

| 文件 | 行 | 问题 |
|---|---|---|
| `gateway/backends/__init__.py` | 17-39 | `BACKEND_METADATA` 硬编码两个 provider 的信息 |
| `gateway/backends/__init__.py` | 42-49 | `get_backend_info()` 和 `get_available_backends()` 从此字典读取 |
| `runtime/runtime.py` | 344-366 | `_register_available_backends()` 依赖此字典 |
| `runtime/runtime.py` | 368-382 | `_auto_select_default_backend()` 依赖此字典 |
| `auth/store.py` | 182-185 | `_detect_env_backend()` 依赖此字典 |
| `cli/main.py` | 92-103, 108-126 | 向导和 credential 注入依赖此字典 |
| `cli/doctor.py` | 170-195 | `_check_backend()` 依赖此字典 |

**影响**: 如果要新增一个 Backend（例如 OpenAI），必须修改：
1. 新建 `gateway/backends/openai.py`
2. 修改 `gateway/backends/__init__.py` — 添加 import 和 `BACKEND_METADATA` 条目
3. 三个地方的 env var 命名规则隐含在 metadata 中（无统一规范）

### Backend 实现层面

`ClaudeBackend` (`claude.py`):
- `name: str = "claude"` — 类属性硬编码
- `provider: str = "anthropic"` — 类属性硬编码
- `CLAUDE_DEFAULT_MODEL`, `CLAUDE_API_BASE`, `CLAUDE_API_VERSION` — 模块级常量

`DeepSeekBackend` (`deepseek.py`):
- `name: str = "deepseek"` — 类属性硬编码
- `provider: str = "deepseek"` — 类属性硬编码
- `DEEPSEEK_DEFAULT_MODEL`, `DEEPSEEK_API_BASE` — 模块级常量

这些是 Backend 实现内部细节，**不是 Runtime 的问题** — Runtime 不直接引用它们。但 `name` 和 `provider` 的语义规范需要明确。

---

## 6. 模型硬编码 — 存在

| 文件 | 行 | 硬编码值 |
|---|---|---|
| `gateway/backends/claude.py` | 30-33 | `CLAUDE_DEFAULT_MODEL = "claude-sonnet-4-6"` |
| | | `CLAUDE_API_BASE = "https://api.anthropic.com/v1"` |
| | | `CLAUDE_DEFAULT_MAX_TOKENS = 4096` |
| | | `CLAUDE_API_VERSION = "2023-06-01"` |
| `gateway/backends/deepseek.py` | 25-26 | `DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"` |
| | | `DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"` |
| `gateway/backends/__init__.py` | 20 | `"default_model": "claude-sonnet-4-6"` |
| `gateway/backends/__init__.py` | 31 | `"default_model": "deepseek-chat"` |

这些硬编码在 Backend **实现内部**，Runtime 不直接使用它们。如果目标是让 Runtime 不直接引用它们，当前架构已经做到了。

**但从抽象角度看**: 模型名称应该通过 config 注入而非代码硬编码。当前架构中 config 可以覆盖（`ClaudeBackend.__init__` 读取 `config.get("model", CLAUDE_DEFAULT_MODEL)`），所以硬编码是**后备默认值**，可以接受但不是最佳。

---

## 7. `if backend == xxx` 检查 — 仅一处

### ✅ Runtime 无 `if backend == xxx`

Runtime (`runtime/runtime.py`)、SWE Agent (`swe/agent.py`)、LifecycleManager (`runtime/lifecycle.py`) 全部通过 `Backend` 接口调用，没有任何 provider 名称检查。

### ⚠️ CLI/Doctor 无 `if backend == xxx` 直接判断，但有等效逻辑

遍历 `BACKEND_METADATA` 的代码本质上等同于 provider 检查 — 因为 `BACKEND_METADATA` 的 key 就是 provider 名称。

### ❌ `cli/doctor.py:179` — 显式硬编码

```python
reg.register(name, cls, default=(name == "claude"))
```

这是**唯一一处显式的 `name == "claude"` 硬编码**。应使用 `BACKEND_METADATA` 中的优先级或 config 来决定默认值。

### ❌ `runtime/runtime.py:374` — 隐式顺序依赖

```python
# 按 BACKEND_METADATA 定义顺序选第一个有环境变量凭证的
for name, info in get_available_backends().items():
```

默认 Backend 的选择依赖 `BACKEND_METADATA` 字典的定义顺序。如果 `BACKEND_METADATA` 被排序或重新组织，默认选择行为会改变。这是隐式的 provider 优先级硬编码。

---

## 总结

| 检查项 | 状态 | 说明 |
|---|---|---|
| 1. Backend Interface 统一 | ✅ 部分统一 | ABC 设计良好，但 `model` 和 `provider` 属性缺少强契约约束 |
| 2. Gateway 统一 | ✅ 统一 | 导出层干净，无 provider 泄漏 |
| 3. BackendRegistry 统一 | ✅ 统一 | Registry 本身干净，但 **注册数据源** 硬编码 |
| 4. Config 统一 | ✅ 统一 | Config 系统完全 provider-agnostic |
| 5. Provider 统一 | ❌ 不统一 | `BACKEND_METADATA` 是 provider 耦合中心，新增 Backend 需改代码 |
| 6. 模型硬编码 | ⚠️ 存在 | 各 Backend 实现内部有默认模型硬编码；配置可覆盖，可接受但不是最佳 |
| 7. `if backend == xxx` | ⚠️ 1 处显式 + 多处隐式 | `doctor.py:179` 有 `name == "claude"`；多处遍历 `BACKEND_METADATA` 等效于 provider 检查 |

### 核心矛盾

Runtime 不直接引用 Claude/DeepSeek，但 `BACKEND_METADATA` 硬编码 dict 在 `gateway/backends/__init__.py` 中，被 Runtime、CLI、AuthStore、Doctor 四处引用。**这是一个 "半抽象" 状态** — 接口统一了，但 provider 的注册和发现机制仍是硬编码的。

### 推荐改进方向（不改代码，仅建议）

1. **消除 `doctor.py:179` 的 `name == "claude"`** — 改用 config 或 metadata 中的 "优先级" 字段
2. **将 `BACKEND_METADATA` 外部化** — 改为 JSON config 或 Python entry points 机制，使新增 Backend 不需要改 `__init__.py`
3. **统一 env var 命名规范** — 如所有 provider 的 env var 遵循 `{NAME}_API_KEY` 和 `{NAME}_MODEL` 模式，消除 metadata 中不同 env var 名的差异
4. **让 `Backend.name` 和 `Backend.provider` 成为 `@abstractmethod`** — 强制子类实现，提供契约保证
