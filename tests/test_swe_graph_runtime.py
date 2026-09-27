"""Graph Runtime（观察层）—— Node / Edge / Transition trace / 合法性校验。

设计约束（本文件测的就是这些约束）：
  * Graph 只**观察**既有控制流，不调度、不判定、不持有第二份状态；
  * ``SWEState`` 是 ``AgentContext.metadata`` 的就地视图，不是副本；
  * ``enforce=False``（生产默认）下非法转移只记录，Agent 行为逐字节不变；
  * 边表必须覆盖真实控制流 —— 一次真实运行结束后 ``graph_violations`` 必须为空。

用例分两组：
  A. 纯 GraphRuntime 单元（不跑 Agent）
  B. 真实 SWEAgent + scripted backend（真跑 pytest / 真改文件）
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from zmai.agent import AgentContext, AgentState
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.swe import graph as graph_mod
from zmai.swe.agent import SWEAgent
from zmai.swe.graph import (
    EDGES,
    GraphEdgeError,
    GraphRuntime,
    Node,
    SWEState,
    analyze_trace,
    validate_trace,
)
from zmai.tool import ToolCall, ToolRegistry

# ═══════════════════════════════════════════════════════════════════
# A. 纯 GraphRuntime
# ═══════════════════════════════════════════════════════════════════


def _runtime(meta: dict | None = None, **kw) -> tuple[GraphRuntime, dict]:
    meta = meta if meta is not None else {}
    return GraphRuntime(SWEState(meta), **kw), meta


class TestEdges:
    def test_every_node_has_an_edge_entry(self):
        """边表必须覆盖全部节点（漏一个 → 该节点的所有转移都被误判为非法）。"""
        missing = [n.value for n in Node if n not in EDGES]
        assert not missing, f"EDGES 缺少节点: {missing}"

    def test_terminal_nodes_have_no_outgoing_edges(self):
        assert EDGES[Node.DONE] == frozenset()
        assert EDGES[Node.FAILED] == frozenset()

    def test_edit_and_verify_cannot_jump_to_success(self):
        """任何代码修改/测试失败路径都不得直接到 DONE。

        "EDIT" 在本 Runtime 里不是可调度节点（动作由模型发起），它的等价物是
        TOOL（工具批）与 REVIFY 关口 COMPLETION_GATE —— 因此禁令落在这两个节点上。
        """
        for n in (Node.TOOL, Node.REGRESSION, Node.REPAIR_PLAN, Node.EDIT_RECOVERY,
                  Node.AUTO_VERIFY, Node.LOOP_GUARD, Node.READ_LIMIT,
                  Node.FIX_DRIVING, Node.REPLAN, Node.PLAN, Node.BACKEND):
            assert Node.DONE not in EDGES[n], f"{n.value} 不得直达 DONE"

    def test_only_completion_gate_and_entry_reach_done(self):
        """DONE 的唯一入口是完成门禁与 step 入口的完成硬终止判定。"""
        sources = {n for n, outs in EDGES.items() if Node.DONE in outs}
        assert sources == {Node.COMPLETION_GATE, Node.ENTRY}


class TestEnter:
    def test_legal_path_records_full_transition_fields(self):
        rt, meta = _runtime()
        rt.enter(Node.ENTRY, "step 1", step=1)
        rt.enter(Node.BACKEND, "invoke", step=1)
        rt.enter(Node.TOOL, "1 tool call(s)", step=1)
        rt.enter(Node.COMPLETION_GATE, "post_tool", step=1)
        rt.enter(Node.DONE, "post_tool_complete", step=1)

        assert rt.violations == []
        assert [t["node"] for t in rt.trace] == [
            "ENTRY", "BACKEND", "TOOL", "COMPLETION_GATE", "DONE"]
        first = rt.trace[0]
        assert first == {"step": 1, "from": None, "node": "ENTRY", "reason": "step 1"}
        assert rt.trace[-1]["from"] == "COMPLETION_GATE"
        assert rt.current is Node.DONE
        # trace 落在 metadata 上（AgentResult.metadata 复用同一条通道）
        assert meta["graph_trace"] is rt.trace

    def test_first_enter_allows_previous_none(self):
        rt, _ = _runtime()
        rt.enter(Node.FAILED, "no_backend")
        assert rt.violations == []

    def test_illegal_edge_is_recorded_but_tolerated(self):
        """默认 enforce=False：非法转移只记录，不抛错，也不拦控制流。"""
        rt, meta = _runtime()
        rt.enter(Node.TOOL, "batch")
        rt.enter(Node.DONE, "wishful thinking")

        assert len(rt.violations) == 1
        assert meta["graph_violations"][0]["prev"] == "TOOL"
        assert meta["graph_violations"][0]["node"] == "DONE"
        # current 照样前移：记录必须反映控制流真实去过哪里
        assert rt.current is Node.DONE
        assert rt.trace[-1]["node"] == "DONE"

    def test_terminal_then_anything_is_a_violation(self):
        for terminal in (Node.DONE, Node.FAILED):
            rt, _ = _runtime()
            rt.enter(Node.ENTRY, "s")
            rt.enter(terminal, "end")
            rt.enter(Node.ENTRY, "next step")
            assert len(rt.violations) == 1, terminal

    def test_enforce_true_raises(self):
        rt, meta = _runtime(enforce=True)
        rt.enter(Node.REGRESSION, "worse")
        with pytest.raises(GraphEdgeError):
            rt.enter(Node.DONE, "illegal")
        assert meta["graph_violations"][0]["node"] == "DONE"

    def test_repair_loop_edges_are_legal(self):
        """VERIFY(失败) → REPAIR_PLAN → EDIT_RECOVERY → 再验证 必须全部合法。"""
        rt, _ = _runtime()
        for node in (Node.ENTRY, Node.BACKEND, Node.TOOL, Node.REPAIR_PLAN,
                     Node.EDIT_RECOVERY, Node.COMPLETION_GATE, Node.ENTRY,
                     Node.BACKEND, Node.TOOL, Node.COMPLETION_GATE, Node.DONE):
            rt.enter(node)
        assert rt.violations == []


class TestState:
    def test_state_is_a_view_not_a_copy(self):
        meta = {"ever_modified": False}
        st = SWEState(meta)
        assert st.meta is meta
        meta["ever_modified"] = True
        meta["repair_phase"] = "plan"
        meta["regression_recoveries"] = 2
        assert st.ever_modified is True
        assert st.repair_phase == "plan"
        assert st.regression_recoveries == 2
        # 缺省值不写入 metadata（视图不得产生副作用）
        assert "tests_passed" not in meta
        assert st.tests_passed is False

    def test_trace_and_violations_are_json_serializable(self):
        """trace 要能进 AgentResult.metadata → 必须可 JSON 序列化。"""
        rt, meta = _runtime()
        rt.enter(Node.ENTRY, "s", step=1)
        rt.enter(Node.DONE, "illegal")          # ENTRY→DONE 合法；这里只验证序列化
        rt.enter(Node.ENTRY, "after terminal")  # 这条非法
        blob = json.dumps({"graph_trace": meta["graph_trace"],
                           "graph_violations": meta["graph_violations"]})
        assert '"node": "DONE"' in blob
        assert json.loads(blob)["graph_violations"][0]["node"] == "ENTRY"


# ═══════════════════════════════════════════════════════════════════
# A2. validate_trace —— 只根据 trace 校验 Loop 结构不变量
# ═══════════════════════════════════════════════════════════════════


def _tr(*nodes: Node, steps: int = 1) -> list[dict]:
    """按 GraphRuntime 的真实形态构造 trace（from 自动接上一条）。"""
    return [{"step": steps, "from": nodes[i - 1].value if i else None,
             "node": n.value, "reason": ""} for i, n in enumerate(nodes)]


def _edge(src: Node, dst: Node, *, index: int = 1) -> list[dict]:
    """构造一条"孤立"的非法转移（从 src 出发跳到 dst）。"""
    return [{"step": index, "from": None, "node": src.value, "reason": ""},
            {"step": index, "from": src.value, "node": dst.value, "reason": ""}]


class TestValidateTraceLegalShapes:
    def test_legal_repair_loop_passes(self):
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.REPAIR_PLAN,
                    Node.COMPLETION_GATE, Node.READ_LIMIT, Node.FIX_DRIVING,
                    Node.LOOP_GUARD, Node.ENTRY, Node.BACKEND, Node.TOOL,
                    Node.COMPLETION_GATE, Node.DONE)
        assert validate_trace(trace) == []

    def test_legal_regression_recovery_passes(self):
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.REGRESSION,
                    Node.REPAIR_PLAN, Node.COMPLETION_GATE, Node.READ_LIMIT,
                    Node.FIX_DRIVING, Node.LOOP_GUARD, Node.ENTRY, Node.BACKEND,
                    Node.TOOL, Node.EDIT_RECOVERY, Node.COMPLETION_GATE,
                    Node.ENTRY, Node.BACKEND, Node.TOOL, Node.COMPLETION_GATE,
                    Node.DONE)
        assert validate_trace(trace) == []

    def test_empty_trace_passes(self):
        assert validate_trace([]) == []

    def test_done_via_entry_hard_terminate_after_a_gate_passes(self):
        """step 入口的完成硬终止：其前必须已经出现过完成门禁。"""
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.COMPLETION_GATE,
                    Node.READ_LIMIT, Node.FIX_DRIVING, Node.LOOP_GUARD,
                    Node.ENTRY, Node.DONE)
        assert validate_trace(trace) == []


class TestValidateTraceViolations:
    def test_repair_plan_to_done_fails(self):
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.REPAIR_PLAN, Node.DONE)
        problems = validate_trace(trace)
        assert problems, "REPAIR_PLAN → DONE 必须被判违规"
        assert any("REPAIR_PLAN → DONE" in p for p in problems), problems

    def test_regression_to_done_fails(self):
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.REGRESSION, Node.DONE)
        assert any("REGRESSION → DONE" in p for p in validate_trace(trace))

    def test_edit_recovery_to_done_fails(self):
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.EDIT_RECOVERY, Node.DONE)
        assert any("EDIT_RECOVERY → DONE" in p for p in validate_trace(trace))

    def test_failure_path_must_pass_the_gate_again(self):
        """失败路径之后经 ENTRY 硬终止完成（绕过重新验证）→ 必须判违规。"""
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.REPAIR_PLAN,
                    Node.COMPLETION_GATE, Node.READ_LIMIT, Node.FIX_DRIVING,
                    Node.LOOP_GUARD, Node.ENTRY, Node.DONE)
        assert any("COMPLETION_GATE 重新放行" in p for p in validate_trace(trace))

    def test_reverification_after_edit_recovery_is_visible(self):
        """跨批的"恢复 → 重新验证"：EDIT_RECOVERY 之后经 ENTRY 硬终止 → 违规。"""
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.EDIT_RECOVERY,
                    Node.COMPLETION_GATE, Node.READ_LIMIT, Node.FIX_DRIVING,
                    Node.LOOP_GUARD, Node.ENTRY, Node.DONE)
        assert any("COMPLETION_GATE 重新放行" in p for p in validate_trace(trace))

    def test_same_batch_fix_then_verify_is_not_flagged(self):
        """边界（刻意不误报）：同批内 [改坏 → 改好 → 全绿] 在 trace 上与
        "改完直接完成"同形 —— 结构层无法区分二者。

        "这次验证是否覆盖了最后一次修改"是 CompletionState 的职责
        （mods_since_pass），不在本函数的判据内；此处只保证不产生假阳性。
        """
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.EDIT_RECOVERY,
                    Node.COMPLETION_GATE, Node.DONE)
        assert validate_trace(trace) == []

    def test_terminal_then_business_node_fails(self):
        for terminal in (Node.DONE, Node.FAILED):
            trace = [*_tr(Node.ENTRY, terminal),
                     {"step": 9, "from": terminal.value, "node": "ENTRY", "reason": ""}]
            problems = validate_trace(trace)
            assert any("终态" in p for p in problems), problems

    def test_illegal_edge_fails(self):
        # TOOL → LOOP_GUARD 不在 EDGES 中（工具批之后必经完成门禁）
        trace = [*_tr(Node.ENTRY, Node.BACKEND, Node.TOOL),
                 {"step": 2, "from": "TOOL", "node": "LOOP_GUARD", "reason": ""}]
        assert any("非法转移 TOOL → LOOP_GUARD" in p for p in validate_trace(trace))

    def test_unknown_node_fails(self):
        trace = [*_tr(Node.ENTRY), {"step": 2, "from": "ENTRY",
                                    "node": "NOPE", "reason": ""}]
        assert any("未知节点" in p for p in validate_trace(trace))

    def test_from_field_must_match_previous_node(self):
        trace = [_tr(Node.ENTRY)[0],
                 {"step": 1, "from": "TOOL", "node": "BACKEND", "reason": ""}]
        assert any("不一致" in p for p in validate_trace(trace))


# ═══════════════════════════════════════════════════════════════════
# B. 真实 Agent 接入
# ═══════════════════════════════════════════════════════════════════

TEXT = "I have fixed the issue."


def _write_project(tmp_path: Path) -> None:
    """初始 1 failed；`_fix_edit()` 后 1 passed（裸 pytest -q 是完整范围）。

    old_text 刻意不含正则元字符 —— edit 的 regex_replace 模式按正则解释。
    """
    (tmp_path / "bug.py").write_text(
        "VALUE = 1\n\n\ndef double(x):\n    return x * VALUE\n", encoding="utf-8")
    (tmp_path / "test_all.py").write_text(
        "import bug\n\n\ndef test_double():\n    assert bug.double(3) == 6\n",
        encoding="utf-8")


def _pytest() -> ToolCall:
    return ToolCall(id="pt", name="shell_exec",
                    params={"command": "python -m pytest -q"})


def _fix_edit() -> ToolCall:
    """真实修好 bug：VALUE 1 → 2，test_double 由失败变通过。"""
    return ToolCall(id="fix", name="edit",
                    params={"path": "bug.py", "mode": "regex_replace",
                            "old_text": "VALUE = 1", "new_text": "VALUE = 2"})


def _breaking_edit() -> ToolCall:
    """语法合法、语义破坏：模块无法 import → collection error → 灾难性退化。"""
    return ToolCall(id="brk", name="edit",
                    params={"path": "bug.py", "mode": "append",
                            "new_text": "\n_x = parse_config\n"})


def _revert_edit() -> ToolCall:
    return ToolCall(id="rev", name="write_file",
                    params={"path": "bug.py",
                            "content": "VALUE = 1\n\n\ndef double(x):\n    return x * VALUE\n"})


def _bad_edit() -> ToolCall:
    """真实 edit 失败：old_text 不存在 → 编辑没有落地 → EDIT_RECOVERY。"""
    return ToolCall(id="bad", name="edit",
                    params={"path": "bug.py", "mode": "regex_replace",
                            "old_text": "NO_SUCH_CONTENT_ANYWHERE", "new_text": "x"})


class _ScriptedBackend(Backend):
    """str → 纯文本响应（无 tool_calls）；list → 该步的工具调用。"""

    name = "graph"

    def __init__(self, script):
        self._script = script
        self._i = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None
        if self._i < len(self._script):
            calls = self._script[self._i]
        self._i += 1
        content = calls if isinstance(calls, str) else ""
        if isinstance(calls, str):
            calls = None
        return BackendResponse(content=content, tool_calls=calls, usage=TokenUsage(1, 1),
                               stop_reason="tool_use" if calls else "end_turn")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _run(tmp_path: Path, script, max_steps: int = 6):
    tmp_path.mkdir(parents=True, exist_ok=True)
    _write_project(tmp_path)
    ctx = AgentContext(
        agent_id="graph",
        task="修复 bug 使全部测试通过",
        backend=_ScriptedBackend(script),
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )
    agent = SWEAgent("graph")
    asyncio.run(agent.initialize(ctx))
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action.type)
        if action.type in ("complete", "fail"):
            # 与 Runtime.run 一致：fail 必须落到 step_failed，finalize 才能判 FAILED
            if action.type == "fail":
                ctx.metadata["step_failed"] = action.error or True
            break
    result = asyncio.run(agent.finalize(ctx))
    return ctx, actions, result


def _nodes(ctx: AgentContext) -> list[str]:
    return [t["node"] for t in ctx.metadata.get("graph_trace", [])]


def _violations(ctx: AgentContext) -> list[dict]:
    return ctx.metadata.get("graph_violations", [])


class TestRepairLoop:
    """失败 → 修复计划 → 修改 → 重测 → 完成，全程节点可观测。"""

    def test_repair_loop_nodes_and_clean_edges(self, tmp_path):
        ctx, actions, result = _run(
            tmp_path, [[_pytest()], [_fix_edit()], [_pytest()], TEXT])

        assert actions[-1] == "complete", actions
        nodes = _nodes(ctx)
        for expected in ("ENTRY", "BACKEND", "TOOL", "REPAIR_PLAN",
                         "COMPLETION_GATE", "DONE"):
            assert expected in nodes, f"缺少节点 {expected}: {nodes}"
        assert nodes[-1] == "DONE"
        # 顺序：修复计划必须在 DONE 之前、在首个 TOOL 之后
        assert nodes.index("TOOL") < nodes.index("REPAIR_PLAN") < nodes.index("DONE")
        # 边表覆盖真实控制流：一次完整运行不得出现任何非法转移
        assert _violations(ctx) == [], f"边表未覆盖真实转移: {_violations(ctx)}"

    def test_edit_then_verify_is_visible(self, tmp_path):
        """修改发生在 TOOL 批内，其后必经完成门禁 —— 不存在 EDIT→DONE 直通。"""
        ctx, _, _ = _run(tmp_path, [[_pytest()], [_fix_edit()], [_pytest()], TEXT])
        nodes = _nodes(ctx)
        assert ctx.metadata["ever_modified"] is True
        last_tool = len(nodes) - 1 - nodes[::-1].index("TOOL")
        assert nodes[last_tool + 1] == "COMPLETION_GATE", (
            f"修改后必须先过完成门禁: {nodes[last_tool:]}")


class TestRegressionRecovery:
    def test_regression_enters_recovery(self, tmp_path):
        ctx, _, _ = _run(
            tmp_path, [[_pytest()], [_breaking_edit()], [_pytest()]], max_steps=3)

        nodes = _nodes(ctx)
        assert "REGRESSION" in nodes, nodes
        assert ctx.metadata.get("needs_revert") is True
        assert ctx.metadata.get("force_edit") is True
        assert ctx.metadata["regression_recoveries"] == 1
        assert _violations(ctx) == [], _violations(ctx)

    def test_recovery_clears_revert_requirement(self, tmp_path):
        """恢复后可收集 → needs_revert 解除，且不产生第二次 REGRESSION。"""
        ctx, _, _ = _run(
            tmp_path,
            [[_pytest()], [_breaking_edit()], [_pytest()], [_revert_edit()], [_pytest()]],
            max_steps=5)

        nodes = _nodes(ctx)
        assert nodes.count("REGRESSION") == 1, nodes
        assert "needs_revert" not in ctx.metadata, "测试恢复可收集后应解除恢复要求"
        assert _violations(ctx) == [], _violations(ctx)


class TestTerminalTransitions:
    def test_completion_reaches_done(self, tmp_path):
        ctx, actions, result = _run(
            tmp_path, [[_fix_edit()], [_pytest()], TEXT])

        assert _nodes(ctx)[-1] == "DONE"
        assert result.status is AgentState.COMPLETED
        assert ctx.metadata["graph_trace"][-1]["reason"] == "post_tool_complete"

    def test_blocked_completion_ends_failed(self, tmp_path):
        """零证据纯文本反复被门禁拦截 → 有界 FAILED（不得伪装成 timeout）。"""
        ctx, actions, result = _run(tmp_path, [TEXT] * 8, max_steps=8)

        assert actions[-1] == "fail", actions
        assert _nodes(ctx)[-1] == "FAILED"
        assert result.status is AgentState.FAILED
        assert ctx.metadata["completion_block_count"] > 3
        assert _violations(ctx) == [], _violations(ctx)

    def test_graph_state_matches_agent_state(self, tmp_path):
        """图末节点与 AgentResult 状态一致（两个终态各测一次）。"""
        ctx_ok, _, res_ok = _run(tmp_path, [[_fix_edit()], [_pytest()], TEXT])
        assert _nodes(ctx_ok)[-1] == "DONE" and res_ok.status is AgentState.COMPLETED
        assert ctx_ok.metadata["graph_trace"][-1]["node"] == "DONE"

        ctx_bad, _, res_bad = _run(tmp_path, [TEXT] * 8, max_steps=8)
        assert _nodes(ctx_bad)[-1] == "FAILED" and res_bad.status is AgentState.FAILED

    def test_trace_is_exposed_on_agent_result(self, tmp_path):
        _, _, result = _run(tmp_path, [[_fix_edit()], [_pytest()], TEXT])
        assert result.metadata["graph_trace"][-1]["node"] == "DONE"
        assert result.metadata["graph_violations"] == []
        json.dumps(result.metadata)  # AgentResult.metadata 必须可序列化


class TestRealTracesSatisfyInvariants:
    """真实运行产生的 trace 必须通过结构不变量校验（0 违规）。"""

    def test_real_completion_trace_passes(self, tmp_path):
        ctx, actions, _ = _run(tmp_path, [[_pytest()], [_fix_edit()], [_pytest()], TEXT])
        assert actions[-1] == "complete", actions
        assert validate_trace(ctx.metadata["graph_trace"]) == []

    def test_real_failure_trace_passes(self, tmp_path):
        ctx, actions, _ = _run(tmp_path, [TEXT] * 8, max_steps=8)
        assert actions[-1] == "fail", actions
        assert validate_trace(ctx.metadata["graph_trace"]) == []

    def test_real_regression_recovery_trace_passes(self, tmp_path):
        ctx, _, _ = _run(
            tmp_path,
            [[_pytest()], [_breaking_edit()], [_pytest()], [_revert_edit()], [_pytest()]],
            max_steps=5)
        assert "REGRESSION" in _nodes(ctx)
        assert validate_trace(ctx.metadata["graph_trace"]) == []

    def test_validator_catches_an_injected_illegal_trace(self, tmp_path):
        """在**真实 trace** 上删掉 DONE 前的那次完成门禁 → 校验器必须抓住。"""
        ctx, _, _ = _run(tmp_path, [[_pytest()], [_fix_edit()], [_pytest()], TEXT])
        trace = list(ctx.metadata["graph_trace"])
        idx = max(i for i, t in enumerate(trace) if t["node"] == "COMPLETION_GATE")
        trace.pop(idx)
        trace[idx]["from"] = trace[idx - 1]["node"]   # 重新接上被删节点后的前驱
        problems = validate_trace(trace)
        assert problems, "删掉完成门禁后必须判违规"
        assert any("DONE" in p and "非法" in p for p in problems), problems


class TestObserveOnly:
    def test_enforce_false_does_not_change_behaviour(self, tmp_path, monkeypatch):
        """把边表整体清空（所有转移都"非法"）后，运行结果必须一字不变。"""
        baseline_ctx, baseline_actions, baseline_res = _run(
            tmp_path / "base", [[_pytest()], [_fix_edit()], [_pytest()], TEXT])

        monkeypatch.setattr(graph_mod, "EDGES", {})
        ctx, actions, res = _run(
            tmp_path / "stripped", [[_pytest()], [_fix_edit()], [_pytest()], TEXT])

        assert actions == baseline_actions == ["continue", "continue", "complete"]
        assert res.status is baseline_res.status is AgentState.COMPLETED
        # 边表被清空 → 除首次进入外全部记为违规，但行为毫无变化
        assert len(_violations(ctx)) > 3
        assert ctx.metadata["completion"].tests_complete is True
        assert ctx.metadata["test_success_count"] == baseline_ctx.metadata["test_success_count"]
        assert _nodes(ctx) == _nodes(baseline_ctx)


# ═══════════════════════════════════════════════════════════════════
# C. Execution Trajectory —— analyze_trace
# ═══════════════════════════════════════════════════════════════════


def _traj(ctx: AgentContext) -> dict:
    return analyze_trace(ctx.metadata["graph_trace"],
                         violations=ctx.metadata.get("graph_violations"))


class TestTrajectoryShape:
    def test_empty_trace(self):
        t = analyze_trace([])
        assert t["nodes"] == [] and t["transitions"] == 0
        assert t["start_node"] is None and t["end_node"] is None
        assert t["terminal"] is False and t["terminal_type"] is None
        assert t["terminal_reason"] is None
        assert t["step_count"] == 0 and t["unique_nodes"] == []
        assert t["loop_count"] == 0 and t["recovery_count"] == 0
        assert t["violations"] == [] and t["invariant_problems"] == []

    def test_counts_and_unique_nodes(self):
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.COMPLETION_GATE,
                    Node.ENTRY, Node.BACKEND, Node.TOOL, Node.REPAIR_PLAN,
                    Node.COMPLETION_GATE, Node.DONE)
        for i, entry in enumerate(trace):
            entry["step"] = 1 if i < 4 else 2
        t = analyze_trace(trace)

        assert t["transitions"] == 10 and len(t["nodes"]) == 10
        assert t["step_count"] == 2
        assert t["loop_count"] == 1                      # 第二次 ENTRY = 回到循环
        assert t["tool_batches"] == 2
        assert t["completion_gate_visits"] == 2
        assert t["unique_nodes"] == ["ENTRY", "BACKEND", "TOOL", "COMPLETION_GATE",
                                     "REPAIR_PLAN", "DONE"]
        assert t["start_node"] == "ENTRY" and t["end_node"] == "DONE"
        assert t["terminal"] is True and t["terminal_type"] == "DONE"
        assert t["recovery_count"] == 0

    def test_violations_passthrough_and_invariant_problems(self):
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.REPAIR_PLAN, Node.DONE)
        sentinel = [{"prev": "REPAIR_PLAN", "node": "DONE", "reason": "x", "step": 1}]
        t = analyze_trace(trace, violations=sentinel)
        assert t["violations"] is not sentinel and t["violations"] == sentinel
        assert any("REPAIR_PLAN → DONE" in p for p in t["invariant_problems"])

    def test_multiple_recoveries_are_counted(self):
        """两次退化 + 一次编辑恢复 → recovery_count 3。"""
        trace = _tr(Node.ENTRY, Node.BACKEND, Node.TOOL, Node.REGRESSION,
                    Node.COMPLETION_GATE, Node.ENTRY, Node.BACKEND, Node.TOOL,
                    Node.EDIT_RECOVERY, Node.COMPLETION_GATE, Node.ENTRY,
                    Node.BACKEND, Node.TOOL, Node.REGRESSION, Node.COMPLETION_GATE,
                    Node.DONE)
        t = analyze_trace(trace)
        assert t["regression_count"] == 2
        assert t["edit_recovery_count"] == 1
        assert t["recovery_count"] == 3
        assert t["loop_count"] == 2
        assert t["invariant_problems"] == []


class TestTrajectoryScenarios:
    """真实运行：四种可区分的执行形态必须被 trajectory 区分开。"""

    def test_normal_completion(self, tmp_path):
        ctx, actions, _ = _run(tmp_path, [[_fix_edit()], [_pytest()], TEXT])
        t = _traj(ctx)
        assert actions[-1] == "complete"
        assert t["terminal_type"] == "DONE"
        assert t["repair_plan_count"] == 0 and t["regression_count"] == 0
        assert t["edit_recovery_count"] == 0
        assert t["terminal_reason"] == "post_tool_complete"
        assert t["invariant_problems"] == [] and t["violations"] == []

    def test_test_failure_then_repair_then_done(self, tmp_path):
        ctx, actions, _ = _run(tmp_path, [[_pytest()], [_fix_edit()], [_pytest()], TEXT])
        t = _traj(ctx)
        assert actions[-1] == "complete"
        assert t["repair_plan_count"] >= 1, t["nodes"]
        assert t["terminal_type"] == "DONE"
        assert t["regression_count"] == 0
        # 与"正常完成"的可区分点就是 repair_plan_count
        assert t["loop_count"] >= 2

    def test_regression_then_recovery(self, tmp_path):
        ctx, _, _ = _run(
            tmp_path,
            [[_pytest()], [_breaking_edit()], [_pytest()], [_revert_edit()], [_pytest()]],
            max_steps=5)
        t = _traj(ctx)
        assert t["regression_count"] == 1
        assert t["recovery_count"] >= 1
        assert t["repair_plan_count"] >= 1
        assert t["invariant_problems"] == []

    def test_edit_failure_then_recovery(self, tmp_path):
        """真实 edit 失败（old_text 不存在）→ EDIT_RECOVERY → 改好 → 完成。"""
        ctx, _, _ = _run(
            tmp_path,
            [[_pytest(), _bad_edit()], [_fix_edit()], [_pytest()], TEXT])
        t = _traj(ctx)
        assert t["edit_recovery_count"] == 1, t["nodes"]
        assert t["terminal_type"] == "DONE"
        assert t["regression_count"] == 0
        assert t["invariant_problems"] == []

    def test_final_failure(self, tmp_path):
        ctx, actions, _ = _run(tmp_path, [TEXT] * 8, max_steps=8)
        t = _traj(ctx)
        assert actions[-1] == "fail"
        assert t["terminal"] is True and t["terminal_type"] == "FAILED"
        assert t["terminal_reason"] == "completion_gate_blocked"
        assert t["loop_count"] == ctx.metadata["completion_block_count"] - 1
        assert t["invariant_problems"] == []

    def test_scenario_signatures_are_distinguishable(self, tmp_path):
        """四种形态的 trajectory 签名互不相同（同一次断言里对照）。"""
        normal = _traj(_run(tmp_path / "a", [[_fix_edit()], [_pytest()], TEXT])[0])
        repair = _traj(_run(tmp_path / "b", [[_pytest()], [_fix_edit()], [_pytest()],
                                             TEXT])[0])
        bad_edit = _traj(_run(tmp_path / "c", [[_pytest(), _bad_edit()], [_fix_edit()],
                                               [_pytest()], TEXT])[0])
        failed = _traj(_run(tmp_path / "d", [TEXT] * 8, max_steps=8)[0])

        def sig(t: dict) -> tuple:
            return (t["terminal_type"], t["repair_plan_count"],
                    t["edit_recovery_count"], t["regression_count"])

        assert sig(normal) == ("DONE", 0, 0, 0)
        assert sig(repair) == ("DONE", 1, 0, 0)
        assert sig(bad_edit) == ("DONE", 1, 1, 0)
        assert sig(failed)[0] == "FAILED"
        assert len({sig(normal), sig(repair), sig(bad_edit)}) == 3
