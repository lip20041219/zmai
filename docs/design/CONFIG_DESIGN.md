# ZMAI Configuration Design v2.0

Version: 2.0
Date: 2026-07-16

> **全局配置 + 项目配置。** 多 Backend、多 Workspace、多 Plugin。
>
> 不修改 Runtime / Agent / Gateway / Memory / Workspace / Workflow 模块。

---

## 目录

1. [现状审查](#1-现状审查)
2. [设计原则](#2-设计原则)
3. [配置层级](#3-配置层级)
4. [全局配置](#4-全局配置)
5. [项目配置](#5-项目配置)
6. [多 Backend 配置](#6-多-backend-配置)
7. [多 Workspace 配置](#7-多-workspace-配置)
8. [多 Plugin 配置](#8-多-plugin-配置)
9. [配置合并规则](#9-配置合并规则)
10. [迁移路径](#10-迁移路径)
11. [文件清单与实现计划](#11-文件清单与实现计划)

---

## 1. 现状审查

### 1.1 当前实现

| 文件 | 功能 | 状态 |
|------|------|------|
| `config/config.py` | `Config` 类：多源合并，扁平 KV | ✅ 已有 |
| `config/sources.py` | `FileSource`(JSON) + `EnvSource` + `CLISource` | ✅ 已有 |
| `zmai.json` | 项目配置（单文件） | ✅ 存在 |

### 1.2 当前问题

| 问题 | 说明 | 影响 |
|------|------|------|
| **Config 不加载全局配置** | `~/.zmai/config.json` 由 auth 模块创建，但 `Config` 类未加载它 | 全局偏好不生效 |
| **无层级区分** | `Config()` 只加载 `zmai.json`，丢失来源信息 | 无法追溯值来自哪 |
| **无模式验证** | 任何 key 可设，拼写错误不被发现 | 排错困难 |
| **多 Backend 支持弱** | `gateway.backends` 是字典，无切换语义 | 只能在文件里改 |
| **无多 Workspace** | 单 `workspace.root` 字段 | 无法按场景切换 |
| **无 Plugin 配置** | 无 `plugins` 节 | 无法配置插件 |
| **字典整体替换** | 当前 `dict.update()` 覆盖整个子字典 | 全局和项目的 backends 无法共存 |

### 1.3 v1.0 已覆盖

| 能力 | 状态 |
|------|------|
| 全局/项目分离概念 | ✅ v1.0 已设计 |
| 配置优先级 | ✅ v1.0 已设计 |
| 内置默认值 | ✅ v1.0 已设计 |

### 1.4 v2.0 新增

| 能力 | 说明 |
|------|------|
| **多 Backend 完整语义** | named backends + 运行时切换 + 自动选择 |
| **多 Workspace 完整语义** | named workspaces + 自动发现 + 运行时切换 |
| **多 Plugin 配置** | enabled/disabled 列表 + 独立插件配置 |
| **字典深度合并** | 全局 backends + 项目 backends = 合并而非替换 |
| **配置追溯** | `get_with_source()` 返回 (value, source) |
| **模式验证** | JSON Schema + 运行时类型检查 |

---

## 2. 设计原则

### 2.1 两层级

```
全局配置   ~/.zmai/config.json        ← 用户级别（不随项目迁移）
项目配置   ./zmai.json                ← 项目级别（可提交 Git）

全局配置是基线，项目配置覆盖全局。
环境变量和 CLI 参数是运行时覆盖，不持久化。
```

### 2.2 多实例

```
配置不是单例。每个配置项支持多实例命名：

  Backend:   claude / deepseek / openai / gemini
  Workspace: dev / staging / prod / default
  Plugin:    swe / auth / mcp / custom
```

### 2.3 字典深度合并

```
对于多实例字典（backends / workspaces / plugins），
全局和项目的配置是 合并 而非 替换。

全局 backends:  { claude, deepseek }
项目 backends:  { openai, gemini  }
合并结果:       { claude, deepseek, openai, gemini }
```

### 2.4 凭证不进入配置

```
配置文件中不允许出现 api_key / token / secret 字段。

凭证 → AuthStore → OS Keychain / 加密文件
配置 → Config    → JSON 文件（仅非敏感设置）

AuthStore 和 Config 是两个独立模块。
```

### 2.5 不修改下游

```
仅修改:  src/zmai/config/     ← 重写
         ./zmai.json          ← 格式更新

不修改:  src/zmai/runtime/*    ✗
         src/zmai/gateway/*    ✗
         src/zmai/agent/*      ✗
         src/zmai/workspace/*  ✗
         src/zmai/memory/*     ✗
         src/zmai/workflow/*   ✗
```

---

## 3. 配置层级

### 3.1 五层覆盖

```
优先级 (高 → 低):

  1. CLI 参数                --theme=light
  2. 环境变量                ZMAI_CLI_THEME=light
  3. 项目配置                ./zmai.json
  4. 全局配置                ~/.zmai/config.json
  5. 内置默认值

每层只覆盖上层存在的 key。配置项取最高优先级的非空值。
null / 空字符串 / 空数组 = 未设置，不覆盖下层。
```

### 3.2 加载流程

```python
def load_config(project_root: Path | None = None) -> Config:
    config = Config()

    # 第 1 层: 内置默认值
    config.load_defaults()

    # 第 2 层: 全局配置
    global_path = Path.home() / ".zmai" / "config.json"
    if global_path.exists():
        config.load_file(global_path, source="global")

    # 第 3 层: 项目配置
    if project_root:
        project_path = project_root / "zmai.json"
        if project_path.exists():
            config.load_file(project_path, source="project")

    # 第 4 层: 环境变量
    config.load_env(prefix="ZMAI_")

    # 第 5 层: CLI 参数
    # config.load_cli(args)

    return config
```

### 3.3 配置追溯

```python
config = load_config(project_root)

# 获取值
config.get("cli.theme")
# → "light"

# 获取值 + 来源
config.get_with_source("cli.theme")
# → ("light", "project")     ← 值来自项目配置

config.get_with_source("runtime.max_iterations")
# → (100, "default")          ← 值来自内置默认值
```

### 3.4 配置导出

```python
config.export()
# {
#   "default": {"cli.theme": "dark", "runtime.max_iterations": 100},
#   "global":  {"cli.theme": "light"},
#   "project": {"gateway.default_backend": "deepseek"},
#   "env":     {},
# }
```

---

## 4. 全局配置

### 4.1 位置

```
~/.zmai/config.json
```

**自动创建：** 首次运行 `zmai` 时由初始化向导自动创建。

### 4.2 完整格式

```json
{
  "version": 2,

  "cli": {
    "theme": "dark",
    "confirm": false
  },

  "gateway": {
    "default_backend": "deepseek",
    "timeout": 300
  },

  "runtime": {
    "log_level": "INFO"
  }
}
```

### 4.3 最小格式

```json
{
  "cli": {
    "theme": "dark"
  }
}
```

### 4.4 存储内容

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `cli.theme` | `"dark" | "light" | "plain"` | `"dark"` | 终端主题 |
| `cli.confirm` | `bool` | `false` | 高风险操作是否确认 |
| `gateway.default_backend` | `string` | `"auto"` | 默认 Backend 名称 |
| `gateway.timeout` | `int` | `300` | API 请求超时（秒） |
| `runtime.log_level` | `"DEBUG" | "INFO" | "WARNING" | "ERROR"` | `"INFO"` | 日志级别 |

### 4.5 不存储

全局配置不存储：
- API Key / Token / Secret
- 项目路径
- Workspace 路径
- 插件列表

---

## 5. 项目配置

### 5.1 位置

```
<project-root>/zmai.json
```

**手动创建或由 `zmai init` 生成。可提交 Git。**

### 5.2 完整格式

```json
{
  "version": 2,

  "gateway": {
    "default_backend": "deepseek",
    "backends": {
      "claude": {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096
      },
      "deepseek": {
        "model": "deepseek-chat",
        "url": "https://api.deepseek.com/v1",
        "max_tokens": 8192
      }
    }
  },

  "workspace": {
    "default": "dev",
    "workspaces": {
      "dev": {
        "root": "./workspace",
        "max_file_size": 10485760,
        "max_files": 1000
      },
      "ci": {
        "root": "./workspace-ci",
        "max_files": 100
      }
    }
  },

  "plugins": {
    "enabled": ["swe", "auth"]
  },

  "runtime": {
    "max_iterations": 100,
    "max_concurrent_agents": 10,
    "max_conversation_rounds": 10,
    "timeout": 300
  },

  "tool": {
    "default_timeout": 60
  }
}
```

### 5.3 最小格式

```json
{
  "gateway": {
    "default_backend": "deepseek"
  }
}
```

只需指定 Backend 即可。其余全用默认值。

### 5.4 存储内容

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `gateway.default_backend` | `string` | `"auto"` | 项目默认 Backend |
| `gateway.backends.*` | `dict` | `{}` | Backend 配置（模型、URL、参数） |
| `workspace.default` | `string` | `"default"` | 默认 Workspace 名称 |
| `workspace.workspaces.*` | `dict` | `{}` | 命名 Workspace 定义 |
| `plugins.enabled` | `string[]` | `["swe"]` | 启用的插件列表 |
| `plugins.disabled` | `string[]` | `[]` | 禁用的插件列表 |
| `runtime.*` | — | — | Runtime 参数 |
| `tool.*` | — | — | 工具链参数 |

### 5.5 不存储

项目配置不存储：
- API Key / Token / Secret
- 用户偏好（theme, confirm）

---

## 6. 多 Backend 配置

### 6.1 定义

```json
{
  "gateway": {
    "default_backend": "deepseek",

    "backends": {
      "claude": {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "url": "https://api.anthropic.com/v1",
        "timeout": 300
      },
      "deepseek": {
        "model": "deepseek-chat",
        "max_tokens": 8192,
        "url": "https://api.deepseek.com/v1"
      },
      "openai": {
        "model": "gpt-4o",
        "max_tokens": 4096
      },
      "gemini": {
        "model": "gemini-2.0-flash"
      }
    }
  }
}
```

**凭证不在此存储。** API Key 通过 `AuthStore`（OS Keychain / 加密文件）提供。

### 6.2 运行时选择

```
CLI:      zmai --backend claude 分析代码
REPL:     zmai> /backend claude
Config:   gateway.default_backend = "deepseek"
```

优先级：
1. `--backend` CLI 参数（本次执行）
2. `/backend` REPL 命令（本次会话）
3. 项目配置 `gateway.default_backend`
4. 全局配置 `gateway.default_backend`
5. `"auto"` → 自动检测

### 6.3 自动选择

当 `default_backend = "auto"` 时：

```
1. 环境变量 ANTHROPIC_API_KEY → claude
2. 环境变量 DEEPSEEK_API_KEY  → deepseek
3. 环境变量 OPENAI_API_KEY    → openai
4. 环境变量 GEMINI_API_KEY    → gemini
5. 以上都没有 → 提示用户配置
```

### 6.4 字典深度合并

```python
# 全局配置
{"backends": {"claude": {...}, "deepseek": {...}}}

# 项目配置
{"backends": {"openai": {...}, "gemini": {...}}}

# 合并结果（自动合并字典键，而非替换）
{"backends": {
    "claude": {...},     # 来自全局
    "deepseek": {...},   # 来自全局
    "openai": {...},     # 来自项目
    "gemini": {...},     # 来自项目
}}
```

---

## 7. 多 Workspace 配置

### 7.1 定义

```json
{
  "workspace": {
    "default": "dev",

    "workspaces": {
      "dev": {
        "root": "./workspace",
        "max_file_size": 10485760,
        "max_files": 1000
      },
      "ci": {
        "root": "./workspace-ci",
        "max_file_size": 10485760,
        "max_files": 100
      },
      "shared": {
        "root": "/shared/team-workspace",
        "max_file_size": 10485760,
        "max_files": 500
      }
    }
  }
}
```

### 7.2 运行时选择

```
CLI:      zmai --workspace ci <task>
REPL:     zmai> /workspace ci
Config:   workspace.default = "dev"
自动:     扫描 ./workspace/ → 有 state.json? → 使用
```

**不指定时：** 使用 `workspace.default`。不要求用户配置。

### 7.3 自动发现

无配置时按优先级自动发现：

```python
def resolve_workspace(project_root: Path, config: Config) -> Path:
    """解析 Workspace 路径。不要求用户配置。"""

    # 1. 显式指定的 workspace 名称
    ws_name = config.get("workspace.default", "default")
    ws_config = config.get(f"workspace.workspaces.{ws_name}", {})
    if ws_config and "root" in ws_config:
        return Path(ws_config["root"])

    # 2. 自动扫描候选目录
    for candidate in ["./workspace", "./.zmai/workspace", "./agent_workspace"]:
        path = project_root / candidate
        if path.exists() and (path / "state.json").exists():
            return path

    # 3. 默认
    return project_root / "workspace"
```

---

## 8. 多 Plugin 配置

### 8.1 定义

```json
{
  "plugins": {
    "enabled": ["swe", "auth", "mcp"],

    "plugins": {
      "swe": {
        "max_steps": 100,
        "tools": ["read_file", "write_file", "shell_exec"]
      },
      "auth": {},
      "mcp": {
        "servers": {
          "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
          }
        }
      }
    }
  }
}
```

### 8.2 启用/禁用

```json
{
  "plugins": {
    "enabled": ["swe", "auth"],
    "disabled": ["experimental-plugin"]
  }
}
```

- `enabled` 列表中的插件被加载
- `disabled` 列表中的插件被忽略
- 都未配置时加载所有已安装插件

### 8.3 插件配置合并

```python
# 全局配置
config.load_file(global_path, source="global")
# plugins.swe.max_steps = 100

# 项目配置
config.load_file(project_path, source="project")
# plugins.swe.max_steps = 200  ← 覆盖全局

# 结果: swe.max_steps = 200
```

---

## 9. 配置合并规则

### 9.1 Config 类

```python
@dataclass
class ConfigLayer:
    source: str             # "default" | "global" | "project" | "env" | "cli"
    data: dict[str, Any]    # 扁平键值对
    priority: int


class Config:
    """分层配置管理器。"""

    def __init__(self):
        self._layers: list[ConfigLayer] = []
        self._cache: dict[str, Any] = {}

    def load_defaults(self) -> None:
        self._layers.append(ConfigLayer(
            source="default",
            data=_flatten(_DEFAULTS),
            priority=0,
        ))

    def load_file(self, path: Path, source: str) -> None:
        if not path.exists():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        flat = _flatten(raw)
        self._layers.append(ConfigLayer(
            source=source,
            data=flat,
            priority=len(self._layers),
        ))
        self._cache = {}

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._cache:
            return self._cache[key]
        for layer in reversed(self._layers):
            if key in layer.data:
                self._cache[key] = layer.data[key]
                return layer.data[key]
        return default

    def get_with_source(self, key: str) -> tuple[Any, str]:
        """获取值及其来源。"""
        for layer in reversed(self._layers):
            if key in layer.data:
                return layer.data[key], layer.source
        return None, "default"

    def get_dict(self, prefix: str) -> dict[str, Any]:
        """获取某个前缀下的所有配置（用于多实例字典合并）。"""
        result = {}
        # 从低到高遍历，高层覆盖低层
        for layer in self._layers:
            for key, val in layer.data.items():
                if key.startswith(prefix + "."):
                    # 提取相对路径
                    rel_key = key[len(prefix) + 1:]
                    # 深度设置
                    parts = rel_key.split(".")
                    d = result
                    for p in parts[:-1]:
                        d = d.setdefault(p, {})
                    d[parts[-1]] = val
        return result
```

### 9.2 内置默认值

```python
_DEFAULTS = {
    "version": 2,
    "cli.theme": "dark",
    "cli.confirm": False,
    "gateway.default_backend": "auto",
    "gateway.timeout": 300,
    "workspace.default": "default",
    "workspace.max_file_size": 10485760,
    "workspace.max_files": 1000,
    "plugins.enabled": ["swe"],
    "runtime.max_iterations": 100,
    "runtime.max_concurrent_agents": 10,
    "runtime.max_conversation_rounds": 10,
    "runtime.timeout": 300,
    "runtime.log_level": "INFO",
    "tool.default_timeout": 60,
}
```

### 9.3 合并示例

```
内置默认:
  gateway.default_backend = "auto"
  cli.theme = "dark"

全局配置:
  cli.theme = "light"

项目配置:
  gateway.default_backend = "deepseek"

结果:
  gateway.default_backend = "deepseek"    ← 来自项目
  cli.theme = "light"                     ← 来自全局
```

### 9.4 配置验证

```python
_CONFIG_SCHEMA = {
    "cli.theme": {"type": str, "enum": ["dark", "light", "plain"]},
    "cli.confirm": {"type": bool},
    "gateway.default_backend": {"type": str},
    "gateway.timeout": {"type": int, "min": 1},
    "runtime.max_iterations": {"type": int, "min": 1},
    "runtime.log_level": {"type": str, "enum": ["DEBUG", "INFO", "WARNING", "ERROR"]},
}


def validate_config(config: Config) -> list[str]:
    """验证配置值是否符合模式。"""
    errors = []
    for key, schema in _CONFIG_SCHEMA.items():
        val, source = config.get_with_source(key)
        if val is None:
            continue
        expected_type = schema.get("type")
        if expected_type and not isinstance(val, expected_type):
            errors.append(
                f"{key}: 应为 {expected_type.__name__}, "
                f"实际为 {type(val).__name__} (来源: {source})"
            )
        enum_vals = schema.get("enum")
        if enum_vals and val not in enum_vals:
            errors.append(
                f"{key}: 值 '{val}' 不在可选范围内 {enum_vals} (来源: {source})"
            )
    return errors
```

---

## 10. 迁移路径

### 10.1 v1 → v2 变更

```
v1 (当前)                          v2 (目标)
──────────────────────────────────────────────────
zmai.json                         zmai.json
  ├── gateway.backends.*.api_key    ├── 无 api_key（已移至 AuthStore）
  ├── gateway.backends.*.model      ├── gateway.backends.*.model
  ├── workspace.root                ├── workspace.workspaces.*.root
  └── 无 plugins                    └── plugins.enabled

~/.zmai/config.json（可能不存在）   ~/.zmai/config.json（首次运行自动创建）
                                    ├── cli.theme
                                    └── gateway.default_backend
```

### 10.2 自动迁移

```python
def migrate_v1_to_v2(project_root: Path) -> None:
    """将 v1 项目配置迁移到 v2 格式。"""
    path = project_root / "zmai.json"
    if not path.exists():
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") == 2:
        return  # 已是最新

    changed = False

    # 迁移 Backend 配置中的 api_key
    backends = data.get("gateway", {}).get("backends", {})
    for name, bc in backends.items():
        if "api_key" in bc:
            from zmai.auth import AuthStore
            AuthStore().set_backend(name, bc.pop("api_key"),
                                     model=bc.get("model", ""),
                                     make_active=False)
            changed = True

    # 迁移 workspace.root → workspace.workspaces.default.root
    ws_root = data.get("workspace", {}).get("root")
    if ws_root:
        ws = data.setdefault("workspace", {})
        ws.setdefault("workspaces", {})
        if "default" not in ws["workspaces"]:
            ws["workspaces"]["default"] = {"root": ws_root}
            ws["workspaces"]["default"]["max_file_size"] = ws.get("max_file_size", 10485760)
            ws["workspaces"]["default"]["max_files"] = ws.get("max_files", 1000)
            del ws["root"]
            changed = True

    # 写回
    if changed:
        data["version"] = 2
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
```

---

## 11. 文件清单与实现计划

### 11.1 修改文件

```
src/zmai/config/
├── __init__.py             # 🔧 更新导出
└── config.py               # 🔧 重写 — 分层 Config + get_with_source + get_dict
```

### 11.2 不变文件

```
src/zmai/config/sources.py   ✅ 不变（FileSource/EnvSource/CLISource）
src/zmai/runtime/*           ✅
src/zmai/gateway/*           ✅
src/zmai/agent/*             ✅
src/zmai/workspace/*         ✅
src/zmai/memory/*            ✅
src/zmai/workflow/*          ✅
src/zmai/cli/*               ✅
src/zmai/auth/*              ✅
```

### 11.3 代码量变化

```
修改:
  config/config.py       ~80 行（当前）→ ~150 行（分层 + 追溯 + 字典合并）
  净增                   ~70 行
```

### 11.4 配置项迁移对照

| 旧路径 | 新路径 | 说明 |
|--------|--------|------|
| — | `cli.theme` | 全局偏好 |
| — | `cli.confirm` | 全局偏好 |
| `gateway.default_backend` | `gateway.default_backend` | 不变 |
| `gateway.backends.*` | `gateway.backends.*` | 格式不变，去掉 api_key |
| `workspace.root` | `workspace.workspaces.*.root` | 多实例命名 |
| `workspace.max_file_size` | `workspace.workspaces.*.max_file_size` | 按 workspace 独立 |
| — | `plugins.enabled` | 新增 |
| — | `plugins.disabled` | 新增 |
| `runtime.*` | `runtime.*` | 不变 |
| `tool.*` | `tool.*` | 不变 |

### 11.5 实现优先级

```
P0 — 分层 Config（1 天）
├── Config 重写 — 五层模型 + ConfigLayer
├── load_defaults() — 内置默认值
├── load_file() — 带 source 标记
├── get_with_source() — 值追溯
└── 集成到 main.py — 启动时加载全局 + 项目

P1 — 多实例（1 天）
├── get_dict() — 字典深度合并
├── backends 多实例支持
├── workspaces 多实例支持
├── plugins 配置
└── 配置验证

P2 — 迁移（0.5 天）
├── v1 → v2 自动迁移脚本
├── 向后兼容旧格式
├── 类型验证
└── /config 命令集成
```

---

> **总结：**
>
> ZMAI Configuration v2.0 的关键变化：
>
> **从 1 层到 5 层：**
> ```
> CLI 参数                  (最高)
> 环境变量 ZMAI_*
> 项目配置 ./zmai.json
> 全局配置 ~/.zmai/config.json
> 内置默认值                (最低)
> ```
>
> **从单实例到多实例：**
> - **多 Backend** — 命名配置，`/backend` 运行时切换，自动选择
> - **多 Workspace** — 命名配置，自动发现，/workspace 切换
> - **多 Plugin** — 命名配置，enabled/disabled 列表
>
> **从替换到合并：**
> - 字典类型（backends/workspaces/plugins）深度合并
> - 全局 + 项目配置自动合并，不互相覆盖
>
> **新增能力：**
> - `get_with_source()` — 追溯配置来源
> - 类型验证 + 枚举验证
> - v1 → v2 自动迁移
>
> **最小配置：**
> ```json
> {"gateway": {"default_backend": "deepseek"}}
> ```
