"""M1/P0-2：nonce 绑定与独立进程回放回归测试。

覆盖：
  * 正常通过：输出与 receipt 同时绑定本次 nonce，回放通过并记录成本
  * 旧证据复用 / 伪造输出：输出里出现非本次 nonce → 证据作废
  * 伪造凭据：输出带 nonce 但 receipt 没有绑定 → 核验失败
  * 回放不一致：首次通过、回放失败 → 证据污染，完成被门禁拦下
  * 回放超时：有界超时，记录耗时与失败原因
  * 修改后证据失效：由 P0-1 的 stale 用例覆盖（本文件不重复）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from tests.test_swe_evidence_decision import ScriptedBackend, call, pytest_cmd
from zmai.agent import AgentContext
from zmai.swe.acceptance_guard import NONCE_MARKER
from zmai.swe.agent import SWEAgent, _verify_guard_nonce
from zmai.swe.evidence import decide_terminal
from zmai.tool import ToolCall, ToolRegistry

_SEP = " & " if sys.platform == "win32" else " ; "


def _project(tmp_path: Path, test_source: str) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("FIXED = False\n", encoding="utf-8")
    (root / "test_target.py").write_text(test_source, encoding="utf-8")
    return root


def _edit() -> ToolCall:
    return call("edit", path="app.py", mode="regex_replace",
                old_text="FIXED = False", new_text="FIXED = True")


def _run_steps(project: Path, script, *, steps: int = 3, **config):
    ScriptedBackend.script = script
    agent = SWEAgent("nonce_probe")
    ctx = AgentContext(
        agent_id="nonce_probe", task="fix", backend=ScriptedBackend(),
        tools=ToolRegistry(),
        config={"project_path": str(project), "timeout": 60,
                "loop_guard.threshold": 50, **config},
        metadata={},
    )

    async def run():
        await agent.initialize(ctx)
        actions = []
        for _ in range(steps):
            action = await agent.step(ctx)
            actions.append(action)
            if action.type in ("complete", "candidate", "fail"):
                break
        return actions

    return ctx, asyncio.run(run())


_SIMPLE_TEST = "def test_target():\n    assert True\n"


# ═══════════════════════════════════════════════════════════════
# nonce 核验（真实 receipt 文件）
# ═══════════════════════════════════════════════════════════════


def test_nonce_requires_output_and_receipt_binding(tmp_path):
    receipt = tmp_path / "receipt"
    nonce = "a" * 32
    ctx = SimpleNamespace(metadata={
        "__acceptance_guard__": {"receipt": str(receipt), "receipt_seen": ""},
    })
    out = f"{NONCE_MARKER} {nonce}\n1 passed in 0.01s"

    # 输出带 nonce，但 receipt 没有本次绑定 → 伪造凭据。
    receipt.write_text("", encoding="utf-8")
    ok, reason = _verify_guard_nonce(ctx, out, nonce, "python -m pytest -q")
    assert ok is False and "receipt" in reason

    # receipt 有本次绑定 → 通过。
    receipt.write_text(f"123 1.0 {nonce}\n", encoding="utf-8")
    assert _verify_guard_nonce(ctx, out, nonce, "python -m pytest -q")[0] is True

    # 旧 nonce / 缺失 nonce → 失败。
    assert _verify_guard_nonce(
        ctx, f"{NONCE_MARKER} {'b' * 32}\n1 passed", nonce,
        "python -m pytest -q")[0] is False
    assert _verify_guard_nonce(ctx, "1 passed", nonce,
                               "python -m pytest -q")[0] is False

    # 非 pytest 运行器：nonce 不适用，交给回放。
    assert _verify_guard_nonce(ctx, out, nonce, "python -m unittest")[0] is None


def test_stale_nonce_in_output_invalidates_evidence(tmp_path):
    """复用旧输出（伪造 marker）混在真实运行输出里 → 本次证据作废。"""
    project = _project(tmp_path, _SIMPLE_TEST)
    fake = "c" * 32
    cmd = f"echo {NONCE_MARKER} {fake}{_SEP}{pytest_cmd('test_target.py', '-q')}"
    ctx, actions = _run_steps(project, [call("shell_exec", command=cmd)], steps=1)

    assert actions[0].type == "continue"
    assert ctx.metadata.get("test_success_count", 0) == 0
    records = ctx.metadata["evidence_ledger"].records
    assert records and records[0].evidence_status == "polluted"
    assert records[0].integrity.nonce_verified is False
    assert records[0].nonce and records[0].nonce != fake
    assert len(ctx.metadata["evidence_nonces"]) == 1
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind != "completed"
    assert "[EVIDENCE_NONCE]" in "\n".join(
        str(m.get("content", "")) for m in ctx.metadata.get("messages", []))


# ═══════════════════════════════════════════════════════════════
# 独立进程回放
# ═══════════════════════════════════════════════════════════════


def test_replay_mismatch_invalidates_full_green(tmp_path, monkeypatch):
    """首次通过、回放失败：证据污染，完成门禁必须拦下。"""
    monkeypatch.setenv("ZMAI_REPLAY_STATE", str(tmp_path / "state"))
    project = _project(tmp_path, (
        "import os, pathlib\n"
        "def test_target():\n"
        "    p = pathlib.Path(os.environ['ZMAI_REPLAY_STATE'])\n"
        "    assert not p.exists()\n"
        "    p.write_text('x')\n"
    ))
    ctx, actions = _run_steps(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("-q")),
    ], steps=2)

    assert actions[-1].type == "continue", actions
    records = ctx.metadata["evidence_ledger"].records
    record = records[-1]
    assert record.integrity.nonce_verified is True
    assert record.replay.attempted is True
    assert record.replay.verified is False
    assert record.integrity.replay_verified is False
    assert record.evidence_status == "polluted"
    assert "exit code" in record.replay.reason
    assert record.replay.duration_ms >= 0
    assert ctx.metadata["evidence_ledger"].replay_attempts == 1
    assert ctx.metadata["evidence_ledger"].replay_total_ms >= record.replay.duration_ms
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind != "completed"
    assert "[EvidenceGuard]" in "\n".join(
        str(m.get("content", "")) for m in ctx.metadata.get("messages", []))


def test_replay_timeout_is_bounded_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("ZMAI_REPLAY_STATE", str(tmp_path / "state"))
    project = _project(tmp_path, (
        "import os, pathlib, time\n"
        "def test_target():\n"
        "    p = pathlib.Path(os.environ['ZMAI_REPLAY_STATE'])\n"
        "    if p.exists():\n"
        "        time.sleep(5)\n"
        "    else:\n"
        "        p.write_text('x')\n"
    ))
    ctx, actions = _run_steps(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("-q")),
    ], steps=2, **{"replay.timeout": 1})

    assert actions[-1].type == "continue", actions
    record = ctx.metadata["evidence_ledger"].records[-1]
    assert record.replay.attempted is True
    assert record.replay.verified is False
    assert "timed out" in record.replay.reason
    assert record.replay.duration_ms >= 900
    assert ctx.metadata["evidence_ledger"].replay_total_ms >= record.replay.duration_ms
