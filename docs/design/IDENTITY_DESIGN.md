# ZMAI Agent Identity Design

## Current State (已修复)

Identity 数据流已经全部动态化，没有硬编码。

### 数据流

```
Runtime.run()
  → self._gateway.get(backend)      # 获取 Backend 实例（DeepSeekBackend / ClaudeBackend）
  → AgentContext(backend=backend_inst, ...)  # 注入到 AgentContext
  → agent.step(ctx)
    → _build_system_prompt(backend=context.backend)
      → backend.name     = "deepseek"      ← 动态
      → backend.model    = "deepseek-chat"  ← 动态
      → backend.provider = "deepseek"       ← 动态
      → 生成的 System Prompt:
          ## 你的身份
          你运行在 DEEPSEEK Backend 上。
          当前模型: deepseek-chat。
```

### Backend 公共属性

| 属性 | 类型 | 来源 | 示例 |
|------|------|------|------|
| `backend.name` | class attr | 子类定义 | `"claude"` / `"deepseek"` |
| `backend.model` | property | `self._model` | `"claude-sonnet-4-6"` / `"deepseek-chat"` |
| `backend.provider` | class attr | 子类定义 | `"anthropic"` / `"deepseek"` |

### 验证：无硬编码

```
grep -rn "我是\|I am\|当前模型\|你运行在" src/zmai/swe/ --include="*.py"
↓
src/zmai/swe/agent.py:143: f"你运行在 {bp.upper() if bp else bn} Backend 上。"
src/zmai/swe/agent.py:145: f"当前模型: {bm}。"
```

两处 identity 输出均从 Backend 实例动态读取。`"你是一个软件工程 Agent (SWE Agent)"` 描述的是角色（role），不是模型身份（identity）。

### System Prompt 最终结构

```
## 你的身份                          ← 动态，来自 backend.{name,model,provider}
你运行在 DEEPSEEK Backend 上。
当前模型: deepseek-chat。

你是一个软件工程 Agent (SWE Agent)...  ← 角色描述，不涉及模型
```

切换 Backend 后 identity 自动变化，无需修改代码。
