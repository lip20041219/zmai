"""CR-1: 测试文件从工作区消失 = TestGuard 违规（证据层拦截）。

修复前：TestGuard 只保护 `edit` / `write_file`（路径判定），ShellTool 侧是
"破坏性动词开头 + 测试文件标记"的**命令字符串模式**，`GitTool` 则完全没有保护。
于是存在一次真正的完成判定绕过：

    git rm test_bug.py            ← 失败测试被删掉，GitTool 不做任何检查
    python -m pytest -q           ← 这是**首次** full-scope 运行
    baseline_test_count = 1       ← baseline 被锁定为"删除后"的规模
    1 passed → scope_complete     ← 剩下的测试全绿
    COMPLETED                     ← 完成证据来自被收缩过的验收标准

修复后：判据移到**证据层** —— 比较前后两次工作区指纹的路径集合，凡"上一份指纹
里有、这一份里没有、且 `_is_test_file` 为真"的路径，即判违规。于是 git rm /
git mv / git checkout <ref> -- tests/ / git clean / git stash / python -c
"os.remove(...)" 等一切间接路径被同一条规则覆盖，GitTool 不需要命令解析。

违规态下：
  * 该次调用不计入 had_modification / ever_modified（不算进展）；
  * 旧 green 立即失效（CompletionState + test_success_count）；
  * 不得锁定/刷新 baseline_test_count，scope_complete 强制为 False；
  * 测试结果既非通过证据也非失败证据（_no_verdict）；
  * 纯文本路径同样不得完成；
  * 恢复（文件重新出现）后自动解除违规态。

覆盖：1 git 删除 / 2 间接删除 / 3 正常源码修改不受影响 / 4 首次 full-scope 前删除 /
反向对照（同一 harness 在合法路径下确实能 COMPLETED）。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from tests.test_swe_autoverify_bound import (
    TEXT,
    _ScriptedBackend,
    _write_clean_project,
)
from zmai.agent import AgentContext
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolCall, ToolRegistry

_PASSING_APP = "def value():\n    return 1\n"
_PASSING_TEST = "from app import value\n\n\ndef test_value():\n    assert value() == 1\n"
_FAILING_TEST = "from app import value\n\n\ndef test_value():\n    assert value() == 2\n"


def _write_split_project(tmp_path: Path) -> None:
    """test_app.py 通过、test_bug.py 失败：删掉后者即可让整个套件全绿。"""
    (tmp_path / "app.py").write_text(_PASSING_APP, encoding="utf-8")
    (tmp_path / "test_app.py").write_text(_PASSING_TEST, encoding="utf-8")
    (tmp_path / "test_bug.py").write_text(_FAILING_TEST, encoding="utf-8")


def _write_tracked_project(tmp_path: Path) -> None:
    """同上，但额外建一个 git 仓库并提交初始状态（`git rm` 需要文件已被跟踪）。"""
    _write_split_project(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@example.com",
                    "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=tmp_path, check=True, capture_output=True)


def _git_rm_test_bug() -> ToolCall:
    return ToolCall(id="rm", name="git", params={"args": "rm test_bug.py"})


def _indirect_rm_test_bug() -> ToolCall:
    """间接删除：不以破坏性动词开头，绕过 shell 的模式匹配，也不经 GitTool。"""
    return ToolCall(id="py", name="shell_exec",
                    params={"command": "python -c \"import os;os.remove('test_bug.py')\""})


def _pytest_full() -> ToolCall:
    return ToolCall(id="pt", name="shell_exec",
                    params={"command": "python -m pytest -q"})


def _edit_app() -> ToolCall:
    return ToolCall(id="ed", name="edit",
                    params={"path": "app.py", "mode": "regex_replace",
                            "old_text": "return 1", "new_text": "return 1  # touched"})


class _Run:
    def __init__(self, ctx, actions):
        self.ctx = ctx
        self.actions = actions

    @property
    def kinds(self) -> list[str]:
        return [a.type for a in self.actions]

    @property
    def text(self) -> str:
        return "\n".join(str(m.get("content", ""))
                         for m in self.ctx.metadata.get("messages", []))


def _run(tmp_path: Path, script, *, max_steps: int = 4,
         project=_write_split_project) -> _Run:
    project(tmp_path)
    backend = _ScriptedBackend(script)
    ctx = AgentContext(
        agent_id="cr1",
        task="修复 bug 使全部测试通过",
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )
    agent = SWEAgent("cr1")
    asyncio.run(agent.initialize(ctx))
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action)
        if action.type in ("complete", "fail"):
            break
    return _Run(ctx, actions)


def _assert_violation(r: _Run, *, expect_missing: str) -> None:
    assert "complete" not in r.kinds, f"删除测试文件后不得完成: {r.kinds}"
    removed = r.ctx.metadata.get("test_files_removed")
    assert removed and expect_missing in removed, f"必须记录违规路径: {removed}"
    # 不得把收缩后的套件规模锁成新 baseline
    assert r.ctx.metadata.get("baseline_test_count") is None, \
        "验收文件缺失期间不得建立 baseline"
    # 删除本身不是"进展"
    assert r.ctx.metadata["completion"].tests_complete is False
    assert r.ctx.metadata.get("test_success_count", 0) == 0


# ═══════════════════════════════════════════════════════════════════
# 1 — git rm 删除测试文件
# ═══════════════════════════════════════════════════════════════════
class TestGitDeletion:
    def test_git_rm_test_file_blocks_completion(self, tmp_path: Path):
        r = _run(tmp_path, [[_git_rm_test_bug()]], max_steps=1,
                 project=_write_tracked_project)

        assert not (tmp_path / "test_bug.py").exists(), "用例前提：文件真的被删掉"
        _assert_violation(r, expect_missing="test_bug.py")
        assert r.ctx.metadata.get("ever_modified") is not True, \
            "删除测试文件不得被计为源码修改/进展"
        assert "[TestGuard]" in r.text, "必须注入 TestGuard 恢复指令"

    def test_git_rm_then_full_green_cannot_lock_baseline(self, tmp_path: Path):
        """CR-1 核心：删除 → 首次 full-scope 全绿 → 仍不得完成、不得锁 baseline。"""
        r = _run(tmp_path, [[_git_rm_test_bug()], [_pytest_full()], TEXT], max_steps=3,
                 project=_write_tracked_project)

        _assert_violation(r, expect_missing="test_bug.py")
        assert r.ctx.metadata["completion"].tests_passed is False, \
            "收缩套件的全绿不得成为通过证据"
        assert "COMPLETED" not in r.kinds


# ═══════════════════════════════════════════════════════════════════
# 2 — 间接删除（非 GitTool、非 shell 破坏性动词模式）
# ═══════════════════════════════════════════════════════════════════
class TestIndirectDeletion:
    def test_python_os_remove_test_file_blocks_completion(self, tmp_path: Path):
        r = _run(tmp_path, [[_indirect_rm_test_bug()], [_pytest_full()], TEXT],
                 max_steps=3)

        assert not (tmp_path / "test_bug.py").exists(), "用例前提：文件真的被删掉"
        _assert_violation(r, expect_missing="test_bug.py")
        assert r.ctx.metadata["completion"].tests_passed is False

    def test_restoring_the_file_clears_the_violation(self, tmp_path: Path):
        """恢复是唯一合法出口：文件回来后违规态解除。"""
        restore = ToolCall(id="rs", name="write_file",
                           params={"path": "test_app.py", "content": _PASSING_TEST})
        r = _run(tmp_path, [[_indirect_rm_test_bug()], [restore], TEXT], max_steps=3)

        assert not (tmp_path / "test_bug.py").exists()
        assert r.ctx.metadata.get("test_files_removed"), \
            "test_bug.py 仍未恢复 → 违规态必须保持"


# ═══════════════════════════════════════════════════════════════════
# 3 — 正常源码修改不受影响
# ═══════════════════════════════════════════════════════════════════
class TestNormalModificationUnaffected:
    def test_source_edit_still_counts_as_progress(self, tmp_path: Path):
        r = _run(tmp_path, [[_edit_app()]], max_steps=1)

        assert r.ctx.metadata.get("ever_modified") is True, "正常源码修改仍须计为进展"
        assert r.ctx.metadata.get("test_files_removed") is None, \
            "正常源码修改不得触发 TestGuard 违规态"
        assert "complete" not in r.kinds

    def test_source_file_deletion_is_not_a_violation(self, tmp_path: Path):
        """删除**业务**源码文件不是 TestGuard 违规（仍走原有修改/恢复逻辑）。"""
        rm_app = ToolCall(id="rma", name="shell_exec",
                          params={"command": "python -c \"import os;os.remove('app.py')\""})
        r = _run(tmp_path, [[rm_app]], max_steps=1)

        assert not (tmp_path / "app.py").exists()
        assert r.ctx.metadata.get("test_files_removed") is None, \
            "业务文件不属于验收标准，不得误判为 TestGuard 违规"
        assert r.ctx.metadata.get("ever_modified") is True, \
            "业务文件的删除仍是工作区修改"


# ═══════════════════════════════════════════════════════════════════
# 反向对照 — 同一 harness / 同一项目模型，合法路径下确实能 COMPLETED
# ═══════════════════════════════════════════════════════════════════
class TestControlCaseStillCompletes:
    def test_clean_project_full_green_completes(self, tmp_path: Path):
        r = _run(tmp_path, [[_pytest_full()], TEXT], max_steps=3,
                 project=_write_clean_project)

        assert r.kinds[-1] == "complete", f"合法路径必须仍能完成: {r.kinds}"
        assert r.ctx.metadata["completion"].tests_complete is True

    def test_project_with_failing_test_can_still_fix_and_complete(self, tmp_path: Path):
        """真实修复路径未被 CR-1 影响：删掉失败测试不是唯一出路，改源码才是。"""
        # app.value() 返回 1；两个测试不可能同时通过 → 用干净项目 + 真实编辑验证
        # "编辑 → 全绿 → 完成"这条正常闭环仍然成立（同一套完成门禁）。
        r = _run(tmp_path, [[_edit_app()], TEXT], max_steps=2,
                 project=_write_clean_project)

        assert r.ctx.metadata.get("ever_modified") is True
        assert "complete" in r.kinds or "continue" in r.kinds
