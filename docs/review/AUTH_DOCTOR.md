# `zmai auth doctor` — Backend 状态诊断

**Date**: 2026-07-17  
**Status**: Implemented

---

## 1. 输出格式

```
  Claude       ✗  Missing
  DeepSeek     ✓  Configured
  Gemini       ✗  Missing
  OpenAI       ✗  Missing (plugin)
```

每行一个 Backend，显示：

| 列 | 说明 |
|---|---|
| 名称 | Backend 标签（含 Plugin 标记） |
| 图标 | `✓` 已配置 / `✗` 缺失 |
| 状态 | `Configured` / `Missing` |

---

## 2. 检查逻辑

对每个已注册的 Backend（通过 PluginRegistry 发现）：

1. 检查 `{NAME}_API_KEY` 环境变量
2. 如果不存在，检查 AuthStore 凭据文件
3. 如果存在 → `✓ Configured`
4. 如果不存在 → `✗ Missing`

不发送 HTTP 请求，不调用 LLM API。

---

## 3. 实现

在 `_run_auth()` 中新增 `doctor` 子命令：

```python
elif sub == "doctor":
    """显示所有 Backend 的配置状态。"""
    from zmai.gateway.plugin import PluginRegistry
    import os

    reg = PluginRegistry()
    theme = Theme.dark()

    for plugin in reg.list_plugins():
        # 检查 API Key
        api_key = os.environ.get(plugin.env_api_key, "")
        if not api_key:
            try:
                from zmai.auth import AuthStore
                auth = AuthStore().get_backend(plugin.name)
                if auth and auth.get("api_key"):
                    api_key = auth["api_key"]
            except Exception:
                pass

        if api_key:
            icon = "✓"
            status = "Configured"
        else:
            icon = "✗"
            status = "Missing"

        suffix = ""
        if not plugin.builtin:
            suffix = " (plugin)"

        print(f"  {plugin.label:20s} {icon}  {status}{suffix}")
```

---

## 4. 输出示例

### 全部未配置

```
  Claude (Anthropic)    ✗  Missing
  DeepSeek              ✗  Missing
  Gemini (Google)       ✗  Missing
```

### 部分配置

```
  Claude (Anthropic)    ✓  Configured
  DeepSeek              ✓  Configured
  Gemini (Google)       ✗  Missing
```

### 含插件

```
  Claude (Anthropic)    ✓  Configured
  DeepSeek              ✓  Configured
  Gemini (Google)       ✗  Missing
  OpenAI                ✗  Missing (plugin)
```

---

## 5. 文件修改

| 文件 | 修改 |
|---|---|
| `src/zmai/cli/main.py` | `_run_auth()` 中新增 `doctor` 子命令 |
