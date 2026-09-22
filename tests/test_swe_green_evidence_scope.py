"""完成门禁：区分"零修改的 verify-only run"与"未被验证的修改"。

回归（修复前，`pytest -q` 实测 15 个用例失败）：

  * 测试**首次运行就全绿**（哪怕是子集命令）的 run 永远拿不到完成资格：
    子集全绿 → `_scope_complete=False` → 既不累计 `test_success_count`，也把
    `_needs_retest` 置于真 → 完成点被反复拦下（日志 "Completion blocked: no code
    modification yet"）→ 烧完 MAX_COMPLETION_BLOCKS 后整个 run 判 FAILED。
    autostop / termination / requirements 这类"跑一次测试，通过就停"的场景全部阵亡。
  * 从未 `initialize`（或直接构造 AgentContext）的 run 被 `test_discovery` 缺省值
    fail-closed：连"这个任务到底有没有测试"都没问过，就被要求出示完整套件全绿。
  * 被推断出来的项目根（cwd 恰好在一个仓库里）把 "say hello" 变成测试任务。

修复后的判据（"真正需要阻止完成的情况"才阻止）：

  1. 测试失败过 → 必须有完整套件全绿（不论是否改过代码）；
  2. 改过代码 或 零绿色证据（且测试是验收标准 / 发现状态显式未知）→ 必须有完整套件全绿；
  3. 零修改 + 首次运行即全绿 且**没有"漏跑了测试"的证据**（未建立基线，或有基线
     但本次覆盖数达到基线）→ 放行：没有修改就没有"未验证的改动"，一次真实全绿
     就是本 run 自己的完成证据；已知基线却只跑子集的不算（有证据证明还有测试没跑）。

覆盖"不能回退"的既有约束：partial green 不得借此完成（T3）、零证据不得完成（T2）、
block 计数只按"连续无进展"度量（T4）。

全部用例走真实 `SWEAgent.step` + 真实工具执行（真跑 pytest）。项目根显式声明
（config.project_path），与 CLI / benchmark harness 一致。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.test_swe_autoverify_bound import (
    TEXT,
    _pytest_failing,
    _ScriptedBackend,
    _touch_edit,
    _write_clean_project,
    _write_persistent_fail_project,
)
from zmai.agent import AgentContext
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolCall, ToolRegistry

# 子集命令：显式指定测试目标 → 不是 full-scope（P1-2/P2-2 不变），
# 因此它拿到的是 partial_green（tests_complete=False）。
_SUBSET = ToolCall(id="ps", name="shell_exec",
                   params={"command": "python -m pytest test_app.py -q"})

_FULL = ToolCall(id="pf", name="shell_exec",
                 params={"command": "python -m pytest -q"})


class _Run:
    def __init__(self, agent, ctx, backend, actions):
        self.agent = agent
        self.ctx = ctx
        self.backend = backend
        self.actions = actions

    @property
    def kinds(self) -> list[str]:
        return [a.type for a in self.actions]


def _run(tmp_path: Path, script, *, max_steps: int = 6,
         project=_write_clean_project) -> _Run:
    """驱动真实 SWEAgent.step（项目根显式声明 = 测试是验收标准）。"""
    project(tmp_path)
    backend = _ScriptedBackend(script)
    ctx = AgentContext(
        agent_id="green_scope",
        task="修复 bug 使全部测试通过",
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )
    agent = SWEAgent("green_scope")
    asyncio.run(agent.initialize(ctx))
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action)
        if action.type in ("complete", "fail"):
            break
    return _Run(agent, ctx, backend, actions)


# ═══════════════════════════════════════════════════════════════════
# T1 — 零修改 + 全绿（子集）→ 必须 complete
# ═══════════════════════════════════════════════════════════════════


class TestT1GreenWithoutModificationCompletes:
    def test_subset_green_without_modification_completes(self, tmp_path: Path):
        """首次运行即全绿（子集命令）且从未修改源码 → 立即 complete。"""
        r = _run(tmp_path, [[_SUBSET], TEXT])

        assert r.kinds[-1] == "complete", (
            f"零修改的 run 跑出全绿必须能完成: {r.kinds}"
        )
        assert r.ctx.metadata.get("ever_modified") is not True, "本用例前提：零修改"
        # 放行的只是"完成判定"，证据本身仍然是 partial_green：
        # 改过代码的 run 依旧需要一次完整套件全绿（见 T3）。
        comp = r.ctx.metadata["completion"]
        assert comp.tests_complete is False, "子集全绿不得被记成完整套件全绿"
        assert comp.tests_passed is True, "但那次运行确实是绿的"
        assert r.ctx.metadata.get("test_success_count") == 1

    def test_green_then_next_round_does_not_call_backend(self, tmp_path: Path):
        """全绿后第 2 轮必须短路 → backend 不得再被调用（autostop 语义）。"""
        r = _run(tmp_path, [[_SUBSET], TEXT], max_steps=1)
        assert r.kinds == ["complete"]
        calls_after_first = r.backend._i

        action = asyncio.run(r.agent.step(r.ctx))
        assert action.type == "complete", f"已绿后应短路 complete: {action.type}"
        assert r.backend._i == calls_after_first, (
            f"已绿后不得再调用 backend: {calls_after_first} → {r.backend._i}"
        )


# ═══════════════════════════════════════════════════════════════════
# T2 — 零绿色证据（测试未通过 / 从未跑）→ 仍然不能 complete
# ═══════════════════════════════════════════════════════════════════


class TestT2NoGreenEvidenceStillBlocked:
    def test_failing_subset_without_progress_cannot_complete(self, tmp_path: Path):
        """测试未全绿且没有进展 → 完成守卫必须照常介入。"""
        r = _run(tmp_path, [[_SUBSET], TEXT],
                 project=_write_persistent_fail_project)

        assert "complete" not in r.kinds, f"测试未全绿不得完成: {r.kinds}"
        assert r.ctx.metadata.get("ever_modified") is not True

    def test_text_only_with_zero_evidence_cannot_complete(self, tmp_path: Path):
        """项目有测试（且是验收标准）却零修改、零测试 → 不得凭纯文本完成。"""
        r = _run(tmp_path, [TEXT, TEXT], project=_write_persistent_fail_project)

        assert "complete" not in r.kinds, f"零证据不得完成: {r.kinds}"
        assert r.ctx.metadata["completion"].tests_complete is False

    def test_undeclared_project_without_evidence_completes(self, tmp_path: Path):
        """项目根只是**被推断**出来的（调用方没声明）→ 不构成验收标准。

        与上一条的差别只在"项目根来源"：CLI / benchmark 会显式声明 project_path，
        那种 run 必须出示证据；而 cwd 恰好在一个仓库里不等于"这个任务是它的测试"。
        """
        _write_persistent_fail_project(tmp_path)
        backend = _ScriptedBackend([TEXT])
        ctx = AgentContext(
            agent_id="undeclared",
            task="say hello",
            backend=backend,
            tools=ToolRegistry(),
            workspace=tmp_path,
            metadata={"messages": []},
        )
        agent = SWEAgent("undeclared")
        asyncio.run(agent.initialize(ctx))

        # 前置条件：确实扫描到了测试（项目根来自 cwd 推断，不是调用方声明）
        assert ctx.metadata.get("test_discovery") == "known"
        assert ctx.metadata.get("repo_info") is not None
        assert ctx.metadata.get("project_root_declared") is False

        action = asyncio.run(agent.step(ctx))
        assert action.type == "complete", (
            f"未声明项目的 run 不得被测试证据门禁卡死: {action.type} / {action.error}"
        )


# ═══════════════════════════════════════════════════════════════════
# T3 — 改过代码后，子集全绿依旧不构成完成资格（P1-2/P2-2 不回退）
# ═══════════════════════════════════════════════════════════════════


class TestT3PartialGreenStillNotCompletion:
    def test_modified_then_subset_green_cannot_complete(self, tmp_path: Path):
        r = _run(tmp_path, [[_touch_edit()], [_SUBSET], TEXT])

        assert "complete" not in r.kinds, (
            f"改过代码后子集全绿不得完成: {r.kinds}"
        )
        assert r.ctx.metadata.get("ever_modified") is True
        assert r.ctx.metadata["completion"].tests_complete is False
        assert r.ctx.metadata.get("test_scope_incomplete") is True, (
            "必须提示模型去跑完整套件"
        )

    def test_modified_then_full_green_completes(self, tmp_path: Path):
        """反向护栏：改过代码 + 完整套件全绿 → 正常完成（既有行为不变）。"""
        r = _run(tmp_path, [[_touch_edit()], [_FULL], TEXT])

        assert r.kinds[-1] == "complete", f"{r.kinds}"
        assert r.ctx.metadata["completion"].tests_complete is True


# ═══════════════════════════════════════════════════════════════════
# T4 — completion_block_count 仍按"连续无进展"度量（GAP-1 不回退）
# ═══════════════════════════════════════════════════════════════════


class TestT4BlockCountMeasuresConsecutiveLackOfProgress:
    def test_real_progress_resets_counter_without_eval_mode(self, tmp_path: Path):
        """非 eval 路径同样在真实修改落地时归零；修改后仍须完整套件全绿。

        逐步记录计数轨迹：block 连续累计 → 真实 edit 落地 → 计数回落。
        """
        _write_persistent_fail_project(tmp_path)
        script = [[_pytest_failing()], TEXT, TEXT, [_touch_edit()]] + [TEXT] * 4
        backend = _ScriptedBackend(script)
        ctx = AgentContext(
            agent_id="reset_track",
            task="修复 bug 使全部测试通过",
            backend=backend,
            tools=ToolRegistry(),
            config={"project_path": str(tmp_path), "timeout": 60,
                    "loop_guard.threshold": 50},
            metadata={},
        )
        agent = SWEAgent("reset_track")
        asyncio.run(agent.initialize(ctx))

        counts: list[int] = []
        kinds: list[str] = []
        for _ in range(6):
            action = asyncio.run(agent.step(ctx))
            kinds.append(action.type)
            counts.append(ctx.metadata.get("completion_block_count", 0))
            if action.type in ("complete", "fail"):
                break

        assert ctx.metadata.get("ever_modified") is True, "前置条件：edit 必须落地"
        # step1/step2 = 两次纯文本、无进展 → 计数连续累计
        assert counts[2] > counts[1] > 0, f"无进展必须连续累计: {counts}"
        # step3 = 真实 edit（进展边界）→ 计数归零
        assert counts[3] == 0, f"真实修改落地必须让计数归零: {counts}"
        # step4 = 再次纯文本 → 从 0 重新累计（而不是接着历史累计）
        assert counts[4] == 1, f"归零后必须重新累计: {counts}"
        assert "fail" not in kinds, f"有真实进展不得被判'无进展'而 FAILED: {kinds}"
