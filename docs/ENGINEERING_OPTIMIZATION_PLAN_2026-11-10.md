# ZMAI 可信判定层工程优化方案

- 版本：v1.1
- 日期：2026-11-10
- 代码基线：`b32d64f`
- 依据：4 个真实实例主机探针结果 + 现有控制层实现
- 适用读者：项目作者、评审者、二次开发者

---

## 0. 本版修订说明（v1.1）

本版针对评审提出的四个问题做了修订：

| 问题 | 修订 |
|---|---|
| 决策表优先级冲突 | 规则改为**互斥**：completed 只要求 full scope；candidate 只要求非 full / coverage unknown；并按 validity → completed → candidate 顺序判定 |
| "无新增失败"不可证明 | 改为"**在已执行且可信的测试范围内未观察到新增失败**"，未执行范围记录为 unverified |
| 完整性失败与覆盖不足混淆 | 引入证据三态：**polluted/stale（不可用）**、**valid-incomplete（可候选）**、**valid-complete（可完成）** |
| nonce/回放边界 | 明确 Runtime 生成与核验、绑定命令/指纹/运行、回放必须重新执行、不承诺绝对防伪、测量回放成本 |

同时明确把工作拆为两条路线：

- **路线 A（可信判定层）**：本方案主体，减少错误完成、保护证据、减少无意义超时
- **路线 B（真实修复能力）**：单独阶段，提升正确补丁率、接口兼容性、隐藏测试命中率

本方案**不承诺**提升 SWE-bench 解决率；candidate 提前退出甚至可能降低探索。因此路线 B 必须独立评测，不与 A 同阶段大改。

---

## 1. 摘要（一页看懂）

ZMAI 的目标不是"比 Codex/Claude Code 更会写代码"，而是回答一个更具体的软件工程问题：

> **如何让 AI 自主修复 Bug，同时保证结果有证据、过程受约束、失败不谎报？**

本方案把"完成判定"从模型自述改为 **Runtime 按证据计算**：

- 每次测试运行形成结构化证据（范围、指纹、基线差异、完整性标记、证据状态）
- 终态由单一权威函数 `decide_terminal(ledger)` 计算：`completed` / `candidate` / `failed`
- 验证不足只能产出 **候选补丁（candidate）**，绝不冒充成功
- 判定前对关键证据做 **回放复核 + nonce 防伪**
- 覆盖范围不可证明时 **降级为候选**；证据被污染时 **直接作废**

创新点集中在四点：证据驱动判定、候选语义、防篡改复核、预算感知终止。可靠性通过威胁模型、fail-closed、回放验证与明确的残余风险清单来保证。

---

## 2. 项目现状

### 2.1 代码与能力基线

- 代码基线：`b32d64f`（公共 API 守卫已提交）
- 已实现的控制层能力：
  - 单一终态：`completed` / `candidate` / `failed` / `timeout`
  - 候选提交：编辑后定向绿 + 无已知新回归 + 不改测试/不缩范围
  - 防篡改：`acceptance_guard`（pytest 插件）、`TestGuard`（范围/排除选项）、测试文件保护
  - 恢复：`LoopGuard`、编辑验证与重试、墙钟限制
  - 编辑期风险提示：公共 API 变化（AST）、返回值/异常变化（启发式）
  - 基线对比：`test_baseline_comparison`、`unresolved_regressions`

### 2.2 四实例实测

| 实例 | agent 终态 | FTP | PTP | 独立判定 |
|---|---|---|---|---|
| pylint-7228 | candidate | 2/2 | 10/10 | resolved |
| flask-4045 | timeout | 1/2 | 50/50 | 未解决 |
| pylint-7993 | timeout（补丁正确） | 1/1 | 10/10 | 未收尾 |
| seaborn-3407 | completed（公开绿） | 0/1 | 120/120 | 未解决 |

结论：

1. 定位与修改方向稳定，失败集中在隐藏测试精确性、回归控制与终止策略。
2. "公开全绿"不能保证隐藏测试通过（seaborn 案例）。
3. "补丁正确但超时"会被判失败（pylint-7993 案例），说明终止/预算策略是独立瓶颈。

### 2.3 当前模型条件

- 仅 DeepSeek `deepseek-flash` 可用
- 订阅制产品（Codex Plus、Claude Code）不是可编程 API，不能作为 backend
- 结论：判定层是当前资源下最值得投入、且不依赖模型强度的方向

---

## 3. 问题定义：为什么需要判定层

四类失败模式，现有控制层只能部分覆盖：

| 失败模式 | 案例 | 现状 | 判定层目标 |
|---|---|---|---|
| 公开测试全绿但隐藏测试不通过 | seaborn-3407 | completed 但独立验收失败 | 明确"公开证据 ≠ 隐藏验收" |
| 修改引入回归 | pylint-7993 | PTP 9/10 | 基线对比 + 影响集 + 覆盖降级 |
| 改对但未收尾 | pylint-7993 重跑 | 跑全量超时 | 预算感知终止 + 候选出口 |
| 用旧结果/缩范围/改测试冒充 | 历史审计 | TestGuard 部分覆盖 | nonce + 回放 + 指纹扩展 |

核心矛盾：**Agent 自证完成不可信；全量套件代价过高；隐藏测试不可知。**

解法：把"完成"降级为"证据等级"，由 Runtime 计算，并允许诚实的中间态。

---

## 4. 设计原则

与项目既有 I1–I8 对齐：

| 原则 | 本方案落点 |
|---|---|
| I1 Termination | 决策表 + 预算守卫给出明确终态 |
| I2 Completion Evidence | 每条完成都必须有账本证据 |
| I3 Fail-Closed | 证据被污染不可用；覆盖不可证明只能 candidate |
| I4 Exception → Failed | 关键异常映射 failed，不吞掉 |
| I5 Bounded Recovery | LoopGuard + 重试上限不变 |
| I6 Workspace-Change Progress | 指纹使编辑前证据失效 |
| I7 Single Authority | `decide_terminal()` 是唯一终态来源 |
| I8 Prompt for Efficiency | Prompt 只提升效率，不决定终态 |

附加工程约束（项目规则）：

- 最小 diff、标准库优先、不新增依赖
- 定向测试，不跑全量 pytest
- 先根因、后修复；行为等价重构优先

---

## 5. 创新点（与主流 SWE agent 的差异）

> 说明：以下均为设计层面的差异化，不宣称榜单分数领先。

### 5.1 证据驱动判定（核心）

- **主流**：模型自己 submit/finish，外部 harness 用隐藏测试判定
- **ZMAI**：Runtime 读取结构化账本，按规则计算终态；模型只能提交候选
- **价值**：终态可解释、可复现、可审计；模型无法用自述换取成功

### 5.2 候选 ≠ 成功

- `candidate` 是一等终态：有有效证据但验证不完整
- 失败降级而非谎报；对外呈现"已验证范围 / 未验证范围"
- **价值**：在高成本验证场景下仍能产出可用结果

### 5.3 防篡改与证据复核（含边界）

- acceptance guard + TestGuard + 指纹 + nonce + 回放验证
- **nonce 规则**：由 Runtime 生成、保存、核验；绑定本次 run 的命令、工作区指纹与时间；guard 输出中携带 nonce；Runtime 只接受匹配证据
- **回放规则**：判定前用**独立新进程重新执行**记录的命令，比较结果；不允许复用原始输出文本
- **明确边界**：被测代码与测试进程共享信任边界时，nonce 理论上可被读取或复用，回放也不能证明未知功能正确；**不承诺**对能控制宿主机或测试进程的恶意程序具有绝对防伪能力
- **成本**：回放会增加执行时间，必须记录并度量（见 11.1）
- **价值**：把"相信记录"提升为"复核记录"，但只在声明的威胁模型内成立

### 5.4 Fail-Closed 与证据三态

证据分三种状态，严格区分：

| 状态 | 触发 | 处置 |
|---|---|---|
| **polluted / stale** | 篡改测试、nonce 无效、回放不一致、运行后工作区/测试/配置变更、指纹不匹配、凭据异常 | 该证据**作废**，不得用于 completed 或 candidate；需要新证据 |
| **valid-incomplete** | 证据真实有效，但 scope 非 full，或覆盖不可证明（动态导入、影响集为空） | 只能 `candidate`，并记录 unverified 范围 |
| **valid-complete** | scope == full、exit 0、完整性通过 | 可进入 `completed` 判定 |

要点：**覆盖不足 ≠ 证据污染**。前者允许候选，后者连候选都不允许。

### 5.5 预算感知终止

- 全量测试启动前检查剩余墙钟（上次耗时 × 1.5 估计）
- 不足则拒绝启动并提示候选提交
- **价值**：解决"改对但超时"的浪费

### 5.6 编辑期接口/行为风险提示

- 公共属性改名、签名变化、基类变化、返回值形态/异常类型变化
- 只警告、不阻断，引导先查引用与调用方
- **价值**：在错误发生前降低不必要的接口变化

### 5.7 轻量可信运行时

- 纯标准库、单进程、可替换模型、无第三方 Agent 框架
- 指标诚实：false-success rate、timeout→candidate 转化率、证据有效率
- **价值**：适合教学、研究与二次开发

---

## 6. 目标架构

### 6.1 组件关系（文本图）

```
 agent loop (agent.py)
   │  tool calls
   ▼
 tools.py ── edit/write ──► _write_checked ──► public API / behavior warnings
   │  shell_exec (pytest)
   ▼
 test scope classifiers (cli_scope_filter / test_scope_targets / _is_full_scope_test_command)
   │
   ▼
 evidence.py  (新增)
   ├─ EvidenceRecord       每条测试运行
   ├─ CompletionLedger     账本（单一真相源）
   └─ decide_terminal()    纯函数决策表
   │
   ▼
 terminal actions: completed / candidate / failed
```

### 6.2 证据记录 schema

```python
EvidenceRecord = {
    "run_id": str,
    "step": int,
    "command": str,
    "scope": "full" | "targeted" | "subset" | "unknown",
    "coverage": "full" | "partial" | "unknown",   # 本次运行可证明的覆盖范围
    "evidence_status": "valid" | "polluted" | "stale",
    "workspace_fingerprint": str,                 # 运行时刻工作区指纹
    "exit_code": int,
    "passed": int,
    "failed": int,
    "failed_ids": [str, ...],                     # 截断保存
    "executed_tests": int,                        # 本次实际执行数量
    "baseline_compare": {
        "available": bool,
        "new_failures": [str, ...],
        "fixed_failures": [str, ...],
        "preexisting_failures": [str, ...],
    },
    "integrity": {
        "test_files_unchanged": bool,
        "config_unchanged": bool,
        "no_scope_narrowing": bool,
        "ran_after_last_edit": bool,
        "nonce_verified": bool,
        "replay_verified": bool,
    },
    "unverified_scope": str,                      # 例如 "full suite"、"tests/ 下其余文件"
}
```

### 6.3 决策表（互斥规则，唯一权威）

判定顺序：

```
Step 1  取最新有效证据；polluted/stale 证据直接丢弃，不得用于任何终态。
        若没有可用的新证据 → 继续循环或 failed。

Step 2  completed（互斥条件）：
        scope == "full" 且 exit_code == 0 且 evidence_status == "valid"
        且 integrity 全部通过（测试/配置未变、nonce/回放通过、指纹匹配）

Step 3  candidate（互斥条件）：
        evidence_status == "valid"
        且 scope != "full"（或 coverage == "unknown"）
        且 存在“编辑后”的定向绿证据
        且 在已执行且可信的测试范围内未观察到新增失败
        （未执行范围记入 unverified_scope，不作为“没有回归”的证明）

Step 4  failed：关键异常、不可恢复、重复阻断

Step 5  其他 → 继续循环（有界）
```

互斥与顺序说明：

- completed 只接受 full scope；candidate 只接受非 full 或覆盖未知 —— **两者不可能同时匹配**
- 即使一次定向运行包含全部测试，只要 scope 判定为 full，就按 completed 处理；否则按 candidate 处理
- "未观察到新增失败"仅是对**已执行可信范围**的陈述，不是对全项目的证明
- 无基线（baseline 不可用）时：completed 只要求 full 且 exit 0；candidate 仍允许，但必须记录 `baseline_compare.available = false`

---

## 7. 分阶段实施

### 路线 A · 可信判定层（本方案主体）

#### M1 · 判定层核心（9–14 人日）

##### P0-1 证据账本 + 决策表（3–5 人日）

- 新增 `src/zmai/swe/evidence.py`
- `agent.py` 的测试结果处理改为写入账本；旧 metadata 双写
- 决策先等价迁移；新旧结论不一致时回退旧逻辑并记录
- 测试：决策表逐条用例（含互斥条件）；4 条历史 run 的 golden replay
- 验收：历史结论不变；polluted 证据无法产生候选或完成

##### P0-2 nonce + 回放验证（2–3 人日）

- acceptance guard 输出 Runtime 生成的 nonce；Runtime 核验并绑定 run/命令/指纹
- 判定前用独立进程重放关键测试命令；记录回放耗时
- 验收：旧输出、伪造输出、截断输出、文本复用均无法通过；回放成本有记录

##### P0-3 指纹扩展 + 覆盖升级（3–4 人日）

- 哈希扩展：`conftest.py`、pytest 配置、插件、`.pth`、`sitecustomize`
- 检测动态导入/配置变更 → `coverage = "unknown"`
- 验收：污染类用例作废证据；覆盖未知类用例只能 candidate

##### P0-4 判定器自身测试（1–2 人日）

- golden replay 固定输入
- 每条新规则必须带反例
- 验收：决策函数覆盖完整；异常路径有测试

#### M2 · 成本与闭环（5–8 人日）

##### P1-1 时间预算守卫（0.5–1 人日）

- shell_exec 启动全量测试前检查剩余墙钟
- 估计值 = 同命令上次耗时 × 1.5；无记录用保守默认
- 不足则拒绝启动，返回候选提交建议

##### P1-2 `verify` 命令 / 候选升级（1–2 人日）

- 用户跑完全量后，通过回放验证把 candidate 提升为 completed
- 记录升级证据

##### P1-3 批量评测（6–8 实例，3–5 人日）

- 统一探针脚本 + 结果汇总
- 指标：resolved、false-success、timeout→candidate、token、耗时、回放成本
- 验收：同模型下 resolved 不下降；超时浪费显著减少

### 路线 B · 真实修复能力（独立阶段，不与 A 同阶段扩展）

目标：提高正确补丁率、保持接口兼容、减少隐藏测试失败。

保留的最小必要改进（已具备或低成本）：

1. 编辑期公共 API / 行为变化提示（已实现）
2. fix_planner 的调用方检查（已实现）
3. 用于验证“candidate-first 不降低解决率”的 A/B 评测

暂不在本阶段做：多候选、自一致性、强模型接入、影响集精确化。这些必须等路线 A 稳定后单独评测。

---

## 8. 明确不做（性价比低）

| 项 | 原因 |
|---|---|
| agent.py 大拆分 | 只提升维护性，风险高 |
| Tier 2 精确 import 图 | 动态依赖无法穷尽 |
| 多候选/自一致性 | flash 下成本翻倍、收益未知 |
| 全量 SWE-bench Lite | 镜像/磁盘/API 成本数倍 |
| 容器级安全隔离 | 超出威胁模型 |
| 多模型路由 | 当前只有一个可用模型 |
| UI/插件生态 | 对研究目标零贡献 |
| 默认追求全量 completed | 与创新方向冲突 |

---

## 9. 可删除 / 可缩减

1. 账本落地后删除重复状态：`test_success_count`、`candidate_test_evidence`、`test_baseline_comparison` 等
2. 清理旧完成路径（保留一层兼容后再删）
3. 根目录实验产物归档（`_experiments/`、`ab_output/`、`_fix/`、旧日志等，不删数据）
4. 版本兼容的仓库合并共享 evalenv，减少环境维护面
5. 历史文档归档，只保留权威报告

---

## 10. 可靠性与边界

### 10.1 威胁模型

- 防御：会犯错、会走捷径的模型（改测试、缩范围、复用旧结果、伪造输出）
- 不防御：拥有宿主机或测试进程控制权的恶意程序

### 10.2 设计不变量

> **任何检测不到的缺口，最坏结果只能是降级为 candidate 或被拒绝，不能是静默的假成功。**

补充：**证据被污染时必须作废，不能降级为 candidate。**

### 10.3 残余风险

| 风险 | 后果 | 是否假成功 |
|---|---|---|
| 隐藏测试要求推断不出 | 候选不被外部接受 | 否（candidate） |
| 动态导入导致影响集漏检 | 回归未发现 | 否（candidate + unverified_scope） |
| 被测代码篡改 pytest 进程 | 理论上可伪造 | 威胁模型外 |
| 指纹不可得 | 无法验证 | 否（证据作废） |
| 回放遇到非确定性测试 | 误判为污染 | 记录并重跑一次；仍不一致则作废 |

### 10.4 能力边界

- 只有 flash：小样本 resolved 预期 20%–30%
- 判定层不提升隐藏测试命中率
- 本阶段只支持 ≤10 个手工适配实例

---

## 11. 指标与评测

### 11.1 核心指标

| 指标 | 定义 |
|---|---|
| resolved | candidate/completed 且独立 FTP/PTP 全过 |
| false-success rate | 判 completed 但独立验收失败的比例（目标 0） |
| timeout→candidate | 超时前有正确补丁、转为候选的比例 |
| 每实例成本 | 模型调用数、token、墙钟 |
| 回放成本 | 回放执行时间占单实例总耗时比例 |
| 证据有效率 | valid 证据占比（polluted/stale 排除后） |
| 判定一致率 | 新旧决策函数/人工判断的一致率 |

### 11.2 评测方法

- 6–8 个已适配实例，每例单次正式运行
- 独立验收：baseline + agent patch + test_patch → FTP/PTP
- 记录环境与模型版本、回放耗时

---

## 12. 工期与资源

| 阶段 | 人日 | 全职日历 |
|---|---:|---|
| M1（路线 A） | 9–14 | 2–3 周 |
| M2（路线 A） | 5–8 | 1–1.5 周 |
| 路线 B 最小验证 | 2–3 | 0.5–1 周 |
| 路线 B 扩展（可选） | 9–15 | 2–3 周 |

假设：1 人 + AI 辅助。业余每天 3–4 小时，日历工期约为全职的 2 倍。

---

## 13. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 判定器 bug | 假阴性 | golden replay、双写、反例测试 |
| candidate-first 降低探索 | 解决率下降 | A/B 后再默认 |
| 影响集漏检 | 回归未发现 | unverified_scope 显式记录；覆盖未知只给候选 |
| 环境适配脆弱 | 实例不可用 | 每实例固定 env + 预检 |
| 回放成本过高 | 耗时增加 | 只回放候选证据；记录比例；必要时抽样 |
| 模型上限 | 解决率天花板 | 明确边界，预算允许再换模型 |

---

## 14. 使用说明（目标状态）

### 终态解读

- `completed`：全量绿 + 完整性通过，可作为"已验证完成"
- `candidate`：有有效证据但验证不完整，附证据包与 unverified 范围；需外部/人工复核
- `failed`：关键异常、证据无法重建或不可恢复
- `timeout`：预算耗尽；先看是否已有正确补丁可转候选

### 推荐流程

1. Agent 产出 candidate + 证据包
2. 用户/CI 跑全量或隐藏测试
3. 通过 `verify` 回放验证后升级为 completed，或直接合并

### 证据包示例（目标格式）

```json
{
  "terminal": "candidate",
  "verified_scope": "tests/reporters/unittest_reporting.py",
  "unverified_scope": "full suite",
  "evidence_status": "valid",
  "runs": [
    {
      "command": "python -m pytest tests/reporters/unittest_reporting.py -q",
      "scope": "targeted",
      "coverage": "partial",
      "result": "10 passed",
      "workspace_fingerprint": "sha256:...",
      "integrity": {"test_files_unchanged": true, "ran_after_last_edit": true,
                     "nonce_verified": true, "replay_verified": true}
    }
  ]
}
```

---

## 15. FAQ

**Q：判定层能提升修 bug 能力吗？**
不能。它保证诚实与成本；能力由模型和补丁质量决定（路线 B）。

**Q：为什么不直接跑全量？**
成本高、易超时，且对隐藏测试判定没有直接贡献；候选语义已诚实标注未验证范围。

**Q：candidate 会不会变成"逃避验证"？**
候选需要编辑后定向证据 + 已执行范围内无新增失败 + 完整性通过；polluted 证据连候选都不允许。

**Q：“没有观察到新增失败”是否等于“没有回归”？**
不等于。它只覆盖已执行且可信的测试范围；未执行范围以 unverified_scope 记录。

**Q：为什么不用更强的模型？**
订阅制产品不是 API；当前只有 flash。强模型是路线 B 的能力上限问题，与本方案解耦。

---

## 16. 术语表

| 术语 | 含义 |
|---|---|
| 证据账本 | 每次测试运行的结构化记录 |
| 决策表 | Runtime 计算终态的互斥规则集合 |
| candidate | 有有效证据但验证不完整的候选补丁 |
| polluted / stale | 证据被污染或已过期，不可使用 |
| valid-incomplete | 证据有效但覆盖不完整，只能候选 |
| nonce | Runtime 生成并核验的每次运行防伪码 |
| 回放验证 | 判定前重新执行关键测试并与记录比对 |
| unverified_scope | 未被执行且可信证据覆盖的范围 |
| 影响集 | 可能受改动影响的现有测试集合 |

---

## 附录 A：决策表测试用例（目标）

1. 全量绿 + 完整性通过 → completed（即使同时存在定向绿）
2. 定向绿 + 已执行范围内无新增失败 → candidate
3. 定向绿 + 新的失败 → 不得 candidate
4. 全量运行但有失败 → 不得 completed；若新增失败则不得 candidate
5. 运行后再次编辑（旧证据）→ stale，证据作废
6. 测试文件被修改 → polluted，证据作废
7. 配置/conftest 在运行后变化 → stale，证据作废
8. 动态导入/覆盖不可证明 → valid-incomplete → 只能 candidate
9. 指纹不可计算 → 证据不可用，不得完成
10. 伪造输出（无有效 nonce）→ polluted
11. 回放结果与记录不一致 → polluted
12. baseline 不可用 + 定向绿 → candidate，记录 baseline_available=false

---

## 附录 B：与现有测试的衔接

- `tests/test_swe_cli_scope_guard.py`：范围/排除检测
- `tests/test_swe_pytest_addopts_guard.py`：PYTEST_ADDOPTS
- `tests/test_swe_public_api_guard.py`：公共接口变化
- `tests/test_swe_behavior_guard.py`：返回值/异常变化
- `tests/test_swe_edit_failure_recovery.py`：编辑失败恢复
- 新增：`tests/test_swe_evidence_decision.py`（M1）

---

## 结语

ZMAI 的竞争力不在"更聪明"，而在"更可信"：把判定权从模型手里拿走，交给可复核的证据。按本方案落地后，在同模型条件下，ZMAI 应以"最诚实、最可审计的轻量 SWE 运行时"作为定位，而不是与强模型 agent 比拼解决率。路线 A 负责可信，路线 B 负责能力，两者分开评估。
