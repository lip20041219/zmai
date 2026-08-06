# Config File Paths — 完整路径与优先级

**Date**: 2026-07-17  
**Goal**: 列出所有配置文件真实路径，说明 Runtime 读取顺序

---

## 1. 所有配置文件路径

| # | 名称 | 真实路径 | 类型 | 写入者 | 读取者 |
|---|---|---|---|---|---|
| 1 | Project Config | `{CWD}/zmai.json` | JSON | 开发者 | `FileSource` → `Config` |
| 2 | Global Config | `~/.zmai/config.json` | JSON | `_first_run_wizard()` | `FileSource` → `Config` |
| 3 | Credentials | `~/.zmai/credentials` | 加密 (XOR+base64) | `AuthStore.set_backend()` | `AuthStore._load()` |
| 4 | Plugin Backends | `~/.zmai/backends/*.py` | Python | `zmai plugin install` | `discover_plugins()` |
| 5 | Project Plugins | `{CWD}/.zmai/backends/*.py` | Python | 开发者 | `discover_plugins()` |
| 6 | Workspace | `{CWD}/workspace/` | 目录 | `Workspace.prepare()` | `Workspace` |
| 7 | Memory | `~/.zmai/memory/` | 目录 | `MemoryManager` | `MemoryManager` |
| 8 | Sessions | `~/.zmai/sessions/` | JSON | `_save_session()` | `_load_latest_session()` |
| 9 | History | `~/.zmai/history` | 文本 | `readline` | `readline` |
| 10 | Env Vars | 进程环境 | `ZMAI_*` 前缀 | 用户 / `_inject_auth_credentials()` | `EnvSource` → `Config` |

### 1a. 路径解析（以当前用户为例）

```
~/.zmai = C:\Users\MECHREVO\.zmai
CWD     = D:\desk\ZMAI
```

| 配置项 | 解析后路径 |
|---|---|
| Global Config | `C:\Users\MECHREVO\.zmai\config.json` |
| Credentials | `C:\Users\MECHREVO\.zmai\credentials` |
| Plugin Backends | `C:\Users\MECHREVO\.zmai\backends/` |
| Project Plugins | `D:\desk\ZMAI\.zmai\backends/` |
| Memory | `C:\Users\MECHREVO\.zmai\memory/` |
| Sessions | `C:\Users\MECHREVO\.zmai\sessions/` |
| History | `C:\Users\MECHREVO\.zmai\history` |
| Project Config | `D:\desk\ZMAI\zmai.json` |
| Workspace | `D:\desk\ZMAI\workspace/` |

---

## 2. Config 加载链

### 2a. Config 类的 Source 顺序

```python
# config/config.py:30-35
self._sources = [
    FileSource("zmai.json"),                                    # ① 项目配置
    FileSource(str(Path.home() / ".zmai" / "config.json")),     # ② 全局配置
    EnvSource(),                                                # ③ 环境变量
    CLISource(),                                                # ④ CLI 参数
]
```

后加载的 source **覆盖**先加载的。所以优先级：

```
低  ←  ① zmai.json（项目）
        ② ~/.zmai/config.json（全局）
        ③ ZMAI_* 环境变量
高  →  ④ CLI --key=value
```

### 2b. main() 中的显式构造

```python
# cli/main.py:771-778
config = Config(
    sources=[
        FileSource(str(root / "zmai.json")),    # ① 项目配置
        FileSource(global_cfg),                  # ② 全局配置
        EnvSource(),                             # ③ 环境变量
        CLISource(),                             # ④ CLI 参数
    ]
)
```

与默认顺序一致。

---

## 3. Runtime 实际读取路径图

```
Runtime.__init__(config)
  │
  ├── Runtime 配置
  │     └── self._config = Config 实例
  │           ├── zmai.json          ← 项目级
  │           ├── ~/.zmai/config.json ← 全局级
  │           ├── ZMAI_* env vars     ← 环境变量
  │           └── CLI --key=val       ← CLI 参数
  │
  ├── PluginRegistry(config)
  │     ├── _discover()
  │     │     ├── BACKEND_METADATA   ← 代码内置
  │     │     ├── ~/.zmai/backends/  ← 用户插件目录
  │     │     └── {CWD}/.zmai/backends/ ← 项目插件目录
  │     │
  │     ├── _build_config(name)
  │     │     ├── plugin defaults     ← 代码默认值
  │     │     ├── AuthStore           ← ~/.zmai/credentials（加密）
  │     │     ├── Config backends.*   ← zmai.json + ~/.zmai/config.json
  │     │     └── env overrides       ← {NAME}_API_KEY 等
  │     │
  │     └── _auto_select_default()
  │           ├── Config gateway.default_backend ← 用户配置
  │           ├── env var 检测                   ← 环境变量
  │           └── 第一个已注册 Backend            ← 兜底
  │
  ├── Workspace(root)
  │     └── config.get("workspace.root", "./workspace")
  │           └── {CWD}/workspace/
  │
  ├── MemoryManager()
  │     └── Path.home() / ".zmai" / "memory"
  │           └── C:\Users\MECHREVO\.zmai\memory/
  │
  └── AuthStore（通过 PluginRegistry 间接调用）
        └── Path.home() / ".zmai" / "credentials"
              └── C:\Users\MECHREVO\.zmai\credentials
```

---

## 4. 数据流向

### Backend 配置的完整路径

```
用户输入 API Key
  │
  ▼
_first_run_wizard()
  │
  ├── AuthStore.set_backend("deepseek", key)
  │     └── → 加密写入 ~/.zmai/credentials
  │           └── backends.deepseek.api_key = "sk-xxx..."
  │
  ├── os.environ["DEEPSEEK_API_KEY"] = key   ← 当前进程
  │
  └── 写入 ~/.zmai/config.json
        └── gateway.default_backend = "deepseek"
        └── backends.deepseek.model = "deepseek-chat"
              ...
  │
  ▼
_inject_auth_credentials()    ← 每次启动执行
  │
  ├── AuthStore.get_backend("deepseek")
  │     └── ~/.zmai/credentials → 解密 → {"api_key": "sk-xxx..."}
  │
  └── os.environ["DEEPSEEK_API_KEY"] = "sk-xxx..."
  │
  ▼
PluginRegistry._build_config("deepseek")
  │
  ├── ① plugin defaults           → 无 api_key
  ├── ② AuthStore.get_backend()   → api_key from ~/.zmai/credentials
  ├── ③ Config backends.deepseek   → model/base_url from ~/.zmai/config.json
  └── ④ env DEEPSEEK_API_KEY      → api_key from env (set by step above)
  │
  ▼
DeepSeekBackend(config)
  └── config.get("api_key")        → api_key = "sk-xxx..."
        └── invoke() → API 调用成功
```

---

## 5. 关键结论

| 问题 | 结论 |
|---|---|
| Project Config 路径 | `{CWD}/zmai.json` |
| Global Config 路径 | `~/.zmai/config.json` |
| Credentials 路径 | `~/.zmai/credentials`（加密） |
| Runtime 优先读哪个？ | zmai.json → global config → env → CLI |
| API Key 最终从哪里来？ | `AuthStore` 从 `~/.zmai/credentials` 解密 → `_build_config()` → `DeepSeekBackend.__init__()` |
| 两个 config.json 谁覆盖谁？ | `~/.zmai/config.json`（全局）覆盖 `zmai.json`（项目） |
| Config 和 Credentials 的关系？ | 独立路径。Config 不加载 credentials。AuthStore 管理 credentials，PluginRegistry 同时查询两者 |
