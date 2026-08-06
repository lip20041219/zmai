# Auth Debug — 启动时认证诊断

## 概述

Auth Debug 是 ZMAI Runtime 启动时自动打印的诊断信息，帮助用户快速确认 API 凭证加载状态，而无需查看或暴露真实的 API Key。

## 触发时机

每次通过 `zmai` CLI 启动 Runtime 时（包括 REPL 模式和单次任务模式），在启动横幅之后自动打印。JSON 输出模式（`--json`）下不显示。

## 输出格式

```
  Auth Debug ──────────────────────────────────────────────────
    Current Backend:  DeepSeek
    Credentials File: C:\Users\xxx\.zmai\credentials
    Credentials:      Loaded
    Environment:      Missing
    Final API Key:    Loaded
```

所有行均使用终端 dim 样式（`\033[2m`）渲染。

## 字段说明

### Current Backend

当前选中的 LLM Backend 名称（如 `DeepSeek`、`Claude (Anthropic)`、`Gemini (Google)`）。

**判定逻辑：**
1. `PluginRegistry.default_name`（由 config `gateway.default_backend`、首个有环境变量凭据的 Backend、或第一个已注册的 Backend 依次决定）
2. 回退到 `AuthStore.get_active_backend()`
3. 均无结果则跳过整段输出

### Credentials File

凭据加密文件在磁盘上的绝对路径，默认位置：
- `~/.zmai/credentials`（Unix）
- `%USERPROFILE%\.zmai\credentials`（Windows）

该文件由 `AuthStore` 使用机器级密钥（XOR + base64）加密存储。

### Credentials

从加密凭据 **文件** 中是否成功加载了当前 Backend 的 API Key。

| 状态 | 含义 |
|------|------|
| `Loaded` | 凭据文件存在且包含当前 Backend 的 API Key |
| `Missing` | 凭据文件不存在、不包含当前 Backend，或读取失败 |

**判定逻辑：** `AuthStore().get_backend(name)` 返回了非空 `api_key`。

### Environment

从 **环境变量** 中是否检测到了当前 Backend 的 API Key。

| 状态 | 含义 |
|------|------|
| `Loaded` | 对应的环境变量已设置且非空 |
| `Missing` | 环境变量未设置或为空 |

**环境变量名称对照：**

| Backend | 环境变量 |
|---------|----------|
| Claude  | `ANTHROPIC_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Gemini  | `GEMINI_API_KEY` |

### Final API Key

综合判定最终能否获取到可用的 API Key（**不打印 Key 本身**）。

| 状态 | 含义 |
|------|------|
| `Loaded` | Credentials **或** Environment 至少一路径加载成功 |
| `Missing` | 两路均未检测到 API Key |

**重要安全约束：** 无论任何模式下，终端 **永远不会** 打印真实 API Key。

## 使用场景

| 场景 | 诊断方法 |
|------|----------|
| 启动后任务失败，提示 API Key 错误 | 查看 `Final API Key` 是否为 `Missing` |
| 刚配置完 Key 但依然报错 | 检查 `Credentials` 和 `Environment` 哪个是 `Loaded`，确认预期来源 |
| 环境变量已设置但 `Environment` 显示 `Missing` | 检查环境变量名是否正确（见上方对照表） |
| 不清楚当前生效的 Backend | 查看 `Current Backend` 行 |

## 实现参考

- 代码入口：`src/zmai/cli/main.py` — `_print_auth_debug()` 函数
- 凭证存储：`src/zmai/auth/store.py` — `AuthStore` 类
- Backend 元信息：`src/zmai/gateway/backends/__init__.py` — `BACKEND_METADATA`
- Gateway 注册表：`src/zmai/gateway/registry.py` — `BackendRegistry` / `src/zmai/gateway/plugin.py` — `PluginRegistry`
