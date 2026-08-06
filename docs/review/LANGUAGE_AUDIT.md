# 语言一致性审计

> 审计日期: 2026-07-26
> 来源文件: `src/zmai/` 下 78 个 Python 文件
> 审计范围: module docstring、class/function docstring、inline comment、error message、CLI help、system prompt

---

## 1. 总体分布

| 语言 | 文件数 | 占比 |
|------|--------|------|
| 纯中文 docstring | 73 | 93.6% |
| 中英混合 docstring | 5 | 6.4% |
| 纯英文 docstring | 0 | 0% |

**项目约定**: 项目初始即使用中文作为开发者文档语言（中国的开发者团队）。

---

## 2. 逐项分析

### 2.1 Module Docstrings

| 语言 | 数量 | 代表文件 |
|------|------|----------|
| 中文 | 78/78 | 全部文件 |
| 英文 | 0 | — |

**一致**: ✅ 全部统一为中文。

### 2.2 Class Docstrings

| 语言 | 数量 | 代表文件 |
|------|------|----------|
| 中文 | ~140 | 全部 |
| 英文 | 2 | `agent/base.py:AgentAction`, `tool/base.py:ToolContext` |

**不一致** 🔴:

- `agent/base.py`: `AgentAction` 类的 `cont()`/`pause()`/`complete()`/`fail()` 方法注解为英文
- `tool/base.py`: `ToolContext` 的 attribute 文档为英文

```python
# agent/base.py — 英文
@classmethod
def cont(cls, output: str = "", metadata: dict | None = None) -> AgentAction:
    ...

# tool/base.py — 英文
@dataclass(frozen=True)
class ToolContext:
    """工具执行上下文。"""  # ← 类 docstring 是中文
    agent_id: str  # 英文注释
    ...
```

### 2.3 Function Docstrings

| 语言 | 数量 | 占比 |
|------|------|------|
| 中文 | ~280 | 97% |
| 英文 | ~10 | 3% |

**主要英文实例**:

| 文件 | 函数 | 语言 | 说明 |
|------|------|------|------|
| `gateway/errors.py` | `validate_backend_response` | 英文 | 内部验证函数 |
| `swe/verifier.py` | `verify_file_exists` | 英文 | 内部验证 |
| `swe/verifier.py` | `verify_file_content` | 英文 | 内部验证 |
| `swe/verifier.py` | `verify_exit_code` | 英文 | 内部验证 |
| `swe/verifier.py` | `verify_test_output` | 英文 | 内部验证 |
| `swe/verifier.py` | `verify_git_diff` | 英文 | 内部验证 |
| `swe/verifier.py` | `auto_generate_checks` | 英文 | 内部验证 |
| `prompt/templates.py` | 全部模板变量 | 英文 | Jinja 模板语法 |
| `execution/log.py` | 全部函数 | 中文 | 统一中文 |

**verifier.py 是唯一大规模使用英文 docstring 的模块** — 5 个 verify 函数全是英文，与项目整体风格不一致。

### 2.4 Inline Comments

| 语言 | 占比 | 说明 |
|------|------|------|
| 中文 | ~70% | 大部分解释性注释 |
| 英文 | ~30% | 代码标记（TODO, FIXME, NOTE），短注释，参数说明 |

**代表性的中文注释**:
```python
# swe/tools.py
# 绝对路径检测（使用 pathlib，避免手动字符串解析）
# 相对路径：优先从 project_path 解析（启动终端目录）
# 回退到 workspace_path（Agent 沙箱）
# 检测二进制文件：读取前 8KB，如果有 null 字节则判为二进制
```

**代表性的英文注释**:
```python
# swe/tools.py
# ── Attempt 1: Path.write_text() ──
# ── Attempt 2: Python open() with explicit encoding ──
# ── All attempts failed ──

# runtime/runtime.py
# lifecycle.cancel() 可能在 Runtime.cancel() 中已被调用
# LifecycleManager.cancel() 在终态会忽略重复调用
```

**不一致**:
- `_emit_tool_result` 使用英文前缀 `SUCCESS/FAIL`
- `_translate_cmd` 使用英文 `linux_cmd/win_cmd`
- `tool_router.py` 部分注释为英文

### 2.5 Error Messages

| 类别 | 语言 | 一致 |
|------|------|------|
| `friendly_http_error()` 用户可见 | 中文 | ✅ |
| Preflight 检查消息 | 中文 | ✅ |
| Runtime run() 返回消息 | 中文 | ✅ |
| SWEAgent fail() 消息 | 中文 | ✅ |
| PlanAgent 错误消息 | 中文 | ✅ |
| PlanModeGuard 拒绝消息 | 中文 | ✅ |
| 验证器结果消息 | 中文 | ✅ |
| `zmai.errors.RuntimeError` 等 | 中文 | ✅ |
| `ExecutionLog` 截断标记 | 英文 | ⚠️ 开发者内部 |

**结论**: 用户可见错误消息100%为中文，一致。 ✅

### 2.6 CLI Help 文本

| 类别 | 语言 | 行 |
|------|------|----|
| `zmai --help` 输出 | **英文** | `main.py:129-157` |
| `zmai eval --help` | 英文 | `eval_cmd.py:113-117` |
| `zmai bench --help` | 英文 | `eval_cmd.py:67-70` |
| `zmai config` 帮助 | 中文 | `config_cmd.py:15` |
| `zmai auth` 子命令 | 中文 | `auth_cmd.py` |

**不一致** 🔴:

- **`zmai --help` 输出是英文**，但子命令帮助（config/auth）是中文
- REPL 内 `/help` 输出为英文
- `_print_help()` 使用的是英文：`"zmai <task description>     Run a single task"`
- `_run_plan_only` 使用的是中文：`"用法: zmai plan <task description>"`

```
$ zmai --help
ZMAI - Model-Agnostic Agent Runtime       ← 英文
  zmai <task description>                   ← 英文

$ zmai config bad
usage: zmai config <get|set|list> [key] [value]  ← 英文

$ zmai plan
用法: zmai plan <task description>          ← 中文
```

### 2.7 SWE Agent System Prompts

| Prompt 变量 | 语言 | 位置 |
|------------|------|------|
| `_BASE_SYSTEM_PROMPT` | **中文** | `swe/agent.py:102-142` |
| `_build_platform_prompt()` | **中文** | `swe/agent.py:40-92` |
| `_PLAN_EXECUTION_PROMPT` | **中文** | `swe/agent.py:151-157` |
| `_PLAN_SYSTEM_PROMPT` | **中文** | `swe/planner.py:24-61` |
| `_PLANNER_SYSTEM_PROMPT` | **中文** | `swe/plan_agent.py:34-70` |
| `_FALLBACK_PLAN_PROMPT` | **中文** | `swe/plan_agent.py:71-72` |
| `SYSTEM_PROMPT` (模板) | **英文** | `prompt/templates.py:10-29` |
| `PLANNER_PROMPT` (模板) | **英文** | `prompt/templates.py:33-59` |
| `EXECUTOR_PROMPT` (模板) | **英文** | `prompt/templates.py:63-87` |
| `VERIFIER_PROMPT` (模板) | **英文** | `prompt/templates.py:91-112` |
| `REPORT_PROMPT` (模板) | **英文** | `prompt/templates.py:115-147` |

**不一致** 🔴:

- **SWEAgent 实际使用的 system prompt 是中文**（`_BASE_SYSTEM_PROMPT`），直接面向 LLM
- **PromptEngine 模板是英文**（`prompt/templates.py`），定义了 SYSTEM/PLANNER/EXECUTOR/VERIFIER/REPORT 五种模板
- 两部分不一致：实际 Agent 用中文，但模板系统是英文
- PromptEngine 未被 SWEAgent 使用（已在上次审计发现）

---

## 3. 分类汇总

### A. 必须英文

| 项目 | 现状 | 判定 |
|------|------|------|
| Python 标识符（类名/函数名/变量名） | 英文 | ✅ |
| Enum 成员值 | 英文 | ✅ |
| 类型注解 | 英文 | ✅ |
| 代码逻辑关键字（TODO/FIXME/NOTE） | 英文 | ✅ |
| `prompt/templates.py` 模板语法 | 英文 | ✅ （Jinja 变量语法） |
| `execution/log.py` 截断标记 `(truncated N chars)` | 英文 | ✅ |

### B. 可以保留中文

| 项目 | 现状 | 判定 |
|------|------|------|
| Module docstring | 中文 | ✅ 一致 |
| Class/function 开发者 docstring | 中文 | ✅ 整体一致，`verifier.py` 有 5 个英文例外 |
| 内联注释 | 中英混合 | ⚠️ 可接受，但建议统一 |
| Logger 消息 | 中英混合 | ⚠️ 可接受，开发者内部 |
| `zmai.errors` 类定义 | 中文 | ✅ |
| `execution/log.py` 文档 | 中文 | ✅ |
| `workspace/workspace.py` 文档 | 中文 | ✅ |

### C. 用户可见文本 — 必须一致

| 文本 | 现状 | 建议 |
|------|------|------|
| `zmai --help` | **英文** | 🔴 与子命令帮助（中文）不一致，建议统一为英文（Open Source 项目标准） |
| `zmai auth` 输出 | **中文** | ⚠️ 可保留或统一为英文 |
| `zmai doctor` 输出 | **英文** (PASS/Missing) | ✅ |
| `zmai eval list` 输出 | **英文** | ✅ |
| `zmai plan` 输出 | **中文** | ⚠️ |
| PlanAgent `format_plan()` | **中文** | ⚠️ |
| `friendly_http_error()` | **中文** | ⚠️ |
| Preflight 检查消息 | **中文** | ⚠️ |
| Runtime 返回 error | **中文** | ⚠️ |
| PlanModeGuard 拒绝消息 | **中文** | ⚠️ |

**主要问题**: `zmai --help` 是英文，但子命令和错误消息是中文，用户体验不统一。

### D. 开发者内部文本

| 文本 | 现状 | 建议 |
|------|------|------|
| `logger` 消息 | 中文 | 可保留 |
| 内联注释 | 中英混合 | 可保留（读得懂即可） |
| 测试 docstring | 中文 | ✅ 全部中文 |
| `test_workspace_security.py` | 中文 | ✅ |

---

## 4. 不一致清单

| 优先级 | 位置 | 问题 | 建议 |
|--------|------|------|------|
| **P0** | `cli/main.py:_print_help()` | CLI 帮助是英文，子命令是中文 | 统一为用户所在时区（en 或 zh） |
| **P1** | `swe/verifier.py` (5 个函数) | 英文 docstring，与其他模块不一致 | 统一为中文 |
| **P1** | `gateway/errors.py:friendly_http_error()` | 中文 docstring 但英文参数格式 | 保持中文（当前一致） |
| **P2** | `agent/base.py:AgentAction` | 方法注解英文 | 统一为中文 |
| **P2** | `tool/base.py:ToolContext` | attribute 注释英文 | 统一为中文 |
| **P3** | `swe/tools.py:Attempt 1/Attempt 2` | 英文注释块 | 可改为中文 |
| **P3** | `runtime/tool_router.py` | 部分注释英文 | 可改为中文 |
| **信息** | `prompt/templates.py` | LLM 模板英文 | SWEAgent 不使用，设计意图为英文 Prompt |

---

## 5. 结论

**总体一致性评分: B+ (85%)**

- **开发者文档**（docstring）：78 个文件中的 73 个（93.6%）统一为中文，仅 `verifier.py` 有 5 个英文例外
- **错误消息**：全部为中文 ✅
- **CLI 帮助**: 🔴 `--help` 出口英文，子命令中文 — **唯一重大不一致**
- **内联注释**: 中英混合，可接受
- **LLM Prompt 模板**: 英文模板（`prompt/templates.py`）vs 中文硬编码 prompt（`swe/agent.py`）— 两部分不互用，但各自内部一致

**修复优先级**: CLI help 文本一致性 > `verifier.py` docstring > `AgentAction` 方法注解
