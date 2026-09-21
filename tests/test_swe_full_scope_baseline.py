"""P2-2：失败的**子集**测试不得建立 baseline_test_count。

故障（修复前，实测复现）：
  `baseline_test_count` 的建立条件是

      if _total_tests > 0 and (not passed or _full_scope_cmd):

  `not passed` 让失败的子集运行获得了绿运行永远拿不到的授权。失败只暴露"这次跑了
  多少个"测试，不暴露套件规模：`pytest test_app.py` 失败只能证明该文件里有 1 个
  测试。baseline 被锁成子集规模后，同一子集再跑绿即满足
  `_total_tests >= _baseline` → `_scope_complete=True` → `tests_complete=True`
  → 拿到完成资格，而完整套件从未运行。baseline 只读不写，被低估后永久生效。

  P1-2 已堵住"首次子集**全绿**自证 baseline"；P2-2 补上对称的另一半：**失败**同样
  不得自证。修复后 baseline 只能由 `_is_full_scope_test_command()` 认可的命令建立。

  | 运行 | 建立 baseline？ |
  |---|---|
  | 完整范围 + 成功 | 是 |
  | 完整范围 + 失败 | 是 |
  | 子集 + 成功 | 否（P1-2） |
  | **子集 + 失败** | **否（P2-2）** |

本文件用真实 `SWEAgent` + 真实 pytest 子进程 + 真实 `edit` 驱动。项目含两个测试
文件（`test_app.py` / `test_other.py`），因此裸 `python -m pytest -q` 收集 2 个测试，
而 `pytest test_app.py -q` 收集 1 个 —— 子集与完整范围的区别正是本项要验证的对象。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.test_swe_edit_failure_recovery import (
    APP_BUGGY,
    HELPERS,
    TEST_APP,
    _ScriptedBackend,
    _run_agent,
)
from zmai.agent import AgentContext
from zmai.tool import ToolCall

# 第二个测试文件：断言 /other 路由。子集路径下**永远不会被执行**。
TEST_OTHER = '''\
from app import app


def test_other_route():
    app.config["TESTING"] = True
    with app.test_client() as c:
        assert c.get("/other").status_code == 200
'''

_SUBSET = ToolCall(id="p_sub", name="shell_exec",
                   params={"command": "python -m pytest test_app.py -q"})
_BARE = ToolCall(id="p_bare", name="shell_exec",
                 params={"command": "python -m pytest -q"})

# 只修 / 路由：足以让子集（test_app.py）变绿，test_other.py 仍失败
_FIX_ONE = ToolCall(
    id="f1", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "def index", "new_text": "@app.route('/')\ndef index"})

# 修好两个路由：完整套件才能全绿
_FIX_BOTH = ToolCall(
    id="f2", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "def index",
            "new_text": "@app.route('/')\n@app.route('/other')\ndef index"})


def _write_two_file_project(project_dir: Path) -> None:
    """app.py + test_app.py(/ 路由) + test_other.py(/other 路由)。

    裸 `pytest -q` 收集 2 个；`pytest test_app.py -q` 收集 1 个（子集）。
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "app.py").write_text(APP_BUGGY, encoding="utf-8")
    (project_dir / "test_app.py").write_text(TEST_APP, encoding="utf-8")
    (project_dir / "test_other.py").write_text(TEST_OTHER, encoding="utf-8")
    (project_dir / "helpers.py").write_text(HELPERS, encoding="utf-8")


def _baseline(ctx: AgentContext):
    return ctx.metadata.get("baseline_test_count")


def _run(project: Path, script, max_steps: int = 10):
    return asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=max_steps))


# ═══════════════════════════════════════════════════════════════════
# T1 — 子集失败不建立 baseline
# ═══════════════════════════════════════════════════════════════════


class TestT1SubsetFailureDoesNotEstablishBaseline:
    def test_subset_failure_leaves_baseline_unset(self, tmp_path: Path):
        project = tmp_path / "p22_subset_fail"
        _write_two_file_project(project)
        ctx, _action = _run(project, [[_SUBSET], None])

        assert _baseline(ctx) is None, \
            f"失败的子集运行不得建立 baseline（旧行为会锁成 1）: {_baseline(ctx)}"
        comp = ctx.metadata.get("completion")
        assert not (comp and comp.tests_complete), "子集失败不得给出 tests_complete"


# ═══════════════════════════════════════════════════════════════════
# T2 — 子集失败 → 修复 → 同一子集变绿：仍不得获得完整验证资格
# ═══════════════════════════════════════════════════════════════════


class TestT2GreenSubsetAfterFailedSubsetIsNotFullVerification:
    def test_same_subset_green_does_not_complete(self, tmp_path: Path):
        project = tmp_path / "p22_subset_then_green"
        _write_two_file_project(project)
        ctx, action = _run(project, [[_SUBSET], [_FIX_ONE], [_SUBSET], None])

        comp = ctx.metadata.get("completion")
        assert comp is not None, "前置条件：completion 应已建立"
        assert _baseline(ctx) is None, "子集命令始终不得建立 baseline"
        assert comp.tests_complete is False, \
            "同一子集变绿不得被当成完整套件全绿"
        assert action.type != "complete", \
            f"子集自身变绿不得完成（旧行为为 complete）: {action.type} / {action.error}"
        # 必须是**因为 partial_green 被拦**，而不是碰巧没走到完成判定
        assert ctx.metadata.get("test_scope_incomplete") is True
        assert ctx.metadata.get("required_next_action") == "run_full_test_suite"


# ═══════════════════════════════════════════════════════════════════
# T3 — 完整范围失败仍建立 baseline
# ═══════════════════════════════════════════════════════════════════


class TestT3FullScopeFailureStillEstablishesBaseline:
    def test_bare_failing_run_locks_baseline(self, tmp_path: Path):
        project = tmp_path / "p22_full_fail"
        _write_two_file_project(project)
        ctx, _action = _run(project, [[_BARE], None])

        assert _baseline(ctx) == 2, \
            f"完整范围失败运行必须建立 baseline=2（套件规模）: {_baseline(ctx)}"


# ═══════════════════════════════════════════════════════════════════
# T4 — 完整范围失败 → 修复 → 完整范围绿：原有完成行为不回退
# ═══════════════════════════════════════════════════════════════════


class TestT4FullScopeFailureThenGreenCompletes:
    def test_bare_fail_fix_bare_green_completes(self, tmp_path: Path):
        project = tmp_path / "p22_full_then_green"
        _write_two_file_project(project)
        ctx, action = _run(project, [[_BARE], [_FIX_BOTH], [_BARE], None])

        comp = ctx.metadata.get("completion")
        assert _baseline(ctx) == 2
        assert comp is not None and comp.tests_complete is True, \
            "完整套件全绿必须拿到 tests_complete"
        assert action.type == "complete", \
            f"完整套件全绿应正常完成: {action.type} / {action.error}"
        assert ctx.metadata.get("test_success_count") == 1


# ═══════════════════════════════════════════════════════════════════
# T5 — 判别性对照：同一项目、同一修复，只有测试范围不同
# ═══════════════════════════════════════════════════════════════════


class TestT5DiscriminatorIsTestScope:
    def test_subset_path_blocked_full_path_allowed(self, tmp_path: Path):
        # 路径 A：子集失败 → 修 / → 同一子集绿 → 被拦
        pa = tmp_path / "p22_path_a"
        _write_two_file_project(pa)
        ctx_a, action_a = _run(pa, [[_SUBSET], [_FIX_ONE], [_SUBSET], None])

        # 路径 B：完整范围失败 → 修两个路由 → 完整范围绿 → 放行
        pb = tmp_path / "p22_path_b"
        _write_two_file_project(pb)
        ctx_b, action_b = _run(pb, [[_BARE], [_FIX_BOTH], [_BARE], None])

        # 两条路径的项目、修改、pytest 退出码形态完全同构，差异只在命令范围
        comp_a = ctx_a.metadata.get("completion")
        comp_b = ctx_b.metadata.get("completion")
        assert comp_a is not None and comp_b is not None
        assert comp_a.tests_complete is False
        assert comp_b.tests_complete is True
        assert action_a.type != "complete", \
            f"子集路径必须被拦: {action_a.type}"
        assert action_b.type == "complete", \
            f"完整范围路径必须保持完成资格: {action_b.type} / {action_b.error}"
