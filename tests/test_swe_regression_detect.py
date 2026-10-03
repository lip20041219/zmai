"""Regression Detection —— 修复过程中"修改让测试变差"必须被识别并告知 Agent。

语义（classify_test_progress）：
  progress    : 通过数增加，或失败/错误清零
  no_progress : 通过数与失败/错误数都不变（不得据此判定代码错误）
  regression  : 通过数减少 **且** 失败/错误数增加
  first       : 没有上一轮结果（首次运行）→ 不判定

Regression 只做识别 + 告知，不做任何自动回滚。
"""

from __future__ import annotations

import asyncio
import subprocess
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
from zmai.swe.agent import (
    MAX_COMPLETION_BLOCKS,
    MAX_FORCE_EDIT_STEPS,
    MAX_REGRESSION_RECOVERIES,
    SWEAgent,
)
from zmai.swe.verifier import classify_test_progress, parse_test_totals
from zmai.tool import ToolCall, ToolRegistry


def _t(passed: int, failed: int, *, errors: int = 0, skipped: int = 0) -> dict:
    return {"passed": passed, "failed": failed, "errors": errors,
            "skipped": skipped, "deselected": 0, "ignored": 0, "collected": 0}


# ── Test 1：进步 ────────────────────────────────────────────────
def test_progress_when_more_tests_pass():
    assert classify_test_progress(_t(2, 2), _t(3, 1)) == "progress"


# ── Test 2：不变 ────────────────────────────────────────────────
def test_no_progress_when_unchanged():
    assert classify_test_progress(_t(2, 2), _t(2, 2)) == "no_progress"


# ── Test 3：退步 ────────────────────────────────────────────────
def test_regression_when_fewer_pass_and_more_fail():
    assert classify_test_progress(_t(2, 2), _t(0, 4)) == "regression"


# ── Test 4：全部通过 ────────────────────────────────────────────
def test_all_green_is_progress():
    assert classify_test_progress(_t(2, 2), _t(4, 0)) == "progress"


# ── Test 5：首次运行没有基线 → 不是 regression ──────────────────
def test_first_run_is_not_regression():
    assert classify_test_progress(None, _t(2, 2)) == "first"
    assert classify_test_progress({}, _t(0, 4)) == "first"


def test_skipped_change_alone_is_not_regression():
    """skipped 数量变化不得被判为 regression。"""
    assert classify_test_progress(_t(2, 2, skipped=0), _t(2, 2, skipped=3)) == "no_progress"


def test_collection_error_has_no_counts():
    """纯 collection error 没有 passed/failed 计数 → 解析层不产出计数（语义保持）。"""
    totals = parse_test_totals("ERROR test_x.py\n1 error in 0.05s\n")
    assert totals["passed"] + totals["failed"] == 0
    assert totals["errors"] == 1


# ── Test 1（P0-C）：有基线后掉进 collection error = 灾难性退化 ──────
def test_collection_error_after_counts_is_regression():
    """1 passed/3 failed → 0 passed/0 failed/1 error 必须判 regression。

    修复前这里会返回 "progress"：errors 被并入 failed 计数，于是 "failed 从 3
    降到 0" 被读成进步。语义上这不是失败变少，而是测试整个消失（模块 import 失败）。
    """
    before = _t(1, 3)
    after = parse_test_totals(
        "collected 0 items / 1 error\n"
        "ERROR test_ledger.py - NameError: name 'parse_config' is not defined\n"
        "1 error in 0.29s\n"
    )
    assert after["errors"] == 1 and after["passed"] == 0 and after["failed"] == 0
    assert classify_test_progress(before, after) == "regression"


def test_collection_error_without_baseline_is_first():
    """反向保证：首次运行就是 collection error（无基线）→ 仍是 first，不误报。"""
    assert classify_test_progress(None, _t(0, 0, errors=1)) == "first"
    assert classify_test_progress({}, _t(0, 0, errors=1)) == "first"


# ── Test 2（P0-C 反向保证）：正常计数之间不得被误判为 collection regression ──
def test_normal_result_change_is_not_collection_regression():
    """1 passed/3 failed → 2 passed/2 failed 是进步，不是退化。"""
    assert classify_test_progress(_t(1, 3), _t(2, 2)) == "progress"


def test_normal_regression_still_detected_without_errors():
    """不含 errors 的普通退化仍按 passed/failed 判定（原有语义不变）。"""
    assert classify_test_progress(_t(3, 1), _t(1, 3)) == "regression"


# ── Test 6：Regression 必须进入 Agent 可见上下文 ─────────────────
def _write_project(tmp_path: Path) -> None:
    """初始 1 passed / 1 failed；Agent 的修改会把它变成 0 passed / 2 failed。"""
    (tmp_path / "bug.py").write_text("VALUE = 1\n", encoding="utf-8")
    # 第三个源码文件：让"读取 N 个不同文件"可以真实发生（同一文件重复读会被
    # ReadCache 判定为无新信息、不再计入 reads_after_fail）。
    (tmp_path / "helper.py").write_text("LIMIT = 10\n", encoding="utf-8")
    (tmp_path / "test_all.py").write_text(
        "import bug\n"
        "def test_a():\n    assert bug.VALUE == 1\n"
        "def test_b():\n    assert bug.VALUE == 2\n",
        encoding="utf-8",
    )


def _pytest() -> ToolCall:
    return ToolCall(id="pt", name="shell_exec",
                    params={"command": "python -m pytest -q"})


def _bad_edit() -> ToolCall:
    """把 VALUE 从 1 改成 0：test_a 由通过变失败 → regression。"""
    return ToolCall(id="fix", name="edit",
                    params={"path": "bug.py", "mode": "regex_replace",
                            "old_text": "VALUE = 1", "new_text": "VALUE = 0"})


class _ScriptedBackend(Backend):
    name = "regression"

    def __init__(self, script):
        self._script = script
        self._i = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None
        if self._i < len(self._script):
            calls = self._script[self._i]
        self._i += 1
        # 脚本项为 str → 纯文本响应（无 tool_calls），用于验证文本路径。
        content = calls if isinstance(calls, str) else ""
        if isinstance(calls, str):
            calls = None
        return BackendResponse(
            content=content, tool_calls=calls,
            usage=TokenUsage(1, 1),
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


def _run(tmp_path: Path, script, max_steps: int = 6, extra_config: dict | None = None,
         project=_write_project):
    project(tmp_path)
    backend = _ScriptedBackend(script)
    agent = SWEAgent("reg")
    ctx = AgentContext(
        agent_id="reg",
        task="修复 bug 使全部测试通过",
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50, **(extra_config or {})},
        metadata={},
    )
    asyncio.run(agent.initialize(ctx))
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action.type)
        if action.type in ("complete", "fail"):
            break
    return ctx, actions


def test_regression_reaches_agent_context(tmp_path):
    """1 passed/1 failed → 改成 0 passed/2 failed：注入 [Regression] 到上下文。"""
    script = [
        [_pytest()],     # 1 passed, 1 failed → first（无基线，不报 regression）
        [_bad_edit()],   # 修改使情况变差
        [_pytest()],     # 0 passed, 2 failed → regression
        None,
    ]
    ctx, _ = _run(tmp_path, script)

    text = _messages_text(ctx)
    assert "[Regression]" in text, f"Agent 上下文应包含 [Regression]: {text[-800:]}"
    assert "previous test result: 1 passed, 1 failed" in text
    assert "current test result: 0 passed, 2 failed" in text

    # 结构化记录（不只是内部变量）
    assert ctx.metadata["test_progress"] == "regression"
    assert ctx.metadata["swe_stats"]["regression_detected"] == 1
    # 连续判定：上一轮已更新为当前结果
    assert ctx.metadata["last_test_totals"]["failed"] == 2


def test_first_failing_run_is_not_regression(tmp_path):
    """首次运行就失败（无 previous baseline）→ 不得注入 [Regression]。"""
    script = [
        [_pytest()],  # 1 passed, 1 failed → first
        None,
    ]
    ctx, _ = _run(tmp_path, script)
    assert ctx.metadata["test_progress"] == "first"
    assert "[Regression]" not in _messages_text(ctx)
    assert ctx.metadata["swe_stats"].get("regression_detected", 0) == 0


# ══════════════════════════════════════════════════════════════════
# P0-2：force_edit 闭环 —— 测试失败后必须可靠进入"修改代码"阶段
#   覆盖 RC-2（拦截的读取不喂数）/ RC-3（pytest 可验证）/ RC-4（纯文本不得完成）
#   / RC-5（只读 git 不是修改证据）
# ══════════════════════════════════════════════════════════════════

_EVAL_CFG = {"eval.require_code_change": "true"}


def _read(path: str = "bug.py") -> ToolCall:
    return ToolCall(id="rd", name="read_file", params={"path": path})


def _diagnostic_reads() -> list[ToolCall]:
    """3 次**读取不同文件**的诊断读取（每次都有新信息）→ 攒满 fix.read_limit。

    不能用一个文件连读 3 次来凑数：重复读取会被 ReadCache 判定为无新信息，
    按 P1-E 不再消耗诊断预算。
    """
    return [_read("bug.py"), _read("test_all.py"), _read("helper.py")]


def _git_status() -> ToolCall:
    return ToolCall(id="gs", name="git", params={"args": "status --porcelain"})


def _fix_edit() -> ToolCall:
    """真实改写 bug.py：不改断言语义，只让工作区产生非空 diff。"""
    return ToolCall(id="fix", name="edit",
                    params={"path": "bug.py", "mode": "regex_replace",
                            "old_text": "VALUE = 1", "new_text": "VALUE = 1  # touched"})


def _breaking_edit() -> ToolCall:
    """语法合法、语义破坏的 edit：追加引用未定义名字的代码 → 模块无法 import。"""
    return ToolCall(id="brk", name="edit",
                    params={"path": "bug.py", "mode": "append",
                            "new_text": "\n_x = parse_config\n"})


def _revert_bug_py() -> ToolCall:
    """恢复动作：把 bug.py 写回修改前的正确内容（重新可 import）。"""
    return ToolCall(id="rev", name="write_file",
                    params={"path": "bug.py", "content": "VALUE = 1\n"})


def _git(tmp_path: Path, *args: str) -> str:
    """在测试项目里跑一条 git 命令（-c 传身份，不依赖全局 git 配置）。"""
    r = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    return r.stdout


def _init_repo(tmp_path: Path) -> None:
    """把测试项目纳入版本控制，使"工作区产生真实 diff"可被 git 直接证明。"""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")


# ── Test A：纯文本不能绕过修复流程 ───────────────────────────────
def test_text_only_cannot_complete_without_code_change(tmp_path):
    """RC-4：零修改的纯文本响应不得直接 complete。"""
    ctx, actions = _run(tmp_path, ["I think the issue is fixed."],
                        max_steps=1, extra_config=_EVAL_CFG)

    assert actions[0] == "continue", "零修改的纯文本响应不得判定完成"
    assert "尚未修改任何源码" in _messages_text(ctx)
    assert ctx.metadata.get("ever_modified") is not True


def test_text_only_spin_is_bounded_not_step_exhaustion(tmp_path):
    """纯文本反复空转 → 到上限明确失败，而不是耗尽 max_steps 伪装成 timeout。"""
    ctx, actions = _run(tmp_path, ["fixed it"] * 12, max_steps=12,
                        extra_config=_EVAL_CFG)

    assert "complete" not in actions
    assert actions[-1] == "fail", f"应明确失败而非空转到步数耗尽: {actions}"
    assert len(actions) <= MAX_COMPLETION_BLOCKS + 1, f"拦截次数未收敛: {actions}"


# ── Test B：被拦截的 read 不喂数 ─────────────────────────────────
def test_intercepted_read_does_not_feed_fixdriving(tmp_path):
    """RC-2：被 force_edit 拒绝的读取不计入 reads_after_fail，也不重触发 FixDriving。"""
    script = [
        [_pytest()],                    # 1 failed → test_failed
        _diagnostic_reads(),    # 3 次成功读取 → FixDriving 触发一次
        [_read()],                      # force_edit 生效 → 被拒绝
    ]
    ctx, _ = _run(tmp_path, script, max_steps=3)

    assert ctx.metadata["force_edit"] is True
    assert ctx.metadata["reads_after_fail"] == 0, "被拒绝的读取不得计入 reads_after_fail"
    assert ctx.metadata["swe_stats"]["fixdriving_activations"] == 1, \
        "被拒绝的读取不得重新触发 FixDriving"
    assert "[FixDriving]" in _messages_text(ctx), "模型应看到拒绝原因"


# ── Test C：只读 git 不是修改证据 ────────────────────────────────
def test_git_status_is_not_a_modification(tmp_path):
    """RC-5 主路径：git status 实际执行成功时，不得被当成代码修改。

    这是 RC-5 真正可达的分支 —— force_edit 关闭时 git 才会真正跑起来，
    也只有此时它才会去污染 ever_modified / force_edit / test_failed。
    """
    def repo_project(p: Path) -> None:
        # 本用例的前提是 git status **执行成功**（fail 计数只剩 pytest 那条）。
        # git 的 cwd 是工程目录，工程不是仓库时它只会报 not a git repository：
        # 在没有仓库的机器上"通过"只可能是碰巧身处某个外层仓库里，不能依赖。
        _write_project(p)
        _init_repo(p)

    script = [
        [_pytest()],        # 1 failed → test_failed=True（此时 force_edit 尚未置位）
        [_git_status()],    # 只读 git 真正执行成功
    ]
    ctx, _ = _run(tmp_path, script, max_steps=2, project=repo_project)

    # 只有失败的 pytest 计为 fail —— 证明 git status 确实执行了、没有被拦截，
    # 否则本用例会因"git 被拦"而空洞地通过。
    assert ctx.metadata.get("tool_calls_fail", 0) == 1, "git status 应真正执行而非被拦截"
    assert ctx.metadata.get("ever_modified") is not True, "只读 git 不得算作代码修改"
    assert ctx.metadata["test_failed"] is True, "只读 git 不得清除 test_failed"


def test_git_status_does_not_clear_force_edit(tmp_path):
    """RC-5 强制阶段：force_edit 下 git 被拒绝，且不得解除 force_edit。"""
    script = [
        [_pytest()],
        _diagnostic_reads(),    # → force_edit=True
        [_git_status()],                # 只读 git，应被拒绝
    ]
    ctx, _ = _run(tmp_path, script, max_steps=3)

    assert ctx.metadata.get("ever_modified") is not True, "只读 git 不得算作代码修改"
    assert ctx.metadata["force_edit"] is True, "只读 git 不得解除 force_edit"
    assert ctx.metadata["test_failed"] is True, "只读 git 不得清除 test_failed"
    assert ctx.metadata.get("tool_calls_fail", 0) >= 1, "只读 git 应被拦截"


# ── Test D：验收 —— force_edit 真正闭环 ──────────────────────────
def test_force_edit_closes_loop_with_real_diff(tmp_path):
    """force_edit → 拦截 read → pytest 仍可验证 → edit 真正落地 → 解除 force_edit。"""
    _write_project(tmp_path)
    _init_repo(tmp_path)
    before = (tmp_path / "bug.py").read_text(encoding="utf-8")
    assert _git(tmp_path, "status", "--porcelain").strip() == "", "初始工作区应当是干净的"
    script = [
        [_pytest()],                    # 1) pytest 失败
        _diagnostic_reads(),    # 2) 攒满阈值 → force_edit=True
        [_read()],                      # 3) read 被拒绝，无死循环
        [_pytest()],                    # 4) force_edit 下 pytest 仍可执行（RC-3）
        [_fix_edit()],                  # 5) 被迫 edit
        [_pytest()],                    # 6) 修改后重新验证
    ]
    ctx, _ = _run(tmp_path, script, max_steps=6)

    after = (tmp_path / "bug.py").read_text(encoding="utf-8")
    assert after != before, "edit 必须真正改变工作区文件"
    assert "# touched" in after
    # ── 核心验收：workspace 产生真实、非空的 git diff（而非仅 ever_modified 标志）──
    assert _git(tmp_path, "status", "--porcelain").strip(), "工作区必须出现真实改动"
    assert _git(tmp_path, "diff").strip(), "git diff 必须非空"
    assert "# touched" in _git(tmp_path, "diff")
    assert ctx.metadata["force_edit"] is False, "成功修改后应解除 force_edit"
    assert ctx.metadata["ever_modified"] is True
    # 拦截不再自我喂数：FixDriving 全程只触发一次
    assert ctx.metadata["swe_stats"]["fixdriving_activations"] == 1
    # pytest 在 force_edit 期间确实被执行（未被拦截）
    assert ctx.metadata["swe_stats"]["pytest_calls"] == 3, "第 4 步 pytest 应真正执行"
    assert ctx.metadata.get("tool_calls_fail", 0) >= 1, "第 3 步 read 应被拒绝"


# ── Test E：强制修改阶段必须有界 ─────────────────────────────────
def test_force_edit_phase_is_bounded(tmp_path):
    """RC-6：force_edit 置位后模型始终不修改 → 预算内明确失败，不空转到 max_steps。"""
    script = [[_pytest()], _diagnostic_reads()]
    script += [[_read()] for _ in range(20)]   # 之后一直只读，全部被拒绝
    ctx, actions = _run(tmp_path, script, max_steps=20)

    assert actions[-1] == "fail", f"应在强制期预算内明确失败，而不是空转: {actions}"
    assert len(actions) <= MAX_FORCE_EDIT_STEPS + 3, f"强制期未收敛: {actions}"
    assert ctx.metadata.get("ever_modified") is not True, "全程无真实修改"


# ── Test 3（P1-E）：缓存命中读取不消耗诊断预算 ───────────────────
def test_cached_read_does_not_consume_fixdriving_budget(tmp_path):
    """ReadCache 命中的重复读取没有带来新信息，不得计入 reads_after_fail。"""
    script = [
        [_pytest()],   # 1 passed, 1 failed → test_failed
        [_read()],     # 首次读取：真正拿到内容 → 计数 1
        [_read()],     # 同一文件再读：ReadCache 命中 → 不得计数
    ]
    ctx, _ = _run(tmp_path, script, max_steps=3)

    assert ctx.metadata["swe_stats"]["duplicate_reads"] >= 1, \
        "第二次读取应当是 ReadCache 命中（否则本用例空洞通过）"
    assert ctx.metadata["reads_after_fail"] == 1, \
        "缓存命中的读取不得消耗 FixDriving 诊断预算"


# ── Test 4（P0-B / P1-D）：破坏性 edit → 进入 regression 恢复路径 ──
def test_edit_breaking_import_enters_regression_recovery(tmp_path):
    """语法合法但破坏了 import 的 edit，不得被当作正常成功进度。"""
    script = [
        [_pytest()],          # 1 passed, 1 failed → 建立基线
        [_breaking_edit()],   # 语法合法、语义破坏（追加引用未定义名字）
        [_pytest()],          # 0 passed / 0 failed / 1 error → collection error
        [_read()],            # 恢复态下 read 仍被拦截
    ]
    ctx, _ = _run(tmp_path, script, max_steps=4)

    # 1) 明确判为 regression，而不是 progress
    assert ctx.metadata["test_progress"] == "regression"
    assert ctx.metadata["swe_stats"]["regression_detected"] == 1
    assert ctx.metadata["swe_stats"]["module_broken_regressions"] == 1

    # 2) 上下文里必须出现 [Regression] + 恢复要求（而不是只让模型"继续修改"）
    text = _messages_text(ctx)
    assert "[Regression]" in text
    assert "[Recovery]" in text
    assert "写回修改前的正确内容" in text
    assert "恢复阶段" in text, "恢复态下的拦截消息应要求撤回，而不是继续修改"

    # 3) 不得把破坏性结果当成新基线（否则下一次比较就失去参照）
    assert ctx.metadata["last_test_totals"]["passed"] == 1
    assert ctx.metadata["last_test_totals"]["failed"] == 1

    # 4) 进入恢复/保护态
    assert ctx.metadata["needs_revert"] is True
    assert ctx.metadata["force_edit"] is True


# ── Test 5：regression 不解除保护，且反复改坏必须有界 ────────────
def test_destructive_regression_keeps_protection_and_is_bounded(tmp_path):
    """regression 状态不得自动解除 force_edit；反复写坏项目 → 明确失败。"""
    script = [
        [_pytest()],
        [_breaking_edit()],
        [_pytest()],          # regression #1 → 恢复要求 + 保护态
        [_breaking_edit()],   # 不恢复，继续编辑
        [_pytest()],          # regression #2 → 预算耗尽
    ]
    ctx, actions = _run(tmp_path, script, max_steps=8)

    assert ctx.metadata["force_edit"] is True, "regression 不得自动解除 force_edit"
    assert ctx.metadata["swe_stats"]["module_broken_regressions"] \
        == MAX_REGRESSION_RECOVERIES
    assert actions[-1] == "fail", f"反复把项目改坏必须有界失败: {actions}"
    assert len(actions) <= 5, f"回退预算未收敛: {actions}"


# ── Test 6：闭环验收 —— 改坏 → 恢复 → 重新可诊断，不被卡死 ────────
def test_revert_after_breaking_edit_restores_diagnosable_state(tmp_path):
    """改坏 → 按 [Recovery] 恢复 → 测试重新可收集、恢复态解除、可继续修复。

    这是本次修改的闭环验收：Agent 不会停在"改坏后只会继续改"的死路上。
    """
    script = [
        [_pytest()],            # 1 passed, 1 failed → 基线
        [_breaking_edit()],     # 破坏 import
        [_pytest()],            # collection error → regression + 恢复要求
        [_revert_bug_py()],     # 按 [Recovery] 写回修改前的正确内容
        [_pytest()],            # 测试重新被收集 → 恢复完成
        [_pytest()],            # 继续验证
    ]
    ctx, actions = _run(tmp_path, script, max_steps=6)

    # 1) 只发生过一次破坏性回退，没有触发有界失败
    assert ctx.metadata["swe_stats"]["module_broken_regressions"] == 1
    assert "fail" not in actions, f"恢复后不应被判失败: {actions}"

    # 2) 恢复动作被接受：force_edit 由成功的写操作解除
    assert ctx.metadata["force_edit"] is False, "恢复写入后应退出强制态"
    # 3) 测试重新可收集 → 恢复态解除，重新进入正常诊断/验证
    assert not ctx.metadata.get("needs_revert"), "测试恢复可收集后应解除恢复要求"
    assert ctx.metadata["test_progress"] != "regression"
    assert ctx.metadata["last_test_totals"]["passed"] == 1
    assert ctx.metadata["last_test_totals"]["failed"] == 1


# ══════════════════════════════════════════════════════════════════
# P1-1：不同测试命令共用 last_test_totals → 假 Regression
# ══════════════════════════════════════════════════════════════════
# 复现：`pytest test_fail.py` 记下 0 passed/1 failed，随后**模型未做任何修改**，
# 跑 `pytest broken/` 撞上 collection error。两套测试范围互不相干，却因为共用一份
# last_test_totals 被读成"测试整个消失"→ 假 [Regression] + [Recovery] +
# needs_revert + force_edit。totals 只在同一测试范围内可比。


def _write_cross_project(tmp_path: Path) -> None:
    """两个互不相干的测试范围：test_fail.py（1 failed）与 broken/（无法 import）。"""
    (tmp_path / "test_fail.py").write_text(
        "def test_never():\n    assert False\n", encoding="utf-8")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "test_broken.py").write_text(
        "import module_that_does_not_exist\n", encoding="utf-8")


def _write_recovery_project(tmp_path: Path) -> None:
    """初始 0 passed / 1 failed；一次正确修改即可转绿。"""
    (tmp_path / "bug.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "test_one.py").write_text(
        "import bug\n\n\ndef test_v():\n    assert bug.VALUE == 2\n",
        encoding="utf-8")


def _good_edit() -> ToolCall:
    """把 VALUE 从 1 改成 2：test_one 由失败转通过 → progress。"""
    return ToolCall(id="good", name="edit",
                    params={"path": "bug.py", "mode": "regex_replace",
                            "old_text": "VALUE = 1", "new_text": "VALUE = 2"})


def _cmd(command: str) -> ToolCall:
    return ToolCall(id=command, name="shell_exec", params={"command": command})


# ── P1-1 Test 1（= Test 4 真实 Runtime 复现）：跨命令不得凭计数判 Regression ──
def test_cross_command_run_is_not_regression(tmp_path):
    """不同测试命令之间不得比较 totals，更不得进入回退/强制修改态。

    真实工具执行、模型未做任何修改：needs_revert / force_edit 必须保持未设置，
    [Regression] / [Recovery] 不得注入。
    """
    script = [
        [_cmd("python -m pytest -q test_fail.py")],   # 0 passed, 1 failed
        [_cmd("python -m pytest -q broken/")],        # collection error（另一套范围）
        None,
    ]
    ctx, _ = _run(tmp_path, script, max_steps=4, project=_write_cross_project)

    assert ctx.metadata.get("test_progress") != "regression"
    text = _messages_text(ctx)
    assert "[Regression]" not in text, f"跨命令不得注入假 regression: {text[-500:]}"
    assert "[Recovery]" not in text, f"跨命令不得注入假 recovery: {text[-500:]}"
    assert not ctx.metadata.get("needs_revert")
    assert not ctx.metadata.get("force_edit")
    assert not ctx.metadata.get("ever_modified")
    assert ctx.metadata["swe_stats"].get("regression_detected", 0) == 0
    assert ctx.metadata["swe_stats"].get("module_broken_regressions", 0) == 0


# ── P1-1 Test 2：相同测试命令仍然能够检测 Regression ────────────────
def test_same_command_regression_still_detected(tmp_path):
    """同一测试命令（scope 相同）→ 仍必须判定 regression，修复不得扼杀检测。"""
    script = [
        [_pytest()],      # 1 passed, 1 failed → 建立基线
        [_bad_edit()],    # 改坏
        [_pytest()],      # 0 passed, 2 failed → 必须仍是 regression
        None,
    ]
    ctx, _ = _run(tmp_path, script)

    assert ctx.metadata["test_progress"] == "regression"
    assert "[Regression]" in _messages_text(ctx)
    assert ctx.metadata["swe_stats"]["regression_detected"] == 1


def test_same_command_collection_error_still_regression(tmp_path):
    """同一命令下"测试整个消失"仍是最严重退化（P0-C 语义不得被 scope 门控削弱）。"""
    script = [
        [_pytest()],          # 建立基线
        [_breaking_edit()],   # 破坏 import
        [_pytest()],          # 同一命令 → collection error → regression
        None,
    ]
    ctx, _ = _run(tmp_path, script, max_steps=4)

    assert ctx.metadata["test_progress"] == "regression"
    assert ctx.metadata["swe_stats"]["module_broken_regressions"] == 1
    assert ctx.metadata["needs_revert"] is True


# ── P1-1 Test 3：同一命令下的改善/恢复不得误判 ─────────────────────
def test_same_command_recovery_is_not_regression(tmp_path):
    """同一命令 failed → green 是 progress，不得判 regression。"""
    script = [
        [_pytest()],       # 0 passed, 1 failed → 基线
        [_good_edit()],    # 修好
        [_pytest()],       # 1 passed → progress
        None,
    ]
    ctx, _ = _run(tmp_path, script, project=_write_recovery_project)

    assert ctx.metadata["test_progress"] == "progress"
    assert "[Regression]" not in _messages_text(ctx)
    assert ctx.metadata["swe_stats"].get("regression_detected", 0) == 0
