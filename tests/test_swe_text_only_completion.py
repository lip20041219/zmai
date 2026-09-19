"""P1-4：非 eval 模式下，改过代码但从未形成有效验证，纯文本不得 complete。

修复前，纯文本完成路径的门禁只认"测试曾经失败"（tests_ever_failed）：

    _needs_retest = tests_ever_failed and not completion.tests_complete

于是"改过代码但测试从未失败"完全没有门禁 —— 改完直接回一段纯文本就能 complete：

  A  改过代码 + 从未跑过测试   → complete（错误）
  C  改过代码 + 只跑了子集     → 也没有门禁（此前仅被 _auto_verify 偶然挡住）
  E  同一步内 [全绿, 修改]     → 同上

修复后判据复用既有状态：只要**项目本身有测试**且代码被修改过，就必须存在一次
"未被后续修改作废的完整套件全绿"（completion.tests_complete）才能完成。

无测试的项目不受这条门禁约束（测试不是它的验收标准），保持既有行为 —— 否则
"写个脚本"这类任务会被永久卡死。

全部用例走真实 Runtime + 真实工具（真写文件、真跑 pytest），backend 只决定下一步。
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
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolCall, ToolRegistry

GREEN_BUG = "VALUE = 1\n"
GREEN_TEST = "import bug\n\n\ndef test_value():\n    assert bug.VALUE == 1\n"
TEXT = "I have fixed the issue."


def _write_project(tmp_path: Path) -> None:
    """两个测试文件 → 裸 `pytest -q` 收 2 个，`pytest -q test_all.py` 是子集（1 个）。"""
    (tmp_path / "bug.py").write_text(GREEN_BUG, encoding="utf-8")
    (tmp_path / "test_all.py").write_text(GREEN_TEST, encoding="utf-8")
    (tmp_path / "test_extra.py").write_text(
        GREEN_TEST.replace("test_value", "test_value2"), encoding="utf-8")


def _write_notest_project(tmp_path: Path) -> None:
    """没有任何测试文件 —— 测试不是这个任务的验收标准。"""
    (tmp_path / "greet.py").write_text(
        'def greet():\n    return "hi"\n', encoding="utf-8")


def _pytest(target: str = "") -> ToolCall:
    cmd = f"python -m pytest -q {target}".strip()
    return ToolCall(id=cmd, name="shell_exec", params={"command": cmd})


def _touch_edit() -> ToolCall:
    """真实改写 bug.py：内容变了，测试仍通过。"""
    return ToolCall(id="touch", name="edit",
                    params={"path": "bug.py", "mode": "regex_replace",
                            "old_text": "VALUE = 1", "new_text": "VALUE = 1  # touched"})


def _break_edit() -> ToolCall:
    """真实改写 bug.py → 测试变失败。"""
    return ToolCall(id="brk", name="edit",
                    params={"path": "bug.py", "mode": "regex_replace",
                            "old_text": "VALUE = 1", "new_text": "VALUE = 0"})


def _write_greet() -> ToolCall:
    return ToolCall(id="w", name="write_file",
                    params={"path": "greet.py",
                            "content": 'def greet():\n    return "hello"\n'})


class _ScriptedBackend(Backend):
    """脚本项为 str → 真正的纯文本响应（无 tool_calls）。

    这是本 P1 的关键：必须走"模型只回文本"的完成路径。若 backend 对 str 抛异常，
    走的会是 backend 异常路径，测不到 completion guard。
    """

    name = "text_only"

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


def _messages_text(ctx: AgentContext) -> str:
    return "\n".join(
        (m.get("content", "") if isinstance(m, dict) else str(m))
        for m in ctx.metadata.get("messages", [])
    )


def _run(tmp_path: Path, script, max_steps: int = 6, project=_write_project):
    project(tmp_path)
    ctx = AgentContext(
        agent_id="p1_4",
        task="修复 bug 使全部测试通过",
        backend=_ScriptedBackend(script),
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )
    agent = SWEAgent("p1_4")
    asyncio.run(agent.initialize(ctx))
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action.type)
        if action.type in ("complete", "fail"):
            break
    return ctx, actions


# ── A：改过代码 + 从未测试 → 不得 complete ──────────────────────
def test_edit_then_text_only_cannot_complete(tmp_path):
    """修改后从未跑过测试，纯文本不得 complete（修复前的核心漏洞）。"""
    ctx, actions = _run(tmp_path, [[_touch_edit()], TEXT])

    assert "complete" not in actions, f"改过代码但从未验证，不得完成: {actions}"
    assert ctx.metadata.get("ever_modified") is True, "edit 必须真实生效"
    assert ctx.metadata["completion"].tests_complete is False
    assert "python -m pytest -q" in _messages_text(ctx), (
        "应明确要求跑完整测试套件做验证"
    )


# ── B：改过代码 + 测试失败 → 不得 complete ──────────────────────
def test_edit_then_failed_then_text_only_cannot_complete(tmp_path):
    ctx, actions = _run(tmp_path, [[_break_edit()], [_pytest()], TEXT])

    assert "complete" not in actions, f"测试失败后不得完成: {actions}"
    assert ctx.metadata["completion"].tests_complete is False


# ── C：改过代码 + 子集 partial green → 不得 complete ────────────
def test_edit_then_partial_green_then_text_only_cannot_complete(tmp_path):
    """子集全绿不是有效验证：它没有覆盖完整套件。"""
    ctx, actions = _run(tmp_path, [[_touch_edit()], [_pytest("test_all.py")], TEXT])

    assert "complete" not in actions, f"子集全绿不得作为完成依据: {actions}"
    assert ctx.metadata["completion"].tests_complete is False
    assert ctx.metadata.get("test_scope_incomplete") is True


# ── D：改过代码 + 完整套件全绿 → 可以 complete ──────────────────
def test_edit_then_full_green_then_text_only_completes(tmp_path):
    """修改后完成完整套件验证 → 保持既有正常完成行为。"""
    ctx, actions = _run(tmp_path, [[_touch_edit()], [_pytest()], TEXT])

    assert actions[-1] == "complete", f"修改后完整套件全绿应可完成: {actions}"
    assert ctx.metadata["completion"].tests_complete is True
    assert ctx.metadata["test_success_count"] == 1


# ── E：全绿之后同一步再修改 → 不得 complete ─────────────────────
def test_green_then_edit_same_step_then_text_only_cannot_complete(tmp_path):
    """同一步内 [全绿, 修改]：旧 green 已被修改作废，纯文本不得完成。"""
    ctx, actions = _run(tmp_path, [[_pytest(), _touch_edit()], TEXT])

    assert "complete" not in actions, f"green 已被后续修改作废: {actions}"
    comp = ctx.metadata["completion"]
    assert comp.tests_complete is False and comp.tests_passed is False
    assert ctx.metadata.get("test_success_count", 0) == 0


# ── F：零修改 + 非 eval 的既有语义不回归 ────────────────────────
def test_zero_modification_text_only_preserves_behavior(tmp_path):
    """零修改、测试从未失败：保持既有行为（本 P1 不改变这条路径）。"""
    ctx, actions = _run(tmp_path, [TEXT])

    assert actions[-1] == "complete", f"零修改的既有语义不得改变: {actions}"
    assert ctx.metadata.get("ever_modified") is not True


# ── 作用域护栏：无测试的项目不得被新门禁卡死 ────────────────────
def test_project_without_tests_can_still_complete(tmp_path):
    """项目没有任何测试 → 测试不是验收标准，不得被"必须重测"永久阻塞。

    这条门禁若不加作用域，`write_file` 之后任何纯文本完成都会被拦成 fail ——
    "写个脚本"这类非测试任务会被整体卡死。
    """
    ctx, actions = _run(tmp_path, [[_write_greet()], TEXT],
                        project=_write_notest_project)

    assert actions[-1] == "complete", f"无测试项目不得被新门禁卡死: {actions}"
    assert ctx.metadata.get("ever_modified") is True
    assert ctx.metadata["repo_info"].test_files == []
