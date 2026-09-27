"""SWE Loop 的显式 Graph 建模 —— **观察层**，不是调度器。

现状（审计结论）：ZMAI 的 SWE Loop 真实存在，但只以 ``agent.py`` 里
``SWEAgent.step()`` 的顺序 if/elif 块隐式表达；状态散落在
``AgentContext.metadata`` / ``CompletionState`` / ``ContextManager`` / ``LoopGuard``。

本模块只做三件事：

  1. 给控制流真实到达的决策点贴上 ``Node`` 标签；
  2. 用 ``EDGES`` 把"合法转移"变成**数据**（可断言、可穷举）；
  3. 把实际走过的转移记录成 ``graph_trace``（进 AgentResult.metadata）。

**不做的事**（刻意）：不调用模型/工具/测试，不做完成判定，不持有状态副本，
不重排 ``step()`` 里的任何 if/elif。``SWEState`` 是 ``AgentContext.metadata`` 的
就地视图 —— 真相源仍是那份 dict。

enforce 语义：
  * ``enforce=False``（默认，生产路径）：非法转移只写进 ``graph_violations``，
    Agent 行为完全不变（见 tests/test_swe_graph_runtime.py 的"不改变行为"用例）。
  * ``enforce=True``（测试/审计）：非法转移抛 ``GraphEdgeError``，用于证明边表
    覆盖了真实控制流。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class GraphEdgeError(RuntimeError):
    """enforce=True 时出现了 EDGES 未声明的转移。"""


class Node(str, Enum):
    """SWE Loop 的决策节点。取值与 agent.py 中真实控制流位置一一对应。"""

    ENTRY = "ENTRY"                    # step() 入口（含完成硬终止判定）
    PLAN = "PLAN"                      # auto_plan 生成执行计划
    BACKEND = "BACKEND"                # 模型调用（含重试）
    TOOL = "TOOL"                      # 工具批处理（观察模型动作的副作用）
    REGRESSION = "REGRESSION"          # 判定为退化（含 module_broken 恢复路径）
    REPAIR_PLAN = "REPAIR_PLAN"        # 失败分析 + 修复计划注入
    EDIT_RECOVERY = "EDIT_RECOVERY"    # edit 失败/语法失败的定向恢复
    READ_LIMIT = "READ_LIMIT"          # 只读不测的上限
    FIX_DRIVING = "FIX_DRIVING"        # 测试失败后只读不修的强制修改
    LOOP_GUARD = "LOOP_GUARD"          # 重复无进展动作检测
    REPLAN = "REPLAN"                  # 计划未执行完的重新规划
    AUTO_VERIFY = "AUTO_VERIFY"        # 纯文本回合的客观验证
    COMPLETION_GATE = "COMPLETION_GATE"  # 完成门禁（tool 后 / 纯文本后）
    DONE = "DONE"                      # 终态：完成
    FAILED = "FAILED"                  # 终态：显式失败

    @property
    def is_terminal(self) -> bool:
        return self in (Node.DONE, Node.FAILED)


# ── 合法转移表 ────────────────────────────────────────────────────────
# 每一条都对应 agent.py 中真实存在的控制流（括号内为行号，审计时的快照）。
#
# 语义约定：一次 enter() = "控制流到达了这个决策点"。同一步内可以有多个节点
# 依次到达；步与步之间回到 ENTRY。
EDGES: dict[Node, frozenset[Node]] = {
    # step 入口 → (可选)计划 → 模型调用；完成硬终止 → DONE；无 backend → FAILED
    Node.ENTRY: frozenset({Node.PLAN, Node.BACKEND, Node.DONE, Node.FAILED}),
    Node.PLAN: frozenset({Node.BACKEND, Node.FAILED}),
    # 模型调用 → 工具批 / 纯文本路径（重规划 / 客观验证）；调用失败 → FAILED
    Node.BACKEND: frozenset({Node.TOOL, Node.REPLAN, Node.AUTO_VERIFY, Node.FAILED}),
    # 工具批：批内观察节点（退化 / 修复计划 / 编辑恢复）可互相衔接（一批可有多个工具），
    # 之后必到完成门禁。
    Node.TOOL: frozenset({Node.REGRESSION, Node.REPAIR_PLAN, Node.EDIT_RECOVERY,
                          Node.COMPLETION_GATE}),
    Node.REGRESSION: frozenset({Node.REGRESSION, Node.REPAIR_PLAN, Node.EDIT_RECOVERY,
                                Node.COMPLETION_GATE}),
    Node.REPAIR_PLAN: frozenset({Node.REPAIR_PLAN, Node.REGRESSION, Node.EDIT_RECOVERY,
                                 Node.COMPLETION_GATE}),
    Node.EDIT_RECOVERY: frozenset({Node.EDIT_RECOVERY, Node.REGRESSION, Node.REPAIR_PLAN,
                                   Node.COMPLETION_GATE}),
    # 完成门禁（tool 后）：放行 → DONE；预算耗尽 → FAILED；拦截 → 下一步 ENTRY；
    # 未返回则继续走只读上限。
    Node.COMPLETION_GATE: frozenset({Node.DONE, Node.FAILED, Node.ENTRY, Node.READ_LIMIT}),
    # 只读上限：命中 → cont（下一步 ENTRY）；未命中 → 继续到 FixDriving
    Node.READ_LIMIT: frozenset({Node.ENTRY, Node.FIX_DRIVING}),
    Node.FIX_DRIVING: frozenset({Node.ENTRY, Node.LOOP_GUARD}),
    Node.LOOP_GUARD: frozenset({Node.ENTRY}),
    # 纯文本路径：重规划 → cont / 落到客观验证
    Node.REPLAN: frozenset({Node.ENTRY, Node.AUTO_VERIFY}),
    Node.AUTO_VERIFY: frozenset({Node.COMPLETION_GATE, Node.ENTRY, Node.FAILED}),
    # 终态：无出边（任何 DONE/FAILED 之后的 enter 都是非法转移）
    Node.DONE: frozenset(),
    Node.FAILED: frozenset(),
}


@dataclass
class SWEState:
    """``AgentContext.metadata`` 的**就地视图** —— 不复制、不迁移、不新增真相源。

    图侧只读这些键做记录与断言；写入仍由 agent.py 原有代码负责。
    """

    meta: dict[str, Any]

    @property
    def ever_modified(self) -> bool:
        return bool(self.meta.get("ever_modified"))

    @property
    def repair_phase(self) -> str:
        return str(self.meta.get("repair_phase", "idle"))

    @property
    def test_failed(self) -> bool:
        return bool(self.meta.get("test_failed"))

    @property
    def repair_plan_injected(self) -> bool:
        return bool(self.meta.get("repair_plan_injected"))

    @property
    def force_edit(self) -> bool:
        return bool(self.meta.get("force_edit"))

    @property
    def needs_revert(self) -> bool:
        return bool(self.meta.get("needs_revert"))

    @property
    def tests_passed(self) -> bool:
        return bool(self.meta.get("tests_passed"))

    @property
    def tests_ever_failed(self) -> bool:
        return bool(self.meta.get("tests_ever_failed"))

    @property
    def test_success_count(self) -> int:
        return int(self.meta.get("test_success_count", 0) or 0)

    @property
    def regression_recoveries(self) -> int:
        return int(self.meta.get("regression_recoveries", 0) or 0)

    @property
    def loop_recovery_count(self) -> int:
        return int(self.meta.get("loop_recovery_count", 0) or 0)

    @property
    def completion_block_count(self) -> int:
        return int(self.meta.get("completion_block_count", 0) or 0)

    @property
    def trace(self) -> list[dict[str, Any]]:
        return self.meta.setdefault("graph_trace", [])

    @property
    def violations(self) -> list[dict[str, Any]]:
        return self.meta.setdefault("graph_violations", [])


@dataclass
class Transition:
    """一次真实发生的转移。"""

    prev: Node | None
    node: Node
    reason: str = ""
    step: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "from": self.prev.value if self.prev else None,
            "node": self.node.value,
            "reason": self.reason,
        }


# ── trace 结构校验（纯函数，见 validate_trace）─────────────────────────

#: 失败/恢复类节点：它们之后必须重新经过完成门禁才能 DONE。
#: 只收"真的意味着出过问题"的节点 —— FIX_DRIVING / READ_LIMIT 是在**每个**工具步
#: 都会被观察到的判定点，把它们计入会让"是否发生过失败"永远为真。
_FAILURE_PATH: tuple[Node, ...] = (
    Node.REGRESSION, Node.REPAIR_PLAN, Node.EDIT_RECOVERY)

_TERMINAL: tuple[Node, ...] = (Node.DONE, Node.FAILED)

#: DONE 的非门禁入边 → 违反哪条不变量
_DONE_ENTRY_VIOLATION: dict[Node, str] = {
    Node.REPAIR_PLAN: "修复计划之后必须重新修改并验证",
    Node.REGRESSION: "退化之后必须回到后续 Loop 重新验证",
    Node.EDIT_RECOVERY: "编辑失败恢复之后必须重新走验证路径",
    Node.TOOL: "工具批不得绕过完成门禁",
    Node.LOOP_GUARD: "循环恢复不得绕过完成门禁",
    Node.FIX_DRIVING: "强制修改之后必须重新验证",
    Node.READ_LIMIT: "只读上限之后必须重新验证",
    Node.AUTO_VERIFY: "客观验证必须经完成门禁收口",
    Node.REPLAN: "重新规划不得绕过完成门禁",
    Node.PLAN: "计划阶段不得直接完成",
    Node.BACKEND: "模型调用不得直接完成",
    Node.FAILED: "失败终态之后不得再完成",
}


def validate_trace(trace: list[dict[str, Any]]) -> list[str]:
    """只根据 ``graph_trace`` 校验 SWE Loop 的**结构不变量**。

    返回违规描述列表（空列表 = 通过）。**纯函数**：不读不写 Agent 状态，
    不重算 completion / verification / regression / LoopGuard，也不评价
    "这次运行好不好"。它只回答一个问题：

        实际发生的 transition，是否违反 Loop 的结构不变量？

    刻意划出的边界：trace 里看不到"源码是否真的被改过""测试是否真的全绿"
    —— 那些仍是 ``CompletionState`` / ``verifier`` 的唯一真相源。此处只做
    **结构代理**检查（例如"失败路径之后必须重新经过完成门禁才能 DONE"）。

    校验的不变量：
      1. DONE 必须由 COMPLETION_GATE 放行（ENTRY 的完成硬终止也必须在
         此前出现过完成门禁）
      2. REPAIR_PLAN → DONE 非法
      3. REGRESSION → DONE 非法
      4. EDIT_RECOVERY → DONE 非法
      5/6. 失败/恢复路径之后的 DONE 不得绕过重新验证（由 1 的非门禁入边收口）
      7/8. DONE / FAILED 之后不得再出现任何节点
      9. 不得出现未知 Node
      10. 每条 transition 必须存在于 ``EDGES``（含 from 字段自洽）
    """
    problems: list[str] = []
    if not trace:
        return problems

    # ── 解析 + 不变量 9（未知节点）──────────────────────────────
    nodes: list[Node | None] = []
    for i, t in enumerate(trace):
        try:
            nodes.append(Node(t.get("node")))
        except ValueError:
            nodes.append(None)
            problems.append(f"[{i}] 未知节点 {t.get('node')!r}：不在 Node 枚举中")

    # ── from 字段自洽（不变量 10 的前提）────────────────────────
    for i, t in enumerate(trace):
        if i == 0:
            if t.get("from") is not None:
                problems.append(f"[0] 首条 transition 的 from 必须为 None，"
                                f"实际 {t.get('from')!r}")
            continue
        expect = nodes[i - 1].value if nodes[i - 1] is not None else None
        if t.get("from") != expect:
            problems.append(f"[{i}] from={t.get('from')!r} 与上一条 node={expect!r} 不一致")

    # ── 不变量 10：每条 transition 必须存在于 EDGES ──────────────
    for i in range(1, len(nodes)):
        prev, cur = nodes[i - 1], nodes[i]
        if prev is None or cur is None:
            continue
        if cur not in EDGES.get(prev, frozenset()):
            problems.append(f"[{i}] 非法转移 {prev.value} → {cur.value}（不在 EDGES 中）")

    # ── 不变量 7/8：终态之后不得再出现节点 ──────────────────────
    for i, n in enumerate(nodes):
        if n in _TERMINAL and i != len(nodes) - 1:
            nxt = nodes[i + 1]
            problems.append(
                f"[{i}] {n.value} 是终态，其后不得再出现节点"
                f"（next={nxt.value if nxt else trace[i + 1].get('node')!r}）")
            break

    # ── 不变量 1/2/3/4/5/6：DONE 的入边 ────────────────────────
    gate_seen = False
    failure_seen = False
    for i, n in enumerate(nodes):
        if n is Node.COMPLETION_GATE:
            gate_seen = True
        elif n in _FAILURE_PATH:
            failure_seen = True
        elif n is Node.DONE:
            prev = nodes[i - 1] if i else None
            if prev is Node.COMPLETION_GATE:
                continue                              # 正常：门禁放行
            if prev is Node.ENTRY and gate_seen and not failure_seen:
                continue                              # step 入口完成硬终止（前置门禁已过）
            if prev in _DONE_ENTRY_VIOLATION:
                problems.append(f"[{i}] {prev.value} → DONE 非法："
                                f"{_DONE_ENTRY_VIOLATION[prev]}")
            elif prev is Node.ENTRY and not gate_seen:
                problems.append(f"[{i}] ENTRY → DONE 之前没有任何 COMPLETION_GATE："
                                "完成硬终止缺少前置验证")
            elif prev is Node.ENTRY:
                problems.append(f"[{i}] ENTRY → DONE 之前出现过失败/恢复路径："
                                "必须由 COMPLETION_GATE 重新放行")
            else:
                problems.append(f"[{i}] DONE 由 {prev.value if prev else 'None'} 进入"
                                "：必须由 COMPLETION_GATE 放行")

    return problems


def analyze_trace(trace: list[dict[str, Any]],
                  *, violations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """把 ``graph_trace`` 汇总成一次执行的 **Execution Trajectory** 统计。

    纯函数：只读 trace，不改 Agent 状态，也**不重新判断**
    "测试是否真的通过 / completion 是否满足 / regression 是否成立 /
    修改是否真实发生" —— 那些仍是 Agent / ``CompletionState`` / verifier 的结论。
    这里只回答"发生了什么"。

    可回答的问题：走过哪些节点、循环了几次、进入过几次修复计划 / 退化 /
    编辑恢复、最终在哪个节点、以什么原因结束、是否违反结构不变量。

    已知表达不了的两件事（trace 里没有该项信息，不额外改动 agent.py）：
      * LoopGuard 的 **recovery 次数**：``LOOP_GUARD`` 在每个工具步都会被观察到
        （它是判定点，不是"发生了恢复"的证据），``loop_guard_checks`` 只能给出
        "被检查了几次"。
      * 同理 ``READ_LIMIT`` / ``FIX_DRIVING`` 是否**真的触发**。
    """
    raw = [t.get("node") for t in trace]
    steps = [t.get("step") for t in trace]
    end = raw[-1] if raw else None
    terminal_type = end if end in (Node.DONE.value, Node.FAILED.value) else None

    def _count(node: Node) -> int:
        return raw.count(node.value)

    repair_plan_count = _count(Node.REPAIR_PLAN)
    regression_count = _count(Node.REGRESSION)
    edit_recovery_count = _count(Node.EDIT_RECOVERY)
    tool_batches = _count(Node.TOOL)
    auto_verify_visits = _count(Node.AUTO_VERIFY)

    unique_nodes: list[str] = []
    for n in raw:
        if n not in unique_nodes:
            unique_nodes.append(n)

    return {
        # ── 轨迹本体 ──
        "nodes": raw,
        "transitions": len(trace),
        "start_node": raw[0] if raw else None,
        "end_node": end,
        "terminal": terminal_type is not None,
        "terminal_type": terminal_type,
        "terminal_reason": (trace[-1].get("reason") if terminal_type else None),
        "step_count": len(set(steps)),
        "unique_nodes": unique_nodes,
        # ── 循环与恢复 ──
        "loop_count": max(_count(Node.ENTRY) - 1, 0),
        "repair_plan_count": repair_plan_count,
        "regression_count": regression_count,
        "edit_recovery_count": edit_recovery_count,
        "replan_count": _count(Node.REPLAN),
        "loop_guard_checks": _count(Node.LOOP_GUARD),
        # 只统计"有可观测证据"的恢复：REGRESSION / EDIT_RECOVERY 只在真的发生时进入。
        "recovery_count": regression_count + edit_recovery_count,
        # ── 验证 ──
        "tool_batches": tool_batches,
        "auto_verify_visits": auto_verify_visits,
        # 验证证据的产生点：工具批（跑测试/改代码）+ 纯文本回合的客观验证
        "verification_visits": tool_batches + auto_verify_visits,
        "completion_gate_visits": _count(Node.COMPLETION_GATE),
        # ── 健康度 ──
        "violations": list(violations or []),
        "invariant_problems": validate_trace(trace),
    }


class GraphRuntime:
    """记录 + 校验。**不调度、不判定、不持有第二份状态。**"""

    def __init__(self, state: SWEState, *, enforce: bool = False) -> None:
        self.state = state
        self.enforce = enforce
        self.current: Node | None = None

    @property
    def trace(self) -> list[dict[str, Any]]:
        return self.state.trace

    @property
    def violations(self) -> list[dict[str, Any]]:
        return self.state.violations

    def allows(self, prev: Node | None, node: Node) -> bool:
        """``prev → node`` 是否在 EDGES 中（``prev=None`` 表示首次进入，恒合法）。"""
        if prev is None:
            return True
        return node in EDGES.get(prev, frozenset())

    def enter(self, node: Node, reason: str = "",
              state: SWEState | None = None, step: int = 0) -> Transition:
        """记录"控制流到达 node"。

        非法转移：写入 ``graph_violations``；``enforce=True`` 时抛 GraphEdgeError。
        其余情况下 current 照样前移 —— 记录必须反映控制流**实际**去过哪里，
        否则一次误判会把后续所有合法转移都污染成违规。
        """
        st = state or self.state
        prev = self.current
        t = Transition(prev=prev, node=node, reason=reason, step=step)
        if not self.allows(prev, node):
            flag = {"prev": prev.value if prev else None,
                    "node": node.value, "reason": reason, "step": step}
            st.violations.append(flag)
            if self.enforce:
                raise GraphEdgeError(
                    f"illegal transition {flag['prev']} → {flag['node']} ({reason})")
        self.current = node
        st.trace.append(t.to_dict())
        return t
