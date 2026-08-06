# ZMAI Plugin Design

> 设计日期: 2026-07-17
> 状态: 设计阶段（未实现）
> 参考: ../ARCHITECTURE.md §8、../SPECIFICATION.md §5、../CLASS.md §6、API.md §6、MODULES.md §8

---

## 一、设计目标

1. **插件化 Backend** — 新的 LLM Provider 通过 Plugin 机制注册，不动 Runtime 代码
2. **生命周期 Hook** — Plugin 可监听 Runtime/Agent/Tool/Memory 事件
3. **可安装/卸载** — `pip install` 安装，`zmai plugin` 管理
4. **隔离安全** — Plugin 崩溃不影响 Runtime 主流程

---

## 二、架构总览

```
┌─────────────────────────────────────────────────────────┐
│                     PluginManager                        │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Plugin(ABC) │  │ HookRegistry │  │ PluginDiscovery │  │
│  │ (N instances)│  │ (10 HookPoints)│  │ (entry_points)  │  │
│  └─────────────┘  └──────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │                        │
         ▼                        ▼
   Runtime lifecycle        zmai.plugins  entry point
   (init/step/complete)     (pyproject.toml)
```

### 2.1 三层结构

| 层 | 文件 | 职责 |
|----|------|------|
| **Plugin ABC** | `plugin/base.py` | 插件基类 + 元数据 |
| **HookRegistry** | `plugin/hooks.py` | Hook 点注册与触发 |
| **PluginManager** | `plugin/manager.py` | 发现/加载/启用/禁用/卸载 |

### 2.2 与 Runtime 的关系

```
Runtime
├── _plugins: PluginManager      ← 新增
├── _lifecycle: LifecycleManager
├── _gateway: BackendRegistry
├── _memory: MemoryManager
└── _workspace: Workspace

Runtime.run():
  trigger("on_runtime_start")
  trigger("on_agent_init")
  for step in steps:
    trigger("on_agent_step")
  trigger("on_agent_complete")
  trigger("on_runtime_stop")
```

---

## 三、Plugin ABC

```python
# plugin/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class PluginStatus(Enum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    ENABLED = "enabled"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass
class PluginInfo:
    name: str
    version: str
    description: str = ""
    status: PluginStatus = PluginStatus.DISCOVERED


@dataclass
class PluginMetadata:
    name: str
    version: str
    description: str = ""
    entry_point: str = ""


class Plugin(ABC):
    """插件基类。所有插件必须继承此类。"""

    name: str = ""             # 唯一标识
    version: str = "0.1.0"
    description: str = ""

    def __init__(self) -> None:
        self.status = PluginStatus.DISCOVERED

    @abstractmethod
    def on_load(self) -> None:
        """加载时调用。执行初始化。"""
        ...

    @abstractmethod
    def on_unload(self) -> None:
        """卸载时调用。清理资源。"""
        ...

    def on_enable(self) -> None:
        """启用时调用。注册 Hook。"""
        self.status = PluginStatus.ENABLED

    def on_disable(self) -> None:
        """禁用时调用。注销 Hook。"""
        self.status = PluginStatus.DISABLED

    def register_hooks(self, registry: HookRegistry) -> None:
        """注册 Hook 处理器。子类重写此方法。"""
        pass
```

---

## 四、Hook 系统

### 4.1 HookPoint 枚举

```python
# plugin/hooks.py

from enum import Enum


class HookPoint(Enum):
    ON_RUNTIME_START = "on_runtime_start"       # Runtime.run() 开始
    ON_RUNTIME_STOP = "on_runtime_stop"         # Runtime.run() 结束
    ON_AGENT_INIT = "on_agent_init"             # Agent 初始化后
    ON_AGENT_STEP = "on_agent_step"             # Agent 每一步后
    ON_AGENT_COMPLETE = "on_agent_complete"     # Agent 完成
    ON_AGENT_ERROR = "on_agent_error"           # Agent 出错
    ON_MEMORY_READ = "on_memory_read"           # Memory 读取时
    ON_MEMORY_WRITE = "on_memory_write"         # Memory 写入时
    ON_TOOL_EXECUTE = "on_tool_execute"         # Tool 执行前
    ON_TOOL_RESULT = "on_tool_result"           # Tool 结果返回
```

### 4.2 Hook 触发器上下文

| HookPoint | context 参数 | 预期返回值 |
|-----------|-------------|-----------|
| `ON_RUNTIME_START` | `config`, `agent_id`, `task` | 无 |
| `ON_RUNTIME_STOP` | `agent_id`, `result` | 无 |
| `ON_AGENT_INIT` | `agent_id`, `context` | 无 |
| `ON_AGENT_STEP` | `agent_id`, `step_count`, `messages` | `{"block": False}` 可阻断 |
| `ON_AGENT_COMPLETE` | `agent_id`, `result` | 无 |
| `ON_AGENT_ERROR` | `agent_id`, `error` | 无 |
| `ON_MEMORY_READ` | `agent_id`, `key`, `namespace` | `{"value": Any}` 可覆盖 |
| `ON_MEMORY_WRITE` | `agent_id`, `key`, `value`, `namespace` | `{"block": False}` 可阻断 |
| `ON_TOOL_EXECUTE` | `agent_id`, `tool_name`, `params` | `{"block": False}` 可阻断 |
| `ON_TOOL_RESULT` | `agent_id`, `tool_name`, `result` | 无 |

### 4.3 HookRegistry

```python
class HookRegistry:
    """Hook 注册表。管理 Hook 点与处理器之间的映射。"""

    def register(
        self,
        hook_point: HookPoint,
        handler: Callable[..., Any],
        priority: int = 100,
        plugin_name: str = "",
    ) -> None: ...

    def unregister_all(self, plugin_name: str) -> None:
        """卸载插件时注销其所有 Hook。"""
        ...

    def trigger(
        self,
        hook_point: HookPoint,
        **context: Any,
    ) -> list[HookResult]:
        """按 priority 顺序执行所有处理器。"""
        ...
```

**触发规则**：
- 按 `priority` 升序执行（0 = 最高优先级）
- 前一个 Handler 的返回值作为 context 传给下一个
- Handler 异常被捕获，记录到 `HookResult`，不影响后续 Handler
- 任何 Handler 返回 `{"block": True}` 时中断后续执行

---

## 五、PluginManager

```python
# plugin/manager.py

class PluginManager:
    """插件管理器。负责插件的发现、加载、启用、禁用、卸载。"""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._hooks = HookRegistry()

    @property
    def hook_registry(self) -> HookRegistry:
        return self._hooks

    def discover(self, paths: list[str] | None = None) -> list[PluginInfo]:
        """通过 entry_points 发现已安装的插件。"""
        ...

    def load(self, name: str) -> Plugin:
        """加载插件（调用 on_load）。"""
        ...

    def unload(self, name: str) -> None:
        """卸载插件（调用 on_unload，注销 Hook）。"""
        ...

    def enable(self, name: str) -> None:
        """启用插件（调用 on_enable + register_hooks）。"""
        ...

    def disable(self, name: str) -> None:
        """禁用插件（调用 on_disable + unregister_all）。"""
        ...

    def list(self) -> list[PluginInfo]:
        """列出所有插件及其状态。"""
        ...

    def get(self, name: str) -> Plugin | None:
        """获取插件实例。"""
        ...

    def trigger(self, hook_point: HookPoint, **context) -> list[HookResult]:
        """触发 Hook。快捷方式。"""
        ...
```

### 5.1 生命周期

```
DISCOVERED ──load()──▶ LOADED ──enable()──▶ ENABLED
                     │                       │
                     │                       ├──disable()──▶ DISABLED──enable()──▶ ENABLED
                     │                       └──unload()──▶ (removed)
                     │
                     └── (load 失败) ──▶ FAILED
```

### 5.2 发现机制

使用 Python `importlib.metadata.entry_points`：

```toml
# 插件的 pyproject.toml
[project.entry-points."zmai.plugins"]
my_plugin = "my_plugin:MyPlugin"
```

```python
# PluginManager.discover()
for ep in entry_points(group="zmai.plugins"):
    try:
        cls = ep.load()
        plugin = cls()
        self._plugins[ep.name] = plugin
    except Exception as e:
        logger.error("插件加载失败: %s: %s", ep.name, e)
```

### 5.3 配置

```json
// zmai.json
{
    "plugin": {
        "enabled": ["my_plugin"],
        "disabled": [],
        "paths": ["./custom_plugins"]
    }
}
```

---

## 六、Runtime 集成

```python
# runtime.py — 新增

class Runtime:
    def __init__(self, config):
        ...
        self._plugins = PluginManager()
        self._plugins.discover()

    async def run(self, agent_id, task, ...):
        self._plugins.trigger(HookPoint.ON_RUNTIME_START,
                              config=config, agent_id=agent_id, task=task)
        try:
            ...
            self._plugins.trigger(HookPoint.ON_AGENT_INIT,
                                  agent_id=agent_id, context=ctx)
            while step_count < max_steps:
                action = await agent.step(ctx)
                self._plugins.trigger(HookPoint.ON_AGENT_STEP,
                                      agent_id=agent_id, step_count=step_count)
                ...
            self._plugins.trigger(HookPoint.ON_AGENT_COMPLETE,
                                  agent_id=agent_id, result=result)
        except Exception as e:
            self._plugins.trigger(HookPoint.ON_AGENT_ERROR,
                                  agent_id=agent_id, error=str(e))
        finally:
            self._plugins.trigger(HookPoint.ON_RUNTIME_STOP,
                                  agent_id=agent_id, result=result)
```

---

## 七、CLI 子命令

```python
# cli/commands/plugin.py（或被 cli/main.py 内联）

def _run_plugin(argv: list[str]) -> None:
    """zmai plugin <list|install|uninstall|enable|disable> [name]"""
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        for info in runtime.plugin_manager.list():
            print(f"  {info.name}  {info.version}  {info.status.value}")
    elif cmd == "enable":
        runtime.plugin_manager.enable(argv[1])
    elif cmd == "disable":
        runtime.plugin_manager.disable(argv[1])
```

---

## 八、示例：一个完整的 Plugin

```python
# example_plugin.py
from zmai.plugin import Plugin, HookPoint


class LogPlugin(Plugin):
    name = "log_plugin"
    version = "0.1.0"
    description = "记录所有 Agent 步骤到文件"

    def on_load(self) -> None:
        self.log_file = open("agent_log.txt", "a")

    def on_unload(self) -> None:
        self.log_file.close()

    def register_hooks(self, registry) -> None:
        registry.register(HookPoint.ON_AGENT_STEP, self.log_step, priority=50)

    def log_step(self, agent_id: str, step_count: int, **kw) -> None:
        self.log_file.write(f"[{agent_id}] step {step_count}\n")
        self.log_file.flush()
```

```toml
# pyproject.toml（插件项目）
[project.entry-points."zmai.plugins"]
log_plugin = "example_plugin:LogPlugin"
```

---

## 九、隔离规则

| 规则 | 说明 |
|------|------|
| **不阻塞** | Hook handler 必须快速返回，不可长时间阻塞 |
| **不抛异常** | Hook handler 异常被 PluginManager 捕获，不影响 Runtime |
| **不修改 Runtime 状态** | Hook 通过 context 读数据，不直接修改 Runtime 内部状态 |
| **优先级有序** | 同一 HookPoint 的 handler 按 priority 升序执行 |

---

## 十、与 Backend 注册的关系

当前 `BACKEND_METADATA` 手动注册模式可作为第一个 "内置 Plugin"：

```python
class BackendDiscoveryPlugin(Plugin):
    """自动发现并注册 Backend（当前手动注册逻辑的插件化封装）。"""
    name = "_builtin_backends"

    def on_load(self) -> None:
        from zmai.gateway.backends import BACKEND_METADATA
        for name, info in BACKEND_METADATA.items():
            mod = importlib.import_module(info["module"])
            cls = getattr(mod, info["class"])
            self._gateway.register(name, cls)
```

---

## 十一、文件清单

| 文件 | 行数估计 | 内容 |
|------|---------|------|
| `src/zmai/plugin/__init__.py` | 5 | 导出 Plugin, PluginManager, HookPoint, HookRegistry |
| `src/zmai/plugin/base.py` | 80 | Plugin ABC, PluginStatus, PluginInfo, PluginMetadata |
| `src/zmai/plugin/hooks.py` | 120 | HookPoint enum, HookRegistry, HookResult |
| `src/zmai/plugin/manager.py` | 150 | PluginManager（发现/加载/启用/禁用/卸载） |
| `tests/test_plugin.py` | 200+ | 测试 |
| **总计** | **~350** | |

---

## 十二、实施路线图

| Phase | 内容 | 依赖 |
|-------|------|------|
| **Phase 1** | `PluginError` ✅ **已完成** | 无 |
| **Phase 2** | `plugin/base.py` + `plugin/hooks.py` | errors |
| **Phase 3** | `plugin/manager.py` | base + hooks |
| **Phase 4** | Runtime 集成（trigger 调用） | manager |
| **Phase 5** | CLI `zmai plugin` 子命令 | manager + runtime |
| **Phase 6** | 示例 Plugin + 测试 | 全部 |

---

*Design by `claude` — 基于 ../ARCHITECTURE.md §8、../SPECIFICATION.md §5、../CLASS.md §6、API.md §6、MODULES.md §8 综合设计*
