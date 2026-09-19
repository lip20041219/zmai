"""P1：GitTool 的真实退出码必须进入统一 ToolResult / verification evidence。

修复前 GitTool 无条件 `ToolResult.ok(output=..., metadata={"exit_code": ...})`：
  * `ToolResult.ok` 硬编码 success=True；
  * `metadata` 不会进入 ContextManager 的 tool_results entry
    （`add_tool_result` 没有该参数，entry 只有 name/success/output/duration_ms[/error]）。
因此一条真实失败的 git 命令对 verifier 完全不可见，auto_generate_checks 可能让
"git 失败"验证通过 —— 执行 → 证据 → 验证 的证据链断裂。

修复判据（结构化，非关键词）：**非零退出码 且 stderr 非空 → 失败**。
git 对真实错误一律写 stderr（`fatal:` / `error:`），对"否定结果"不写：

    真实错误   git log <bad-ref>        exit 128 + stderr
    真实错误   git checkout <bad-path>  exit   1 + stderr "error: pathspec"
    否定结果   git grep <no-match>      exit   1 + stderr 空
    否定结果   git diff --exit-code     exit   1 + stderr 空（有差异）
    否定结果   git commit（无改动）      exit   1 + stderr 空

全部用例使用**真实 GitTool + 真实临时 git 仓库**，不手写 {"success": False} 造数。
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
from zmai.swe.agent import SWEAgent
from zmai.swe.tools import GitTool
from zmai.tool import ToolCall, ToolContext, ToolRegistry


def _git_cli(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=root, capture_output=True, check=False)


def _repo(tmp_path: Path) -> Path:
    """真实 git 仓库（已提交一个文件）。"""
    (tmp_path / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _git_cli(tmp_path, "init", "-q")
    _git_cli(tmp_path, "add", "-A")
    _git_cli(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _ctx(root: Path) -> ToolContext:
    return ToolContext(agent_id="git_test", workspace_path=root, timeout=15)


# ── 1/7：正常 git 命令不受影响 ──────────────────────────────────
def test_git_success_commands_still_succeed(tmp_path):
    """status / diff / log / diff --stat 全部 exit 0 → success=True。"""
    root = _repo(tmp_path)
    tool = GitTool()
    for args in ("status --porcelain", "diff", "log --oneline -1", "diff --stat"):
        r = tool.execute(_ctx(root), {"args": args})
        assert r.success is True, f"git {args} 应成功: {r.error}"
        assert r.metadata.get("exit_code") == 0


# ── 2：真实 git 失败 → success=False，exit_code 与诊断信息可追踪 ──
def test_git_real_failure_is_reported(tmp_path):
    """git log <不存在的 ref> 实际 exit=128 → 必须 success=False。"""
    root = _repo(tmp_path)
    r = GitTool().execute(_ctx(root), {"args": "log --oneline -1 nonexistent-ref-xyz"})

    assert r.success is False, f"真实失败的 git 命令不得报 success: {r.output[:120]}"
    assert r.metadata.get("exit_code") == 128, r.metadata
    detail = (r.error or "") + (r.output or "")
    assert "exit 128" in detail, f"应保留退出码: {detail[:120]}"
    assert "fatal" in detail.lower(), f"应保留 git 的诊断信息: {detail[:120]}"
    assert "nonexistent-ref-xyz" in detail, "应能看到触发失败的命令参数"


def test_git_pathspec_failure_is_reported(tmp_path):
    """exit=1 + stderr 的真实错误（checkout 不存在的路径）同样必须报失败。"""
    root = _repo(tmp_path)
    r = GitTool().execute(_ctx(root), {"args": "checkout nonexistent-branch-xyz"})

    assert r.success is False
    assert r.metadata.get("exit_code") == 1
    assert "pathspec" in ((r.error or "") + (r.output or ""))


# ── 3：否定结果（exit=1 但无 stderr）不得误判为失败 ──────────────
def test_git_negative_result_is_not_a_failure(tmp_path):
    """`git grep` 无匹配 / `git diff --exit-code` 无差异语义 → exit=1 但非失败。"""
    root = _repo(tmp_path)
    tool = GitTool()

    r = tool.execute(_ctx(root), {"args": "grep nomatch_pattern_xyz123"})
    assert r.success is True, f"grep 无匹配是正常否定结果: {r.error}"

    r2 = tool.execute(_ctx(root), {"args": "diff --exit-code"})
    assert r2.success is True, f"无差异的 --exit-code 是正常结果: {r2.error}"


def test_git_success_output_with_error_words_is_not_failure(tmp_path):
    """成功 git 命令的输出里出现普通英文（error/fail）不得判失败。"""
    root = _repo(tmp_path)
    # 提交信息里含 error / fail 字样，git log 正常输出它
    (root / "b.txt").write_text("x\n", encoding="utf-8")
    _git_cli(root, "add", "-A")
    _git_cli(root, "commit", "-qm", "fix error handling failure path")

    r = GitTool().execute(_ctx(root), {"args": "log --oneline -1"})
    assert r.success is True
    assert "error" in r.output.lower(), "夹具必须让输出真的含 error 字样"
    assert r.metadata.get("exit_code") == 0


# ── 4/5：失败进入 agent tool_results → verifier 得到 failed check ──
class _OnceBackend(Backend):
    name = "git_fail"

    def __init__(self, call: ToolCall):
        self._call = call
        self._done = False

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None if self._done else [self._call]
        self._done = True
        return BackendResponse(content="", tool_calls=calls, usage=TokenUsage(1, 1),
                               stop_reason="tool_use" if calls else "end_turn")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


async def _run_once(root: Path, call: ToolCall):
    ctx = AgentContext(
        agent_id="git_fail", task="修复 bug",
        backend=_OnceBackend(call), tools=ToolRegistry(),
        config={"project_path": str(root), "timeout": 30, "_quiet": True,
                "loop_guard.threshold": 50},
        metadata={},
    )
    agent = SWEAgent("git_fail")
    await agent.initialize(ctx)
    await agent.step(ctx)
    entries = ctx.metadata["cm"]._tool_results
    return entries, agent._auto_verify(ctx)


def test_git_failure_reaches_verifier(tmp_path):
    """真实 git 失败经真实 Runtime 进入 tool_results → auto_generate_checks 判失败。"""
    root = _repo(tmp_path)
    call = ToolCall(id="g", name="git",
                    params={"args": "log --oneline -1 nonexistent-ref-xyz"})
    entries, vresult = asyncio.run(_run_once(root, call))

    # 证据链：entry 的 success=False（修复前是 True），失败详情在 error
    git_entries = [e for e in entries if e["name"] == "git"]
    assert len(git_entries) == 1
    assert git_entries[0]["success"] is False, (
        f"git 真实失败必须进入 entry 的 success 字段: {git_entries[0]}"
    )
    assert "fatal" in (git_entries[0].get("error") or "").lower()

    # verifier 侧
    assert vresult is not None
    assert vresult.passed is False
    failed = [c for c in vresult.checks if not c.passed]
    assert len(failed) == 1
    assert failed[0].strategy == "exit_code"
    assert "tool reported failure" in failed[0].evidence


def test_git_success_does_not_produce_failed_check(tmp_path):
    """正常 git 命令经 Runtime → 不产生 failed check（反向保证）。"""
    root = _repo(tmp_path)
    call = ToolCall(id="g2", name="git", params={"args": "status --porcelain"})
    entries, vresult = asyncio.run(_run_once(root, call))

    assert entries[0]["success"] is True
    assert vresult is not None
    assert vresult.passed is True, f"正常 git 不得产生失败 check: {vresult.summary}"
    assert not [c for c in vresult.checks if not c.passed]


# ── 6：LoopGuard 不回归 ─────────────────────────────────────────
def test_loopguard_git_semantics_unchanged(tmp_path):
    """git status 仍不算 modification；git checkout 的真实改动仍被 ws_changed 记录。"""
    from zmai.swe.loop_guard import LoopGuard

    g = LoopGuard(threshold=3)
    g.record_no_modification()
    g.record_tool_call(name="git", params={"args": "status --porcelain"},
                       success=True, ws_changed=False)
    assert g.get_status()["steps_without_change"] == 1, "只读 git 不得重置计数器"

    # git checkout 真实回退：ToolResult 成功 + ws_changed=True → 记录 modification
    root = _repo(tmp_path)
    (root / "app.py").write_text("dirty\n", encoding="utf-8")
    r = GitTool().execute(_ctx(root), {"args": "checkout -- ."})
    assert r.success is True, r.error
    g.record_tool_call(name="git", params={"args": "checkout -- ."},
                       success=True, ws_changed=True)
    assert g.get_status()["steps_without_change"] == 0, "真实工作区变化应重置计数器"
    assert g.get_status()["last_modification_step"] >= 1
