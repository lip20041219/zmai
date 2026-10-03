<div align="center">

# ZMAI

**Autonomous Software Engineering Agent Runtime**

[![CI](https://github.com/lip20041219/zmai/actions/workflows/test.yml/badge.svg)](https://github.com/lip20041219/zmai/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](pyproject.toml)

`pip install zmai` · **Zero third-party dependencies** · **1753 tests · CI green**

</div>

---

## 一句话

> **ZMAI** 是一个基于 LLM 的轻量级 SWE Agent，能够自动分析测试失败、定位业务代码、执行修复、运行测试，并在验证成功后停止。

---

## 1. Overview

ZMAI 是一个**开源自主软件工程 Agent 运行时**。给它一个任务（"修复这个 Bug"、"增加这个功能"），它会自主完成：

```
Bug → 分析测试失败 → 定位业务代码 → 修改代码 → 运行测试 → 验证 → 自主停止
```

**它不是简单的 LLM API 封装** —— 从问题理解、测试驱动调试、代码定位、修复规划、验证闭环到完成检测与自主停止，整个 Agent 循环都在本项目内实现。约 2.5 万行纯 Python 标准库（`urllib` / `subprocess` / `pathlib` / `json`），零第三方依赖。

默认后端为 **DeepSeek**，默认模型 **deepseek-v4-flash**（OpenAI-compatible API）。

---

## 2. Why ZMAI

- **💨 本地 & 私有** — 代码不出你的机器
- **🔌 Provider-agnostic** — 运行时切换 DeepSeek / Claude / Gemini / 自定义插件
- **📦 零依赖** — 纯 Python stdlib，无 `requests` / `httpx` / `pydantic`，无 lock 文件
- **🧩 可嵌入** — `import zmai` 当库用
- **🛡️ 多层防护** — TestGuard / AcceptanceGuard / LoopGuard / FixDriving / CompletionState，拦截常见的空转与伪完成路径，阻断测试证据被篡改后的完成判定
- **🛑 受控停止** — 满足完整验证范围与有效 evidence 条件的测试通过后，Runtime 才允许进入完成状态，不耗尽 token

---

## 3. Core Capabilities

- **SWE Agent 工作流闭环** — 发现 → 先跑测试 → 分析失败 → 修改代码 → 验证
- **FailureParser** — 从 pytest traceback 语义化解析失败根因（expected / actual / line / 候选业务文件）
- **FixPlanner** — 基于失败解析自动生成"诊断→计划→修改→验证"的有序修复计划
- **TestGuard** — 测试文件只读保护，阻止通过删除测试、缩小 scope、修改验收文件等方式伪造有效 green evidence；并按基线测试数拦截套件收缩
- **LoopGuard** — 相同调用 / 相同失败 / 无进展 三重循环检测
- **FixDriving** — 测试失败后强制进入修改阶段，阻断"只读不修"空转
- **ReadCache** — 重复读取同一未变化文件自动命中缓存并提示复用，避免无效 read
- **CompletionState** — 跨轮累积完成判定；只有覆盖完整基线范围、且未被后续修改作废的全绿运行才被接受
- **Verifier** — 客观验证（auto_verify），不因"工具调用成功"就判定任务完成
- **Workspace Sandbox** — 路径穿越防护、文件大小限制、符号链接检测
- **Multi-model Gateway** — 统一 Backend 接口 + 加密凭证存储

---

## 4. Architecture

```mermaid
graph TB
    subgraph Entry
        CLI[CLI / REPL]
        API[import zmai]
    end
    CLI --> RT
    API --> RT

    subgraph RT[Runtime]
        SWE[SWE Agent]
        CM[ContextManager]
        MM[Memory]
        LG[LoopGuard]
        CS[CompletionState]
        VF[Verifier]
    end

    SWE --> TR[Tools / Tool Registry]
    TR --> WS[Workspace Sandbox]
    WS --> GW[Gateway]

    subgraph GW[Gateway / LLM Backend]
        DS[DeepSeek]
        CL[Claude]
        GE[Gemini]
        PL[Plugin]
    end
```

**Layer flow**: `CLI/API → Runtime → SWE Agent → Tools → Workspace → Gateway → LLM Backend`

---

## 5. Bug-fixing Workflow

这是 ZMAI 已经真实验证的完整修复闭环：

```
Bug
 ↓
pytest failure          ← 先跑测试，看到失败
 ↓
FailureParser           ← 语义化解析失败根因（expected / actual / line / 候选文件）
 ↓
candidate localization  ← 定位到业务源码文件
 ↓
FixPlanner              ← 生成"诊断→计划→修改→验证"修复计划
 ↓
ReadFile                ← 读取相关源码
 ↓
Edit                    ← 最小、定向修改业务代码
 ↓
pytest                  ← 重跑测试验证
 ↓
verification            ← Verifier 客观确认全绿
 ↓
complete                ← CompletionState 判定完成，自主停止
```

失败不是终点，而是循环里的一等状态。完整 SWE Loop 为：

```
failure → diagnosis → repair plan → edit → verification
             ↑                                  │
             │                          regression / recovery
             │                                  │
             └────────── bounded retry ←────────┘
                                                │
                     completion（全绿且覆盖基线）/ failure（预算耗尽）
```

- **回归检测** — 逐轮比较**同一条测试命令**下的 passed/failed 计数；退化时注入 `[Regression]`；项目被改到 import 失败（collection/import error）时注入 `[Recovery]`，要求先回退再谈修复
- **有界恢复** — 每条恢复路径都有预算（force_edit 步数、灾难性回归次数、completion block 次数、edit 失败恢复次数），耗尽即明确 `FAILED`，不无限重试、也不伪装成 timeout
- **Graph trace** — 控制流**实际到达**的节点（entry / plan / backend / tool / regression / repair plan / edit recovery / read limit / fix driving / loop guard / completion gate / done / failed）与节点间转移写入 `graph_trace`，`validate_trace` / `analyze_trace` 供审计；`enforce=False`，非法转移只记录不抛错，**不参与任何判定**

---

## 6. Demo

![ZMAI Demo](docs/demo.gif)

> 更高清视频: [docs/zmai-demo.mp4](docs/zmai-demo.mp4)

一行命令修复真实 Bug：

```bash
zmai "Fix the ValueError in parse_config() — the function crashes on empty input"
```

---

## 7. DeepSeek Backend

ZMAI 默认使用 **DeepSeek** 后端：

| Backend | Default Model | Notes |
|---|---|---|
| **DeepSeek**（默认） | `deepseek-v4-flash` | OpenAI-compatible API，低成本 |
| **Claude** | `claude-sonnet-4-6` | Anthropic Messages API |
| **Gemini** | `gemini-2.0-flash` | Free tier 可用 |
| **Plugin** | *any* | 自带 20 行 Python 文件即可接入 |

- DeepSeek 走 OpenAI-compatible 端点，`base_url` + `api_key` 即可配置
- 通过 `zmai auth setup` 加密存储凭证，或环境变量 `DEEPSEEK_API_KEY`
- **ZMAI 不含任何硬编码 API key，也不收集/上传使用者的 key**。每个使用者配置自己的 key（环境变量或加密凭证存储）；`base_url` 默认 `https://api.deepseek.com/v1`，零额外设置。

### 最小配置与运行示例

配置一个 DeepSeek key 后即可零设置使用（DeepSeek 是默认后端）：

```bash
# 方式一：环境变量（临时，仅当前 shell 生效）
export DEEPSEEK_API_KEY=your_api_key_here

# 方式二：交互式加密存储（推荐，持久化）
zmai auth setup

# 直接用（默认 DeepSeek backend）
zmai "修复当前项目所有 bug"

# 切换到其他后端
zmai --backend claude "分析这个仓库的架构"
zmai --backend gemini "写一个单元测试"
```

---

## 8. Safety Guards

ZMAI 内置多层防护，防止空转、伪造成功与无限循环：

- **TestGuard** — 测试文件（`tests/`、`test_*.py`、`*_test.py`、`conftest.py`）只读；拦截编辑测试、删除测试、放宽断言、修改 pytest 配置；并按**基线测试数**拦截"套件收缩"伪造成功
- **AcceptanceGuard** — 验收文件（测试文件）在 pytest **会话启动与结束两个时点**核对内容摘要。守卫经 `PYTHONPATH` + pytest 插件注入被测解释器；测试窗口内验收文件发生违规变化时**中止该会话**（非 0 退出、不产出任何 passed/failed 计数），使被污染的 green 无从产生。Runtime 另外要求"确实跑了 pytest 且产出计数"的运行留下**新的 receipt 凭据**：守卫被环境变量覆盖 / 清除而根本没加载时，本次结果不作有效完成证据
- **pytest scope guards** — 三层范围约束：命令点名排除测试的运行**完全作废**（既非通过也非失败证据）；run 起始记录定义 pytest scope 的配置段基线，运行期间配置漂移则结果不作数；指定位置目标 / 缩范围选项的 green 不构成"覆盖完整套件"的证据，必须重跑完整套件
- **Stale bytecode invalidation** — 工作区发生源码变化后主动作废相关 `__pycache__` 字节码（含 shell / git 等非工具写入路径），避免后续验证继续读取过期 `.pyc`
- **LoopGuard** — 检测连续相同调用 / 相同失败 / 无进展，触发结构化恢复信号
- **FixDriving** — 测试失败后达到读取阈值即强制进入修改阶段，结构性阻断继续只读
- **CompletionState** — 跨轮累积完成判定；partial_green（子集全绿未达基线）不完成、不累计，强制运行完整套件
- **Bounded recovery** — 强制修改期、灾难性回归、完成拦截、edit 失败恢复各自独立预算（`MAX_FORCE_EDIT_STEPS` / `MAX_REGRESSION_RECOVERIES` / `MAX_COMPLETION_BLOCKS` / `MAX_EDIT_FAILURE_RECOVERIES`），超预算即明确失败
- **Test command timeout** — 测试命令走独立超时预算（`timeout.test`，默认 600s），不再套用普通 shell 的 30s；超时**既不构成通过证据也不构成失败证据**，连续超时有界失败
- **Workspace Sandbox** — 路径穿越防护、文件大小限制、符号链接检测
- **PlanModeGuard** — Plan 未确认时按**工具权限**拒绝写工具与危险 shell / git 写命令（在工具层拦截，不依赖 prompt 约束）
- **Hard stop** — `max_steps=300` 硬上限，达到上限即停止

---

## 9. Verification

ZMAI 的完成判定依赖**客观验证**而非工具调用成功：

1. pytest `exit_code == 0`
2. `verify_test_output()` 通过（解析测试输出）
3. 测试套件覆盖达到基线（`parse_test_totals`）
4. `CompletionState.should_complete()` 为真
5. 测试通过后无新的业务修改
6. 本次运行**确实执行了测试**（计数为 0 的命令，如 `--collect-only`/`--help`，不构成证据）
7. 测试命令超时不计入通过证据，也不计入失败证据

满足以上条件后返回 `complete`，Runtime 立即 `break`，不再调用 LLM / read / edit / pytest。

判定是 **fail-closed** 的：拿不到结构化测试计数、测试曾失败后没有覆盖基线的全绿重测、改过代码却没有修改后的有效验证 —— 都不判完成。判据落在工作区真实状态（git 索引 / 文件指纹）与测试计数上，而不是"工具调用返回 success"。

### 9.1 Runtime enforcement：LLM 提议，Runtime 判定

这是 ZMAI 在 Agent Loop 之上的核心设计：**LLM 提出完成 / 停止，不等于任务已经完成。** 最终状态由 Runtime 根据实际工作区状态、测试结果、验证范围以及 guard / evidence 状态共同判定：

- **完整测试 evidence** — 只有结构化计数覆盖完整基线套件的全绿才是完成证据；零计数运行（如 `--collect-only` / `--help`）与解析不出计数的输出都不构成证据
- **workspace change / regression detection** — 判据落在工作区真实状态（git 索引 / 文件指纹）：测试通过后的任何业务修改立即作废该次 green；回归与 collection / import error 触发有界恢复流程
- **acceptance guard** — 测试窗口内验收文件偏离时该 pytest 会话被中止且不产出计数；守卫被清除、没有新凭据的运行同样不作证据
- **pytest scope guard** — 缩范围（点名排除 / 配置漂移 / 位置目标子集）得到的 green 不被接受为完整验证
- **termination / completion constraints** — 完成是 Runtime 侧的硬终止：判定满足后不再调用 LLM / 工具；各恢复路径预算耗尽则明确失败，而不是继续重试或伪装成 timeout

**诚实边界**：以上机制约束的是 ZMAI 自己能够观测与控制的那条验证路径（工具调用、测试进程、工作区状态、pytest 配置与凭据）。对被测进程之外的外部副作用、测试会话内部未被守卫观测到的瞬时状态变化，本项目不声称绝对覆盖。

---

## 10. Installation

```bash
git clone https://github.com/lip20041219/zmai.git
cd zmai
python -m venv .venv
.venv/Scripts/activate          # Windows; Unix: source .venv/bin/activate
pip install -e ".[dev]"
```

---

## 11. Quick Start

```bash
# 配置 API key（加密存储）
zmai auth setup

# 或设置环境变量
export DEEPSEEK_API_KEY=***        # 或 ANTHROPIC_API_KEY / GEMINI_API_KEY

# 运行一次任务
zmai "Create hello.py and run it"

# 交互式 REPL
zmai
```

---

## 12. Configuration

ZMAI 配置按优先级解析：**file → env → CLI**。

- `zmai.json` / `zmai config set <key> <value>` — 文件配置
- 环境变量 — `DEEPSEEK_API_KEY`、`ANTHROPIC_API_KEY` 等
- CLI 参数 — `--backend`、`--max-steps`、`--json`

常用项：

| 配置 | 默认 | 说明 |
|---|---|---|
| `backend` | `deepseek` | 默认后端 |
| `runtime.max_iterations` | `300` | Agent 最大步数（`--max-steps`） |
| `timeout` | `30` | 工具执行超时（秒） |
| `fix.read_limit` | `3` | 失败后允许的只读诊断文件数 |

---

## 13. Testing

```
pytest

# Ubuntu (Python 3.10 / 3.11 / 3.12)
1737 passed, 16 skipped

# Windows (Python 3.10 / 3.11 / 3.12)
1749 passed, 4 skipped
```

> 数字取自当前 main（`c4e2e5a`）的 CI run：6/6 matrix job 全部通过，0 failed。两个平台收集到的测试总数相同（1753），平台相关的 skip 数不同（Ubuntu 16 / Windows 4），因此 passed 数相差 12。

- 测试覆盖 auth、credential store、gateway、runtime、loop guard、termination、workspace security、SWE workflow（completion gate / test scope / timeout / acceptance window guard / trace graph）、plan mode、CLI 等
- **无需 API Key 即可运行**（mock backend）
- CI 运行于 Ubuntu + Windows × Python 3.10/3.11/3.12

> ⚠️ 测试结果 ≠ SWE-bench 成绩。本项目**尚未发布**公开标准基准（SWE-bench Full/Verified/Lite）分数。

### 内部 SWE-bench Lite smoke（真实运行，非官方成绩）

数据与逐例分析见 [`benchmarks/results/swebench_lite/PROGRESS.md`](benchmarks/results/swebench_lite/PROGRESS.md)，模型为 DeepSeek 后端，`max_steps=60`、`eval.require_code_change=true`：

- `pallets__flask-4992` — **resolved**（39 步 / 271s，FAIL_TO_PASS 由 FAIL 转 PASS，PASS_TO_PASS 保持通过）
- `psf__requests-3362`、`pylint-dev__pylint-5859` — 未解决；agent 产生的 diff 存在语法错误，另有网络/配额中断
- `pydata__xarray-4248` — 未解决；耗尽步数未产生 git diff
- `mwaskom__seaborn-3010` — 基础设施失败（模型配额 HTTP 402，agent 未执行）

单批次 5 个实例，失败中包含基础设施与模型能力原因，**不能作为 SWE-bench 成绩或能力对比依据**。

---

## 14. Project Structure

```
zmai/
├── src/zmai/
│   ├── agent/            # Agent 抽象
│   ├── cli/              # CLI 入口（REPL / 单次任务）
│   ├── gateway/          # 多后端网关（DeepSeek / Claude / Gemini / 插件）
│   ├── runtime/          # Runtime 执行循环
│   ├── swe/
│   │   ├── agent.py            # SWE Agent 主逻辑（含 SWE Loop、scope / 验收守卫接线）
│   │   ├── graph.py            # 控制流 trace（观察层，enforce=False）
│   │   ├── completion.py       # CompletionState 完成判定
│   │   ├── acceptance_guard.py # 测试窗口内验收文件守卫（pytest 插件）
│   │   ├── loop_guard.py       # LoopGuard 循环保护
│   │   ├── failure.py          # FailureParser 失败解析
│   │   ├── fix_planner.py      # FixPlanner 修复规划
│   │   ├── plan_agent.py       # Plan 模式专用 agent（只读分析）
│   │   ├── plan_guard.py       # PlanModeGuard 工具权限守卫
│   │   ├── scanner.py          # RepositoryScanner 项目源码发现
│   │   ├── github.py           # GitHub API 客户端（纯 stdlib）
│   │   ├── verifier.py         # Verifier 客观验证
│   │   └── tools.py            # 工具（含 TestGuard / ReadCache）
│   ├── workspace/        # Workspace Sandbox
│   └── ...
├── tests/                # 1753 测试（CI 收集数）
├── examples/             # 使用示例
└── docs/                 # 文档 / zmai-demo.mp4
```

---

## 15. Limitations

- **Shell 执行风险** — `shell_exec` 直接在本机运行命令；headless 模式**无确认提示**，请视为可信贡献者
- **凭证加密为混淆而非硬件级** — 密钥文件与凭证同机，本地加密防 casual 读取
- **尚未发布标准基准** — 无官方 SWE-bench 分数；只有内部 SWE-bench Lite smoke（5 实例，1 resolved），勿跨项目对比
- **大仓 timeout 未经 E2E 验证** — 测试命令独立超时（`timeout.test`）已实现并有确定性测试覆盖，但"真实大仓跑满 600s 超时"这条路径**尚未在 E2E 中验证**；当前环境的 SWE-bench 实例存在依赖代差，不作为可靠验证目标
- **Windows 优先** — 内置命令翻译与 UTF-8 处理，但 Linux/macOS 覆盖以 CI 为准

---

## 16. Roadmap

- **Better sandbox** — Docker sandbox 默认、命令 allowlist
- **Multi-agent** — 并行任务编排
- **SWE-bench 评估** — 发布真实 SWE-bench Lite pass@1（计划中，未完成）
- **Context compaction** — 更强的长上下文策略
- **macOS CI 覆盖**

---

## 17. License

MIT © ZMAI Contributors — see [LICENSE](LICENSE).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Changes follow [CHANGELOG.md](CHANGELOG.md).

## Security

See [SECURITY.md](SECURITY.md). Report vulnerabilities privately.
