"""GAP-1：`completion_block_count` 必须是"**连续**无进展"，而不是"整个 run 历史累计"。

故障（修复前，实测复现）：
  `completion_block_count` 全仓只增不减（两处自增、零处重置），而守卫的失败文案与
  其设计契约都是"连续没有取得有效进展"：

      return AgentAction.fail(
          error=f"Completion blocked {_blocks - 1}x without progress: "
                f"ever_modified={...}")

  于是下面这条真实路径会失败在一个**自相矛盾**的判定上：

      pytest 失败 → TEXT（block 1）→ 真实 edit
                  → TEXT（block 2）→ 真实 edit
                  → TEXT（block 3）→ 真实 edit
                  → TEXT（block 4）→ FAILED
      error: "Completion blocked 3x without progress: ever_modified=True"

  `ever_modified=True` 与 `without progress` 出现在同一句里 —— 3 次真实代码修改被
  计入"无进展"。模型一直在推进，只是还没跑到验证，就被判 FAILED（fail-closed）。

修复后语义：

  completion block      → block_count += 1
  真实修改落地（进展边界）→ block_count = 0
  再次 completion block → 从 0 重新累计

  重置点复用全仓唯一的进展边界 `_reset_edit_failure_recovery`（两个调用方都是
  "工作区指纹真的变了"的分支），判据是**工作区证据**而非 `ever_modified` ——
  后者只证明历史上改过代码，不证明最近一次 block 之后取得了进展。

本文件走真实 `SWEAgent.step` + 真实工具执行（真跑 pytest、真写文件）。
"""

from __future__ import annotations

from pathlib import Path

from tests.test_swe_autoverify_bound import (
    EVAL_CFG,
    TEXT,
    _pytest_failing,
    _pytest_q,
    _run,
    _touch_edit,
    _write_clean_project,
    _write_persistent_fail_project,
)
from zmai.swe.agent import MAX_COMPLETION_BLOCKS
from zmai.tool import ToolCall

_REPEAT = TEXT * 6  # 纯文本收尾：每次都会撞完成门禁


def _grep() -> ToolCall:
    """只读工具：不改工作区 → 不得被当成进展。"""
    return ToolCall(id="g", name="grep",
                    params={"pattern": "value", "path": "app.py"})


def _fix() -> ToolCall:
    """真正修好夹具项目的 edit：app.value() 1 → 2，使 test_value 转绿。"""
    return ToolCall(id="fx", name="edit",
                    params={"path": "app.py", "mode": "regex_replace",
                            "old_text": "return 1", "new_text": "return 2"})


def _block_guard_failures(actions) -> list[str]:
    return [a.error for a in actions
            if a.type == "fail" and a.error and "Completion blocked" in a.error]


# ═══════════════════════════════════════════════════════════════════
# T1 — 无进展仍然累计并最终 FAILED（guard 未被绕过）
# ═══════════════════════════════════════════════════════════════════


class TestT1NoProgressStillAccumulates:
    def test_repeated_text_only_still_fails(self, tmp_path: Path):
        ctx, actions = _run(tmp_path, [TEXT] * 12, max_steps=12,
                            extra_config=EVAL_CFG, project=_write_clean_project)
        assert actions[-1].type == "fail", \
            f"零进展的纯文本收尾仍必须有界 FAILED: {[a.type for a in actions]}"
        assert ctx.metadata.get("completion_block_count") == MAX_COMPLETION_BLOCKS + 1

    def test_readonly_tools_are_not_progress(self, tmp_path: Path):
        """只读工具穿插其间不算进展 —— 重置必须绑在**修改**上，不是"有工具活动"上。"""
        script = [TEXT, [_grep()], TEXT, [_grep()], TEXT, [_grep()], TEXT, [_grep()],
                  TEXT]
        ctx, actions = _run(tmp_path, script, max_steps=12,
                            extra_config=EVAL_CFG,
                            project=_write_persistent_fail_project)
        assert actions[-1].type == "fail", \
            f"只读工具不得重置 block 计数: {[a.type for a in actions]}"
        assert ctx.metadata.get("completion_block_count") == MAX_COMPLETION_BLOCKS + 1
        assert _block_guard_failures(actions), "必须由 block guard 收敛"


# ═══════════════════════════════════════════════════════════════════
# T2 — block 后真实 modification → counter reset
# ═══════════════════════════════════════════════════════════════════


class TestT2ModificationResetsCounter:
    def test_real_edit_resets_block_count(self, tmp_path: Path):
        ctx, actions = _run(tmp_path, [TEXT, [_touch_edit()], TEXT],
                            max_steps=4, extra_config=EVAL_CFG,
                            project=_write_persistent_fail_project)
        # step0=TEXT → block 1；step1=真实 edit → reset；step2=TEXT → block 1
        assert ctx.metadata.get("completion_block_count") == 1, \
            (f"真实修改落地必须把 block 计数归零（其后最多再累计 1 次）: "
             f"{ctx.metadata.get('completion_block_count')}")
        assert ctx.metadata.get("ever_modified") is True


# ═══════════════════════════════════════════════════════════════════
# T3 — GAP-1 复现：多次 "block → edit" 不得历史累计到 FAILED
# ═══════════════════════════════════════════════════════════════════


class TestT3InterleavedProgressNeverHitsLimit:
    def test_three_rounds_of_block_then_edit_do_not_fail(self, tmp_path: Path):
        # 修复前：历史累计 4 次 block → FAILED（"without progress" + ever_modified=True）
        script = [[_pytest_failing()],
                  TEXT, [_touch_edit()],
                  TEXT, [_touch_edit()],
                  TEXT, [_touch_edit()],
                  TEXT]
        ctx, actions = _run(tmp_path, script, max_steps=10, extra_config=EVAL_CFG,
                            project=_write_persistent_fail_project)

        assert not _block_guard_failures(actions), (
            f"每轮 block 之间都有真实修改，不得被判'无进展': "
            f"{_block_guard_failures(actions)}")
        assert ctx.metadata.get("completion_block_count") <= 1, (
            f"计数器必须随每次真实修改归零: "
            f"{ctx.metadata.get('completion_block_count')}")
        assert ctx.metadata.get("ever_modified") is True
        assert actions[-1].type != "fail" or not _block_guard_failures(actions)


# ═══════════════════════════════════════════════════════════════════
# T4 — 真实验证进展不得留下旧 block count
# ═══════════════════════════════════════════════════════════════════


class TestT4GreenProgressLeavesNoStaleCount:
    def test_block_then_edit_then_green_completes(self, tmp_path: Path):
        script = [[_pytest_failing()], TEXT, [_fix()], [_pytest_q()], TEXT]
        ctx, actions = _run(tmp_path, script, max_steps=8, extra_config=EVAL_CFG,
                            project=_write_persistent_fail_project)
        assert actions[-1].type == "complete", (
            f"block → 真实修改 → 真实 green 必须能正常完成，不得被旧计数拖成 FAILED: "
            f"{[a.type for a in actions]}")
        assert not _block_guard_failures(actions)


# ═══════════════════════════════════════════════════════════════════
# T5 — 既有 text-only / autoverify 语义未被削弱
# ═══════════════════════════════════════════════════════════════════


class TestT5ExistingGuardsIntact:
    def test_text_only_cannot_complete_without_modification(self, tmp_path: Path):
        """纯文本响应不得绕过完成门禁 —— 未修改代码时永不 complete。"""
        ctx, actions = _run(tmp_path, [TEXT] * 10, max_steps=10,
                            extra_config=EVAL_CFG,
                            project=_write_persistent_fail_project)
        assert all(a.type != "complete" for a in actions), \
            f"未修改代码不得完成: {[a.type for a in actions]}"
        assert actions[-1].type == "fail"

    def test_guard_still_bounded(self, tmp_path: Path):
        """收敛仍有界：不得空转到 max_steps（否则会退化成 TIMEOUT）。"""
        max_steps = 12
        _ctx, actions = _run(tmp_path, [TEXT] * max_steps, max_steps=max_steps,
                             extra_config=EVAL_CFG, project=_write_clean_project)
        assert len(actions) <= MAX_COMPLETION_BLOCKS + 1, \
            f"收敛步数必须仍受 MAX_COMPLETION_BLOCKS 约束: {len(actions)}"
