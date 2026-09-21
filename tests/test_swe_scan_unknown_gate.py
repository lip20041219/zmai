"""P2-1：RepositoryScanner 扫描失败不得等价于"没有测试"。

故障（修复前）：
  `initialize()` 里 scan() 抛异常只记一条 warning，`repo_info` 不写入。完成门禁
  那行

      _has_tests = bool(getattr(repo_info, "test_files", None))

  就把"**无法确定**有没有测试"静默读成了"**没有**测试"。而

      _needs_retest = completion and not tests_complete and (_has_tests or
                                                             tests_ever_failed)

  在 `_has_tests=False` 且测试从未失败时为假，`_needs_change`（仅 eval 模式且
  `ever_modified=False` 时为真）也早已为假 —— 于是"改过一点代码 + 扫描失败 +
  测试从未失败"可以**零正向验证证据**走到 `AgentAction.complete`，finalize 落到
  COMPLETED。

修复后必须成立的三态区分：

  | 状态 | test_discovery | 完成门禁 |
  |---|---|---|
  | 扫描成功，有测试 | known | 要求完整套件全绿（原有行为） |
  | 扫描成功，确实无测试 | known | 保持原有合法行为（可完成） |
  | **扫描失败 / 项目根未知** | **unknown** | **fail-closed：不得仅凭"当前无 failure"完成** |

本文件用真实 `SWEAgent.initialize + step` 驱动，只把 backend 脚本化成"模型回
纯文本"，以此观察完成判定本身。
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
from zmai.swe.scanner import RepositoryScanner
from zmai.tool import ToolRegistry

SOURCE = "def add(a, b):\n    return a + b\n"
TEST_FILE = "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


class _TextOnlyBackend(Backend):
    """模型只回文本、不调用任何工具 —— 这里用它来触发完成判定。"""

    name = "text_only"

    def __init__(self) -> None:
        self.invokes = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invokes += 1
        return BackendResponse(
            content="done",
            tool_calls=None,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


def _project(tmp_path: Path, *, with_tests: bool) -> Path:
    """构造一个最小项目。with_tests=False 时**确实没有**测试文件。"""
    (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
    if with_tests:
        (tmp_path / "test_app.py").write_text(TEST_FILE, encoding="utf-8")
    return tmp_path


def _ctx(project: Path, *, project_path: Path | None) -> AgentContext:
    cfg: dict = {"timeout": 30, "retry.max_attempts": 3}
    if project_path is not None:
        cfg["project_path"] = str(project_path)
    return AgentContext(
        agent_id="scan_gate_test",
        task="修复 app.py",
        backend=_TextOnlyBackend(),
        tools=ToolRegistry(),
        config=cfg,
        workspace=project,
        metadata={},
    )


async def _init_and_step(ctx: AgentContext):
    agent = SWEAgent("scan_gate_test")
    await agent.initialize(ctx)
    return await agent.step(ctx)


# ═══════════════════════════════════════════════════════════════════
# Case A — 正常扫描：有测试 → 行为不变（仍要求正向证据）
# ═══════════════════════════════════════════════════════════════════


class TestCaseANormalScanWithTests:
    def test_known_with_tests_blocks_text_only_completion(self, tmp_path: Path):
        project = _project(tmp_path, with_tests=True)
        ctx = _ctx(project, project_path=project)
        action = asyncio.run(_init_and_step(ctx))

        assert ctx.metadata.get("test_discovery") == "known"
        assert ctx.metadata["repo_info"].test_files, "前置条件：扫描应发现测试文件"
        # 有测试但从未全绿 → 不得完成（与修复前一致）
        assert action.type != "complete", \
            f"有测试的项目未取得全绿证据不得完成: {action.type} / {action.error}"

    def test_scan_populates_repo_info_normally(self, tmp_path: Path):
        project = _project(tmp_path, with_tests=True)
        ctx = _ctx(project, project_path=project)
        asyncio.run(_init_and_step(ctx))
        info = ctx.metadata.get("repo_info")
        assert info is not None and info.source_files
        assert ctx.metadata.get("test_discovery") == "known"


# ═══════════════════════════════════════════════════════════════════
# Case B — 扫描抛异常 → UNKNOWN → fail-closed
# ═══════════════════════════════════════════════════════════════════


class TestCaseBScanRaises:
    def test_scan_failure_is_unknown_not_no_tests(self, tmp_path: Path, monkeypatch):
        project = _project(tmp_path, with_tests=True)   # 项目其实有测试

        def _boom(root, max_files: int = 500):
            raise RuntimeError("scan exploded")

        monkeypatch.setattr(RepositoryScanner, "scan", _boom)

        ctx = _ctx(project, project_path=project)
        action = asyncio.run(_init_and_step(ctx))

        # 不得把"未知"当成"没有测试"
        assert ctx.metadata.get("repo_info") is None, "前置条件：扫描确实失败了"
        assert ctx.metadata.get("test_discovery") == "unknown", \
            "扫描失败必须显式记为 unknown，而不是留下一个隐含的'无测试'"
        # fail-closed：被**完成守卫**拦下（cont），而不是因为别的异常挂掉
        assert action.type == "continue", \
            f"扫描失败应被完成守卫拦下: {action.type} / {action.error}"
        assert "blocked" in (action.output or ""), \
            f"应由完成门禁给出拦截原因: {action.output}"

    def test_unknown_marks_repo_info_absent_but_state_explicit(
            self, tmp_path: Path, monkeypatch):
        """`repo_info` 缺失不等于没有测试 —— 状态必须显式可查。"""
        project = _project(tmp_path, with_tests=False)

        def _boom(root, max_files: int = 500):
            raise OSError("permission denied")

        monkeypatch.setattr(RepositoryScanner, "scan", _boom)
        ctx = _ctx(project, project_path=project)
        asyncio.run(_init_and_step(ctx))

        assert ctx.metadata.get("repo_info") is None
        assert ctx.metadata.get("test_discovery") == "unknown"


# ═══════════════════════════════════════════════════════════════════
# Case C — find_project_root 返回 None → 无法扫描 → UNKNOWN
# ═══════════════════════════════════════════════════════════════════


class TestCaseCFindProjectRootNone:
    def test_no_project_root_is_unknown(self, tmp_path: Path, monkeypatch):
        project = _project(tmp_path, with_tests=False)
        monkeypatch.setattr(RepositoryScanner, "find_project_root",
                            staticmethod(lambda cwd=None: None))

        # 不提供 project_path → initialize 只能依赖 find_project_root
        ctx = _ctx(project, project_path=None)
        action = asyncio.run(_init_and_step(ctx))

        assert ctx.metadata.get("test_discovery") == "unknown", \
            "无法确定项目根不得等价于'没有测试'"
        assert ctx.metadata.get("repo_info") is None
        assert action.type == "continue", \
            f"项目根未知应被完成守卫拦下: {action.type} / {action.error}"
        assert "blocked" in (action.output or ""), \
            f"应由完成门禁给出拦截原因: {action.output}"


# ═══════════════════════════════════════════════════════════════════
# Case D — 真实无测试项目：known 且保留原有合法行为
# ═══════════════════════════════════════════════════════════════════


class TestCaseDRealNoTestsProject:
    def test_known_without_tests_keeps_existing_behavior(self, tmp_path: Path):
        """扫描成功且确认无测试 → 与 UNKNOWN 不同，保留原有可完成行为。

        这条用例是 NO_TESTS ≠ SCAN_UNKNOWN 的证明：同样的"没有测试文件"事实，
        在 known 状态下不阻断完成，在 unknown 状态下必须阻断（Case B/C）。
        """
        project = _project(tmp_path, with_tests=False)
        ctx = _ctx(project, project_path=project)
        action = asyncio.run(_init_and_step(ctx))

        info = ctx.metadata.get("repo_info")
        assert info is not None, "前置条件：扫描必须成功"
        assert not info.test_files, "前置条件：项目确实没有测试文件"
        assert ctx.metadata.get("test_discovery") == "known", \
            "扫描成功即 known —— 这正是与 Case B/C 的区别"
        assert action.type == "complete", \
            f"无测试项目的原有完成行为必须保留: {action.type} / {action.error}"

    def test_no_tests_and_unknown_are_distinguishable(self, tmp_path: Path,
                                                      monkeypatch):
        """同一份"无测试"项目：known 可完成、unknown 被阻断。"""
        project = _project(tmp_path, with_tests=False)

        # known 路径
        ok_ctx = _ctx(project, project_path=project)
        ok_action = asyncio.run(_init_and_step(ok_ctx))
        assert ok_ctx.metadata["test_discovery"] == "known"
        assert ok_action.type == "complete"

        # unknown 路径（同一项目，仅让扫描失败）
        def _boom(root, max_files: int = 500):
            raise RuntimeError("scan exploded")

        monkeypatch.setattr(RepositoryScanner, "scan", _boom)
        bad_ctx = _ctx(project, project_path=project)
        bad_action = asyncio.run(_init_and_step(bad_ctx))
        assert bad_ctx.metadata["test_discovery"] == "unknown"
        assert bad_action.type == "continue", \
            "同样的项目、同样的'没有测试'表象，unknown 必须被守卫拦下（fail-closed）"


# ═══════════════════════════════════════════════════════════════════
# 缺省即 unknown（fail-closed）：未走过 discovery 的上下文不得默认放行
# ═══════════════════════════════════════════════════════════════════


class TestDefaultIsUnknown:
    def test_preset_repo_info_counts_as_known(self, tmp_path: Path):
        """调用方显式预置 repo_info → 视为 known（不改变既有用法）。"""
        project = _project(tmp_path, with_tests=False)
        info = RepositoryScanner.scan(project)
        ctx = _ctx(project, project_path=project)
        ctx.metadata["repo_info"] = info           # 预置，模拟调用方直接提供
        asyncio.run(_init_and_step(ctx))
        assert ctx.metadata.get("test_discovery") == "known"
