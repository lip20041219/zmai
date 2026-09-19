"""P1：text-only 路径上 _auto_verify 持续失败必须有局部上界。

修复前：`if not vresult.passed:` 直接 return cont，而这个分支位于完成守卫**之前**，
LoopGuard 又只挂在 tool_calls 分支 —— 文本路径因此没有任何局部计数：

    text-only → auto_verify 失败 → cont → text-only → …

一路烧到 max_steps，被 Runtime 标成 timeout（`runtime.py`：max_steps 耗尽且最后一步
不是 complete/fail → `timed_out=True`），而不是一个明确的局部失败。

持续失败是怎么来的（真实成因，不是构造）：
`verifier.auto_generate_checks` 对**成功**命令的输出做裸关键词匹配
（error / fail / traceback / cannot），因此一条输出里含 "fail" 字样的**通过**运行
（例如测试名叫 `test_failure_handling`）就会生成一条永久失败的 check；
而 `_tool_results` 滑窗只在有新的工具调用时才推进，纯文本循环里它永不推进。

修复后：该分支复用完成守卫已有的 `completion_block_count` 预算
（语义相同：模型只回文本、Agent 无法推进），超过 MAX_COMPLETION_BLOCKS 即明确失败。

全部用例走真实 Runtime + 真实工具执行（真跑 pytest、真写文件）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

from zmai.agent import AgentContext
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.swe.agent import MAX_COMPLETION_BLOCKS, SWEAgent
from zmai.tool import ToolCall, ToolRegistry

# 需要 eval 守卫让"首步全绿"不在 step 入口直接完成，纯文本路径才有机会执行。
# 这也是 SWE-bench 的实际配置（require_code_change=true）。
EVAL_CFG = {"eval.require_code_change": "true"}

TEXT = "I have fixed the issue."

# 关键夹具：测试**通过**，但测试名里含 "fail" → 成功输出含 "fail"
# → auto_generate_checks 生成永久失败的 check。
PASSING_APP = "def value():\n    return 1\n"
PASSING_TEST = (
    "from app import value\n\n\n"
    "def test_failure_handling():\n"
    "    assert value() == 1\n"
)


def _write_persistent_fail_project(tmp_path: Path) -> None:
    """真实跑起来全绿，但输出里含 'fail' 字样 → _auto_verify 永久失败。"""
    (tmp_path / "app.py").write_text(PASSING_APP, encoding="utf-8")
    (tmp_path / "test_app.py").write_text(PASSING_TEST, encoding="utf-8")


def _write_clean_project(tmp_path: Path) -> None:
    """输出里不含 error/fail/traceback/cannot → _auto_verify 通过。"""
    (tmp_path / "app.py").write_text(PASSING_APP, encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "from app import value\n\n\ndef test_value():\n    assert value() == 1\n",
        encoding="utf-8",
    )


def _pytest_v() -> ToolCall:
    """-v 会打印测试名 → test_failure_handling 出现在成功输出里。"""
    return ToolCall(id="ptv", name="shell_exec",
                    params={"command": "python -m pytest -v"})


def _pytest_q() -> ToolCall:
    return ToolCall(id="ptq", name="shell_exec",
                    params={"command": "python -m pytest -q"})


def _touch_edit() -> ToolCall:
    return ToolCall(id="touch", name="edit",
                    params={"path": "app.py", "mode": "regex_replace",
                            "old_text": "return 1", "new_text": "return 1  # touched"})


class _ScriptedBackend(Backend):
    """脚本项为 str → 纯文本响应（无 tool_calls）。"""

    name = "av_bound"

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
        return BackendResponse(
            content=content, tool_calls=calls, usage=TokenUsage(1, 1),
            stop_reason="tool_use" if calls else "end_turn",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _run(tmp_path: Path, script, max_steps: int = 12,
         extra_config: dict | None = None, project=_write_persistent_fail_project):
    project(tmp_path)
    ctx = AgentContext(
        agent_id="av_bound",
        task="修复 bug 使全部测试通过",
        backend=_ScriptedBackend(script),
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50, **(extra_config or {})},
        metadata={},
    )
    agent = SWEAgent("av_bound")
    asyncio.run(agent.initialize(ctx))
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action)
        if action.type in ("complete", "fail"):
            break
    return ctx, actions


# ── A：持久 _auto_verify failure 必须有界收敛 ────────────────────
def test_persistent_autoverify_failure_converges_to_fail(tmp_path):
    """真实工具造成的持续验证失败 → 明确 fail，而不是烧到 max_steps。"""
    max_steps = 12
    ctx, actions = _run(
        tmp_path, [[_pytest_v()]] + [TEXT] * max_steps,
        max_steps=max_steps, extra_config=EVAL_CFG,
    )
    kinds = [a.type for a in actions]

    # 1) 确实发生了持续的验证失败（否则本用例空洞通过）
    vr = ctx.metadata.get("verification")
    assert vr is not None and vr.passed is False, (
        f"夹具必须造成真实的 _auto_verify 失败: {vr and vr.summary}"
    )
    assert any(c.passed is False for c in vr.checks), "应存在失败的 check"

    # 2) 明确失败收敛
    assert kinds[-1] == "fail", f"应明确失败: {kinds}"
    assert actions[-1].error and "Verification blocked" in actions[-1].error, (
        f"错误信息应说明是无进展的验证阻塞: {actions[-1].error!r}"
    )

    # 3) 不是被 max_steps 耗尽（不得是 timeout 形态）
    assert len(actions) < max_steps, f"不得耗尽 max_steps: {len(actions)}/{max_steps}"
    assert not all(k == "continue" for k in kinds), "不得全部是 continue"
    # +1 是 EvalGuard 在 step 入口的那一步 continue（它不经过本分支、不计数）。
    assert kinds.count("continue") <= MAX_COMPLETION_BLOCKS + 1, (
        f"continue 次数应受 MAX_COMPLETION_BLOCKS 约束: {kinds}"
    )


def test_autoverify_failure_shares_completion_block_budget(tmp_path):
    """复用现有预算：不新增平行计数器。"""
    ctx, actions = _run(
        tmp_path, [[_pytest_v()]] + [TEXT] * 12, max_steps=12, extra_config=EVAL_CFG,
    )
    assert ctx.metadata.get("completion_block_count") == MAX_COMPLETION_BLOCKS + 1
    assert "autoverify_block_count" not in ctx.metadata, "不得新增平行计数器"
    assert actions[-1].type == "fail"


# ── B：_auto_verify 成功路径不受影响 ────────────────────────────
def test_autoverify_success_still_completes(tmp_path):
    """验证通过 → 正常走完成判定，预算不被消耗。"""
    ctx, actions = _run(
        tmp_path,
        [[_touch_edit()], [_pytest_q()], TEXT],
        project=_write_clean_project,
    )
    assert actions[-1].type == "complete", (
        f"验证通过应正常完成: {[a.type for a in actions]}"
    )
    assert ctx.metadata.get("completion_block_count", 0) == 0, (
        "验证通过不得消耗 text-only 预算"
    )


# ── C：既有 text-only completion guard 行为不变 ─────────────────
def test_existing_text_only_guard_convergence_unchanged(tmp_path):
    """零工具结果 + 纯文本：收敛步数与既有 MAX_COMPLETION_BLOCKS 语义一致。

    这条路径由完成守卫（_needs_change）拦截，auto_verify 因无工具结果直接返回
    None 而不参与 —— 共用计数器后其收敛步数不得变化。
    """
    ctx, actions = _run(
        tmp_path, [TEXT] * 12, max_steps=12, extra_config=EVAL_CFG,
        project=_write_clean_project,
    )
    kinds = [a.type for a in actions]
    assert kinds[-1] == "fail", f"既有守卫应仍有界收敛: {kinds}"
    assert len(actions) <= MAX_COMPLETION_BLOCKS + 1, (
        f"收敛步数不得因共享计数器而变化: {len(actions)}"
    )
    assert ctx.metadata.get("completion_block_count") == MAX_COMPLETION_BLOCKS + 1
