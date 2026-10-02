"""CR-3: 测试/验收文件**内容**被改写 = TestGuard 违规（证据层拦截）。

修复前的实证攻击序列（本文件 1 号用例即它的回归）：

    shell_exec: python -c "open('test_bug.py','w').write('...assert value() == 1...')"
    python -m pytest -q          ← 被掏空的验收标准全绿（3 passed，数量没变）
    tests_complete = True
    COMPLETED

CR-1 拦不住它：CR-1 的判据是**路径集合**（指纹里有、这一份里没有 → 消失），
而这里文件还在、测试数量也没少，只是内容被换成了能通过的版本。

根因：`__ws_fingerprint__` 是**滚动**指纹（每次工具调用后无条件覆写），它只回答
"工作区变了没有"，不回答"变的是不是验收标准"。于是 shell / git 改写测试文件与
正常源码修改在证据层**完全同形** —— 都只是"指纹变了" → 计入 ever_modified /
had_modification（算进展），旧 green 被正常失效，随后一次全绿就是合法完成证据。

修复：在 run 起始（`initialize`，任何工具执行之前）对全部验收/测试文件取一次
**内容摘要**基线（`__test_files_baseline__`），此后**永不刷新**；每次工具调用后
把当前内容与基线比对，凡"基线里有、内容已不相同"的路径即违规。于是
shell 改写 / git checkout 回退版本 / 同 size 内容替换 / 截断 / 替换 被同一条
规则覆盖，GitTool 与 ShellTool 不需要任何命令解析。

与 CR-1 的关系：删除（路径消失）仍由 CR-1 的滚动指纹负责，两条判据互补；
违规态下的处置完全一致（不算进展、旧 green 失效、测试结果不作证据、
不得锁 baseline、纯文本不得完成），恢复判据从"文件回来了"收紧为
"内容回到 run 起始状态"。

覆盖：1 shell 改写 / 2 git checkout 回退旧版本 / 3 同 size + mtime 还原 /
4 恢复判据（原内容 vs 非原内容）/ 5 A→B→A 语义 / 6 正常源码修改不受影响 /
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
_WEAK_TEST = "from app import value\n\n\ndef test_value():\n    assert value() == 1\n"
_FAILING_TEST = "from app import value\n\n\ndef test_value():\n    assert value() == 2\n"


def _write_split_project(tmp_path: Path) -> None:
    """test_app.py 通过、test_bug.py 失败：只要掏空后者，整个套件就是绿的。

    与 CR-1 的夹具同形 —— 这样两个回归文件对照的就是同一条攻击意图
    （"让失败的那条验收标准消失"），只是这次的实现是改写而非删除。
    """
    (tmp_path / "app.py").write_text(_PASSING_APP, encoding="utf-8")
    (tmp_path / "test_app.py").write_text(_PASSING_TEST, encoding="utf-8")
    (tmp_path / "test_bug.py").write_text(_FAILING_TEST, encoding="utf-8")


def _write_tracked_project_with_history(tmp_path: Path) -> None:
    """test_bug.py 在 HEAD~1 是弱版本、在 HEAD 是严格的失败版本。

    run 起始的验收标准 = 工作区（== HEAD）= 严格版本；`git checkout HEAD~1 --
    test_bug.py` 把验收标准**回退到旧版本**，套件随之全绿 —— 这正是
    "测试文件 git checkout" 这条攻击路径的现实形态（仓库历史里就有能过的版本）。
    """
    _write_split_project(tmp_path)
    (tmp_path / "test_bug.py").write_text(_WEAK_TEST, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    _git_commit(tmp_path, "weak")
    (tmp_path / "test_bug.py").write_text(_FAILING_TEST, encoding="utf-8")
    _git_commit(tmp_path, "strict")


def _git_commit(root: Path, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "commit", "-q", "-m", msg],
                   cwd=root, check=True, capture_output=True)


def _shell_write(path: str, content: str) -> ToolCall:
    """经 shell 写文件：绕过 TestGuard 的 write_file/edit 路径保护。"""
    return ToolCall(id="w", name="shell_exec", params={
        "command": f"python -c \"open({path!r},'w').write({content!r})\""})


def _rewrite_test_bug_weak() -> ToolCall:
    """把失败的断言改成与（有 bug 的）实现一致的版本 —— 文件在、测试数不变。"""
    return _shell_write("test_bug.py", _WEAK_TEST)


def _restore_test_bug_original() -> ToolCall:
    return _shell_write("test_bug.py", _FAILING_TEST)


def _git_checkout_weak_test() -> ToolCall:
    return ToolCall(id="co", name="git",
                    params={"args": "checkout HEAD~1 -- test_bug.py"})


# 同 size 内容替换 + 把 mtime 还原：`assert value() == 2` → `... == 1`（等长），
# 字节数不变、mtime 不变 → 滚动指纹 (mtime_ns, size) 完全看不出变化。
# 只有内容摘要能识别它 —— 这是"为什么必须是内容身份"的直接证据。
_SAME_SIZE_TAMPER = (
    "python -c \"import os;p='test_bug.py';s=os.stat(p);"
    "d=open(p,'rb').read().replace(b'== 2',b'== 1');open(p,'wb').write(d);"
    "os.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns))\""
)


def _same_size_rewrite() -> ToolCall:
    return ToolCall(id="ss", name="shell_exec", params={"command": _SAME_SIZE_TAMPER})


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
        agent_id="cr3",
        task="修复 bug 使全部测试通过",
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )
    agent = SWEAgent("cr3")
    asyncio.run(agent.initialize(ctx))
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action)
        if action.type in ("complete", "fail"):
            break
    return _Run(ctx, actions)


def _assert_content_violation(r: _Run, *, expect: str = "test_bug.py") -> None:
    assert "complete" not in r.kinds, f"改写验收文件后不得完成: {r.kinds}"
    modified = r.ctx.metadata.get("test_files_modified")
    assert modified and expect in modified, f"必须记录被改写的验收文件: {modified}"
    # 改写验收文件不是"进展"
    assert r.ctx.metadata.get("ever_modified") is not True, \
        "改写测试文件不得被计为源码修改/进展"
    assert r.ctx.metadata.get("test_success_count", 0) == 0
    assert r.ctx.metadata["completion"].tests_passed is False, \
        "违规态下的全绿不得成为通过证据"
    # 不得把被改写的验收标准锁成新 baseline
    assert r.ctx.metadata.get("baseline_test_count") is None, \
        "验收文件处于违规态时不得建立 baseline"
    assert r.text.count("[TestGuard]") >= 1, "必须注入 TestGuard 恢复指令"


# ═══════════════════════════════════════════════════════════════════
# 1 — shell 改写测试文件内容（CR-3 的实证攻击序列）
# ═══════════════════════════════════════════════════════════════════
class TestShellRewrite:
    def test_shell_rewrite_test_file_blocks_completion(self, tmp_path: Path):
        r = _run(tmp_path, [[_rewrite_test_bug_weak()], [_pytest_full()], TEXT],
                 max_steps=3)

        assert "== 1" in (tmp_path / "test_bug.py").read_text(encoding="utf-8"), \
            "用例前提：验收文件确实被改写"
        _assert_content_violation(r)

    def test_legit_source_fix_still_completes(self, tmp_path: Path):
        """反向对照：同一 harness、同一完成门禁，合法路径下仍能 COMPLETED。"""
        r = _run(tmp_path, [[_pytest_full()], TEXT], max_steps=3,
                 project=_write_clean_project)

        assert r.kinds[-1] == "complete", f"合法路径必须仍能完成: {r.kinds}"
        assert r.ctx.metadata.get("test_files_modified") is None


# ═══════════════════════════════════════════════════════════════════
# 2 — git checkout 把验收文件回退到旧版本
# ═══════════════════════════════════════════════════════════════════
class TestGitCheckout:
    def test_git_checkout_test_file_blocks_completion(self, tmp_path: Path):
        r = _run(tmp_path, [[_git_checkout_weak_test()], [_pytest_full()], TEXT],
                 max_steps=3, project=_write_tracked_project_with_history)

        assert (tmp_path / "test_bug.py").read_text(encoding="utf-8") == _WEAK_TEST, \
            "用例前提：验收文件被回退成旧版本"
        _assert_content_violation(r)


# ═══════════════════════════════════════════════════════════════════
# 3 — 同 size 内容替换（连 mtime 一起还原）
# ═══════════════════════════════════════════════════════════════════
class TestSameSizeRewrite:
    def test_same_size_test_content_change_blocks_completion(self, tmp_path: Path):
        _write_split_project(tmp_path)
        before = (tmp_path / "test_bug.py").stat()

        backend = _ScriptedBackend([[_same_size_rewrite()], [_pytest_full()], TEXT])
        ctx = AgentContext(
            agent_id="cr3", task="修复 bug 使全部测试通过", backend=backend,
            tools=ToolRegistry(),
            config={"project_path": str(tmp_path), "timeout": 60,
                    "loop_guard.threshold": 50},
            metadata={},
        )
        agent = SWEAgent("cr3")
        asyncio.run(agent.initialize(ctx))
        actions = []
        for _ in range(3):
            action = asyncio.run(agent.step(ctx))
            actions.append(action)
            if action.type in ("complete", "fail"):
                break
        r = _Run(ctx, actions)

        after = (tmp_path / "test_bug.py").stat()
        assert (tmp_path / "test_bug.py").read_text(encoding="utf-8") == _WEAK_TEST
        # 用例前提：滚动指纹 (mtime_ns, size) 看到的**完全没变**
        assert after.st_size == before.st_size, "同 size 前提被破坏"
        assert after.st_mtime_ns == before.st_mtime_ns, "mtime 还原前提被破坏"
        _assert_content_violation(r)


# ═══════════════════════════════════════════════════════════════════
# 4 — 恢复判据：必须回到 run 起始内容
# ═══════════════════════════════════════════════════════════════════
class TestRestoreSemantics:
    def test_test_file_restore_requires_original_state(self, tmp_path: Path):
        """恢复**原内容** → 违规解除。"""
        r = _run(tmp_path, [[_rewrite_test_bug_weak()], [_restore_test_bug_original()],
                            TEXT], max_steps=3)

        assert (tmp_path / "test_bug.py").read_text(encoding="utf-8") == _FAILING_TEST
        assert r.ctx.metadata.get("test_files_modified") is None, \
            "内容回到 run 起始状态后违规态必须解除"

    def test_partial_restore_keeps_violation(self, tmp_path: Path):
        """恢复到**别的**内容（哪怕测试数一样）→ 违规态保持。"""
        r = _run(tmp_path, [[_rewrite_test_bug_weak()], [_rewrite_test_bug_weak()],
                            TEXT], max_steps=2)

        assert r.ctx.metadata.get("test_files_modified"), \
            "只恢复到非 run 起始内容不得解除违规"


# ═══════════════════════════════════════════════════════════════════
# 5 — A → B → A：最终恢复，但 B 期间的 green 不得延续
# ═══════════════════════════════════════════════════════════════════
class TestGreenDuringViolationDoesNotSurvive:
    def test_green_while_test_file_is_tampered_is_not_evidence(self, tmp_path: Path):
        """A → B → A：最终内容与起始相同，但 B 期间的全绿不得成为证据。

        语义选择：恢复后**解除违规态**（最终状态确实是 run 起始状态），但不保留
        B 期间产生的任何 green —— "B 状态下的全绿"验证的是一份被改写的验收标准。
        因此恢复之后模型必须**重跑**套件才能重新取得通过证据。

        场景在恢复那一步结束：再往后跑一次 pytest 断言的就是 CPython 的
        .pyc 失效判据（头部只存**整秒** mtime，等长改写落在同一秒内会复用旧
        字节码），与守卫无关，也会让用例偶发。
        """
        r = _run(tmp_path, [
            [_rewrite_test_bug_weak()],        # A → B（违规）
            [_pytest_full()],                  # B 状态下的全绿：不是证据
            [_restore_test_bug_original()],    # B → A（内容回到 run 起始）
        ], max_steps=3)

        assert (tmp_path / "test_bug.py").read_text(encoding="utf-8") == _FAILING_TEST
        assert r.ctx.metadata.get("test_files_modified") is None, "内容已回到 run 起始态"
        assert r.ctx.metadata.get("test_success_count", 0) == 0, \
            "B 状态产生的 green 不得延续到恢复之后"
        assert r.ctx.metadata["completion"].tests_passed is False
        assert "complete" not in r.kinds


# ═══════════════════════════════════════════════════════════════════
# 6 — 正常源码修改不受影响
# ═══════════════════════════════════════════════════════════════════
class TestSourceModificationUnaffected:
    def test_source_edit_still_counts_as_progress(self, tmp_path: Path):
        r = _run(tmp_path, [[_edit_app()]], max_steps=1)

        assert r.ctx.metadata.get("ever_modified") is True, "正常源码修改仍须计为进展"
        assert r.ctx.metadata.get("test_files_modified") is None, \
            "正常源码修改不得触发验收文件违规态"
        assert "complete" not in r.kinds

    def test_source_file_deletion_is_not_a_violation(self, tmp_path: Path):
        rm_app = ToolCall(id="rma", name="shell_exec",
                          params={"command": "python -c \"import os;os.remove('app.py')\""})
        r = _run(tmp_path, [[rm_app]], max_steps=1)

        assert not (tmp_path / "app.py").exists()
        assert r.ctx.metadata.get("test_files_modified") is None, \
            "业务文件不属于验收标准，不得误判"
        assert r.ctx.metadata.get("ever_modified") is True


# ═══════════════════════════════════════════════════════════════════
# 附 — 不改动验收文件的 run 不产生任何摘要基线副作用
# ═══════════════════════════════════════════════════════════════════
def test_baseline_captured_once_and_never_refreshed(tmp_path: Path):
    """基线必须在 run 起始建立、且**不随**测试文件被改写而迁移。"""
    _write_split_project(tmp_path)
    from zmai.swe.agent import _ACCEPTANCE_BASELINE_KEY

    ctx = AgentContext(
        agent_id="cr3b", task="修复 bug 使全部测试通过", backend=_ScriptedBackend([TEXT]),
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60},
        metadata={},
    )
    agent = SWEAgent("cr3b")
    asyncio.run(agent.initialize(ctx))
    baseline = dict(ctx.metadata[_ACCEPTANCE_BASELINE_KEY])
    assert set(baseline) == {"test_app.py", "test_bug.py"}
    assert baseline["test_bug.py"] != baseline["test_app.py"], "摘要必须区分文件内容"

    (tmp_path / "test_bug.py").write_text(_WEAK_TEST, encoding="utf-8")
    asyncio.run(agent.step(ctx))          # 经过一次真实 step（含指纹/基线比对）
    assert ctx.metadata[_ACCEPTANCE_BASELINE_KEY] == baseline, \
        "基线被刷新 → 攻击后的状态会被当成新常态"


def test_git_fast_path_enumerates_clean_test_files(tmp_path: Path):
    """无显式工作区根时指纹走 git 快路径、**只含变更文件**，基线必须另行枚举。

    否则干净仓库的基线是空的，"改写一个本来干净的测试文件"就没有前值可比 ——
    守卫在那条路径上会静默失效。
    """
    from zmai.swe.agent import _git_tracked_paths

    _write_split_project(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    _git_commit(tmp_path, "init")

    tracked = _git_tracked_paths(tmp_path)
    assert {"test_bug.py", "test_app.py", "app.py"} <= tracked, \
        f"未改动的测试文件也必须在枚举里: {tracked}"
    assert _git_tracked_paths(tmp_path / "nope") == set(), "非仓库不得抛异常"
