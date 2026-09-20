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

import pytest

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
from zmai.swe.tools import is_test_command
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
def test_zero_evidence_text_only_cannot_complete(tmp_path):
    """项目**有测试**，模型零修改、零测试、零失败却声称完成 → 不得 complete。

    这条路径此前是零证据完成：`_needs_retest` 需要"改过代码或失败过"才为真，
    `_needs_change` 只在 eval 模式生效，`_auto_verify` 因无工具结果返回 None ——
    三个门禁全不触发，`AgentAction.complete` 直接放行，Runtime 没有任何独立证据。
    有测试的项目必须以"存在有效的完整套件全绿"这一正向证据为准。
    """
    ctx, actions = _run(tmp_path, [TEXT], max_steps=8)

    assert actions[-1] != "complete", f"零证据不得完成: {actions}"
    assert ctx.metadata.get("ever_modified") is not True, "本用例前提：零修改"
    assert ctx.metadata["completion"].tests_complete is False, "没有任何有效全绿"
    assert "python -m pytest" in _messages_text(ctx), "应明确要求先跑测试取得证据"


# ── G：零测试计数的"成功"命令不得被当作测试通过证据 ─────────────
def _shell(cmd: str) -> ToolCall:
    return ToolCall(id=cmd, name="shell_exec", params={"command": cmd})


def test_collect_only_cannot_complete(tmp_path):
    """`pytest --collect-only` 打印 "test session starts" 头 → verifier 判"通过"，
    但一个测试都没跑（计数为 0）。照旧记 green 会让 tests_complete 置真 ——
    用一条没跑测试的命令换到完成资格，绕过"正向证据才能完成"的门禁。
    """
    ctx, actions = _run(tmp_path, [[_shell("python -m pytest --collect-only")], TEXT],
                        max_steps=8)

    assert actions[-1] != "complete", f"零测试计数不得完成: {actions}"
    comp = ctx.metadata["completion"]
    assert comp.tests_complete is False and comp.tests_passed is False
    assert ctx.metadata.get("test_success_count", 0) == 0
    assert "python -m pytest -q" in _messages_text(ctx), "应要求跑完整套件取得计数"


def test_pytest_help_cannot_complete(tmp_path):
    """`pytest --help` 输出含裸子串 "ok" → verifier 同样判"通过"，计数仍为 0。"""
    ctx, actions = _run(tmp_path, [[_shell("python -m pytest --help")], TEXT],
                        max_steps=8)

    assert actions[-1] != "complete", f"零测试计数不得完成: {actions}"
    assert ctx.metadata["completion"].tests_complete is False
    assert ctx.metadata.get("test_success_count", 0) == 0


# ── H：P1 —— 只有真正的 runner 调用才算测试证据 ─────────────────
def test_replayed_test_log_cannot_complete(tmp_path):
    """重放旧的全绿日志不得作为完成证据。

    `pytest > pytest.log` 之后改坏代码，再重读该日志：命令文本里含 "pytest"
    （文件名），输出是旧的全绿汇总行。旧实现按裸子串 `"pytest" in cmd` 匹配，
    于是这条**没跑任何测试**的命令给出 tests_complete=True —— 代码已坏却判完成。
    """
    script = [
        [_shell("python -m pytest -q > pytest.log 2>&1")],
        [_break_edit()],
        [_shell("""python -c "print(open('pytest.log').read())" """.strip())],
        TEXT,
    ]
    ctx, actions = _run(tmp_path, script, max_steps=8)

    assert actions[-1] != "complete", f"重放旧日志不得完成: {actions}"
    assert ctx.metadata["completion"].tests_complete is False
    assert ctx.metadata.get("test_success_count", 0) == 0


def test_redirected_test_run_still_forms_evidence(tmp_path):
    """反向护栏：真实 runner 调用（含重定向形态）仍被识别为测试命令。"""
    script = [
        [_shell("python -m pytest -q > pytest.log 2>&1")],
        [_shell("python -m pytest -q")],
        TEXT,
    ]
    ctx, actions = _run(tmp_path, script, max_steps=6)

    assert actions[-1] == "complete", f"真实全绿应可完成: {actions}"
    assert ctx.metadata["completion"].tests_complete is True


@pytest.mark.parametrize("cmd", [
    "pytest",
    "pytest -q",
    "py.test",
    "nosetests",
    "python -m pytest",
    "python3 -m pytest -q",
    "py -m pytest",
    "py -3 -m pytest",
    "python -m unittest discover",
    "cd src && python -m pytest -q",
    "python -m pytest -q > pytest.log 2>&1",
    "python -m pytest -q | more",
    "poetry run pytest",
    "uv run python -m pytest",
    "FOO=1 python -m pytest -q",
    "C:\\Python311\\python.exe -m pytest",
    '"C:\\Python311\\python.exe" -m pytest',
])
def test_real_runner_invocations_are_recognized(cmd):
    assert is_test_command(cmd) is True, cmd


@pytest.mark.parametrize("cmd", [
    "type pytest.log",
    "cat pytest.ini",
    "pip install pytest",
    "echo pytest",
    "echo pytest --version",
    'python -c "import pytest"',
    "# pytest",
    "git log --grep pytest",
    "python -m pip install pytest",
    "python -c \"print(open('pytest.log').read())\"",
])
def test_non_runner_commands_are_rejected(cmd):
    assert is_test_command(cmd) is False, cmd


def test_green_evidence_still_completes(tmp_path):
    """反向护栏：真的跑出完整套件全绿后，纯文本仍应正常完成。"""
    ctx, actions = _run(tmp_path, [[_pytest()], TEXT], max_steps=6)

    assert actions[-1] == "complete", f"有全绿证据应可完成: {actions}"
    assert ctx.metadata["completion"].tests_complete is True


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
