"""SWE Fix-Driving — 测试失败后 Agent 必须进入修改阶段。

覆盖：
  1. 真实 Flask bug（@app.route 缺失 → 404）端到端修复闭环：
     pytest失败 → 读相关文件 → edit 修改 → pytest通过 → complete
  2. 测试失败后只读不修达到阈值 → 强制注入"必须修改"提示
  3. pytest 失败（exit_code!=0）也会被识别为测试失败（触发修复态）

这些测试用真实文件系统 + 真实工具执行（mock backend 仅决定"下一步做什么"，
工具本身真实读文件、真实跑 pytest、真实 edit）。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# 复用既有的工具结果截获器（"哪次调用真的执行了 / 哪次被拒绝"）
from tests.test_swe_edit_failure_recovery import _capture_tool_results
from zmai.agent import AgentContext
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.swe.agent import SWEAgent, _build_fix_state_directive
from zmai.tool import ToolCall, ToolRegistry

# ═══════════════════════════════════════════════════════════════════
# 辅助: 搭建一个缺失 @app.route 的 Flask 项目
# ═══════════════════════════════════════════════════════════════════


APP_BUGGY = '''\
from flask import Flask

app = Flask(__name__)


def index():
    return "Hello"


if __name__ == "__main__":
    app.run(debug=True)
'''

APP_FIXED_MARKER = "@app.route('/')"

TEST_APP = '''\
import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_home_returns_200(client):
    rv = client.get("/")
    assert rv.status_code == 200
'''


HELPERS = '''\
"""与 bug 无关的旁支模块：让"读取 N 个不同文件"能真实发生。"""

DEFAULT_TIMEOUT = 30
'''


def _write_flask_project(project_dir: Path) -> None:
    """写入一个缺失 @app.route 的 Flask 项目（测试先失败）。"""
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "app.py").write_text(APP_BUGGY, encoding="utf-8")
    (project_dir / "test_app.py").write_text(TEST_APP, encoding="utf-8")
    # 第三个文件：同一文件重复读取会被 ReadCache 判定为无新信息、不再计入
    # reads_after_fail，因此"攒满读取阈值"必须靠读取不同文件。
    (project_dir / "helpers.py").write_text(HELPERS, encoding="utf-8")


def _read_file(path: str, tid: str = "r") -> ToolCall:
    return ToolCall(id=tid, name="read_file", params={"path": path})


def _diagnostic_reads() -> list[ToolCall]:
    """3 次读取不同文件的诊断读取 → 攒满 fix.read_limit（默认 3）。"""
    return [_read_file("app.py", "r1"), _read_file("test_app.py", "r2"),
            _read_file("helpers.py", "r3")]


class _ScriptedBackend(Backend):
    """按预写脚本依次返回工具调用；无脚本时返回 end_turn。

    工具调用会真实执行（真实读/写/跑 pytest），backend 只决定"下一步做什么"。
    """

    name = "scripted_fix"

    def __init__(self, script: list[list[ToolCall] | None]):
        self._script = script
        self._idx = 0
        self.calls_seen: list[str] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None
        if self._idx < len(self._script):
            calls = self._script[self._idx]
        self._idx += 1
        if calls:
            for c in calls:
                self.calls_seen.append(c.name)
        # 脚本耗尽 = 模拟模型结束回合。真实模型收尾时会**说话**；`content=""` +
        # 无 tool_calls 是退化响应（P1-B 起 runtime 会重试它），不再用作收尾信号。
        return BackendResponse(
            content="" if calls else "(no further tool calls)",
            tool_calls=calls,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use" if calls else "end_turn",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


async def _run_agent(project_dir: Path, backend: Backend, max_steps: int = 12,
                     initial_metadata: dict | None = None) -> tuple[AgentContext, Any]:  # noqa: E501
    agent = SWEAgent("fix_drive_test")
    registry = ToolRegistry()
    ctx = AgentContext(
        agent_id="fix_drive_test",
        task=(
            f"项目在 {project_dir} 目录下。\n"
            f"任务: test_app.py 中 test_home_returns_200 失败，页面返回 404。"
            f"请读取代码，修复 app.py，使测试通过。"
        ),
        backend=backend,
        tools=registry,
        config={"project_path": str(project_dir), "timeout": 30},
        metadata=dict(initial_metadata or {}),
    )
    await agent.initialize(ctx)

    action = None
    for _ in range(max_steps):
        action = await agent.step(ctx)
        if action.type in ("complete", "fail"):
            break
    return ctx, action


# ═══════════════════════════════════════════════════════════════════
# 测试 1: Flask bug 端到端修复闭环
# ═══════════════════════════════════════════════════════════════════


class TestFlaskFixEndToEnd:
    def test_missing_route_fixed_and_tests_pass(self, tmp_path: Path):
        """缺失 @app.route → agent 走完 失败→分析→修改→重测→完成。"""
        project = tmp_path / "flask_app"
        _write_flask_project(project)

        # 脚本: pytest(失败) → 读app → 读test → edit加route → pytest(通过) → 结束
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="read_file", params={"path": "test_app.py"})],
            [ToolCall(id="4", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [_PYTEST],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 1) agent 成功完成
        assert action.type == "complete", f"应 complete, 实际 {action.type}: {action.output}"

        # 2) app.py 被真实修改，加上了路由
        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert APP_FIXED_MARKER in app_text, "app.py 应包含 @app.route('/')"

        # 3) 真实 pytest 通过（用与 agent 相同的解释器 sys.executable）
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=str(project), capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"修复后 pytest 应通过: {r.stdout}{r.stderr}"

        # 4) 脚本完整走完：读→修→测 顺序正确
        calls = backend.calls_seen
        assert "edit" in calls, f"应调用 edit 修改文件: {calls}"
        # edit 必须在第二次 pytest 之前
        assert calls.index("edit") < calls.index("shell_exec", calls.index("edit")), "edit 应在重测前"  # noqa: E501


# ═══════════════════════════════════════════════════════════════════
# 测试 2: fix-driving 强制修改
# ═══════════════════════════════════════════════════════════════════


class TestFixDrivingEnforcement:
    def test_read_only_after_fail_triggers_force_modify(self, tmp_path: Path):
        """测试失败后只读不修达阈值 → 注入"必须修改"提示。"""
        project = tmp_path / "flask_app2"
        _write_flask_project(project)

        # 脚本: pytest(失败) → 反复 read_file 不修改（读取不同文件，才有诊断信息量）
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
        ]
        for i in range(6):  # 连续 6 个 step 只读，不修改
            name = ["app.py", "test_app.py", "helpers.py"][i % 3]
            script.append([ToolCall(id=f"r{i}", name="read_file",
                                    params={"path": name})])
        script.append(None)
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 达到阈值后应触发 FixDriving 强制修改（return cont, 提示包含"必须修改"）
        messages = ctx.metadata.get("messages", [])
        texts = [getattr(m, "content", "") or m.get("content", "")
                 if isinstance(m, dict) else getattr(m, "content", "")
                 for m in messages] if messages else []
        joined = " ".join(texts)
        assert "[FixDriving]" in joined, "应注入 FixDriving 强制修改提示"
        assert "edit" in joined or "write_file" in joined, "提示应指向 edit/write_file"

    def test_modification_exits_fix_state(self, tmp_path: Path):
        """修改成功后，test_failed 应清空，不再触发 fix-driving。"""
        project = tmp_path / "flask_app3"
        _write_flask_project(project)

        # 脚本: pytest(失败) → read → edit(修改成功) → read(此时已退出修复态,不再累计)
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [ToolCall(id="4", name="read_file", params={"path": "app.py"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 修改后 read 不应触发 fix-driving
        messages = ctx.metadata.get("messages", [])
        texts = [getattr(m, "content", "") if not isinstance(m, dict) else m.get("content", "")
                 for m in messages] if messages else []
        joined = " ".join(texts)
        assert "[FixDriving]" not in joined, "修改成功后不应再触发 FixDriving"


# ═══════════════════════════════════════════════════════════════════
# 测试 2b: FixDriving 触发下限 —— 至少读过 1 个非测试源文件（C1）
# ═══════════════════════════════════════════════════════════════════


def _write_extra_files(project: Path, names: list[str]) -> None:
    """补写若干文件，让"读不同文件"能真实发生（同文件重复读被 ReadCache 拦下，不计入）。"""
    for name in names:
        (project / name).write_text("# unrelated module\n", encoding="utf-8")


def _only_reads(names: list[str], repeat: int = 1) -> list[list[ToolCall] | None]:
    """构造"只读不修"脚本：每步读一个不同文件。"""
    return [[_read_file(n, f"r{i}")] for i, n in enumerate(names * repeat)]


class TestFixDrivingSourceReadGate:
    """触发条件是"读够次数 **且** 读过非测试源文件"，不再是纯计数。"""

    def test_only_test_file_reads_do_not_trigger(self, tmp_path: Path):
        """只读测试文件：读数攒满阈值也不得进入强制修改期。"""
        project = tmp_path / "only_tests"
        _write_flask_project(project)
        _write_extra_files(project, ["test_extra_a.py", "test_extra_b.py"])

        script = [[_PYTEST], *_only_reads(
            ["test_app.py", "test_extra_a.py", "test_extra_b.py"], repeat=2), None]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=12))

        # 阈值确实攒满了 —— 证明拦住它的是新下限，而不是读数不够
        assert ctx.metadata["reads_after_fail"] >= 3
        assert ctx.metadata["src_reads_after_fail"] == 0
        # 断言权威状态而非注入文本：步数一多，上下文压缩会把 FixDriving 消息
        # 压进 [历史摘要]，用文本断言既会假失败、也会在真触发时假通过。
        assert ctx.metadata.get("force_edit") is not True

    def test_source_reads_still_trigger(self, tmp_path: Path):
        """读够非测试源文件 → 仍按原语义触发强制修改。"""
        project = tmp_path / "src_reads"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])

        script = [[_PYTEST], *_only_reads(
            ["app.py", "module_a.py", "module_b.py"]), None]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=12))

        assert ctx.metadata.get("force_edit") is True

    def test_unrelated_source_reads_also_trigger(self, tmp_path: Path):
        """当前策略：任意非测试文件都算"源文件"，不校验是否与失败相关。

        这是刻意的取舍 —— 按 candidate_files 精确匹配需要新建路径归一化机制，
        而该字段绝对/相对混杂且可为空（failure.py:_collect_candidate_files）。
        此处固化这条已知上限：只读无关模块同样会触发。
        """
        project = tmp_path / "unrelated"
        _write_flask_project(project)
        _write_extra_files(project, ["unrelated_a.py", "unrelated_b.py"])

        script = [[_PYTEST], *_only_reads(
            ["helpers.py", "unrelated_a.py", "unrelated_b.py"]), None]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=12))

        assert ctx.metadata.get("force_edit") is True

    def test_persisted_state_without_source_read_does_not_trigger(self, tmp_path: Path):
        """持久化恢复：读数已满但源文件读数为 0 → 仍不触发（新下限跨步生效）。"""
        project = tmp_path / "persisted"
        _write_flask_project(project)

        # 模拟"上一阶段留下的状态"：读数已满、源文件读数为 0
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="e", name="shell_exec", params={"command": "echo hi"})],
            None,
        ]
        ctx, _ = asyncio.run(_run_agent(
            project, _ScriptedBackend(script), max_steps=2,
            initial_metadata={
                "test_failed": True,
                "repair_plan_injected": True,
                "repair_phase": "plan",
                "reads_after_fail": 3,
                "src_reads_after_fail": 0,
            },
        ))

        assert ctx.metadata["reads_after_fail"] >= 3
        assert "[FixDriving]" not in _messages_text(ctx)

    def test_counters_cleared_when_force_edit_fires(self, tmp_path: Path):
        """触发即清零：下一轮修复周期不得继承上一轮的读数。"""
        project = tmp_path / "cleared"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])

        # 恰好 4 步：pytest(失败) → 读 3 个源文件 → 第 4 步末尾触发 FixDriving
        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"])]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=4))

        assert ctx.metadata.get("force_edit") is True
        assert ctx.metadata["reads_after_fail"] == 0
        assert ctx.metadata["src_reads_after_fail"] == 0


# ═══════════════════════════════════════════════════════════════════
# 强制期入口的"零证据"修复：目标未知 → 一次 grep discovery 额度
#
# 缺陷（真实轨迹取证）：`edit_recovery_read_allowance` /
# `edit_recovery_grep_allowance` 只在 `_handle_edit_failure` 里武装，即必须先有
# 一次 edit/write_file **真的执行且失败**。对"被拦截后不再尝试 edit、继续发读取
# 调用"的模型，这个前置永远不成立 —— 强制期内没有任何工具能产生新证据：
#   * xarray-4248：全程 0 次 edit，28 次调用被拒，终态 timeout；
#   * agent_24072 #16-#19：连从未读过的 src/reports.py 也被拒。
# 修复：`_arm_force_edit_recovery` 在 force_edit 上升沿、且目标未知时发一次
# grep 额度；不放开 read_file / git / 普通 shell_exec，不改变拦截与完成门禁。
# ═══════════════════════════════════════════════════════════════════


def _allowance(ctx: AgentContext, key: str) -> int:
    return int(ctx.metadata.get(key, 0) or 0)


# 与 tests/test_swe_edit_recovery_unknown_target.py 同形的 discovery grep
_GREP_SRC = ToolCall(id="g1", name="grep", params={"pattern": "def index"})
_GREP_SRC_AGAIN = ToolCall(id="g2", name="grep", params={"pattern": "app"})
# D2：门槛通过但**执行失败**的 grep（正则错误 / 缺必需参数），以及正常空结果
_GREP_BAD_REGEX = ToolCall(id="g3", name="grep", params={"pattern": "("})
_GREP_NO_PATTERN = ToolCall(id="g4", name="grep", params={})
_GREP_NO_MATCH = ToolCall(id="g5", name="grep",
                          params={"pattern": "zzz_no_such_symbol_zzz"})

# D1：测试命令在导入期改写**既有源文件**（快照/生成物类测试的真实形态）→
# 工作区真的变了，但测试仍然失败 → 命中"工作区变了但调用失败"的边界重置。
# 用哨兵文件控制生效时机：本用例只允许**第二次** pytest 触发改写。若首次运行就
# 改写，`ever_modified` 会被这次测试副作用置位，失败随即被判定为"无基线的修改后
# 失败"（不再进入修复态），用例根本到不了它要验证的边界。
TAMPER_CONFTEST = '''\
import pathlib

if pathlib.Path("arm_tamper.flag").exists():
    with pathlib.Path("app.py").open("a", encoding="utf-8") as fh:
        fh.write("# tampered by conftest\\n")
'''


class TestForceEditEntryDiscoveryAllowance:
    def test_entry_arms_one_grep_when_target_unknown(self, tmp_path: Path):
        """进入强制期且尚无 edit 失败目标 → 必须发放一次 grep discovery 额度。"""
        project = tmp_path / "entry_arms"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])

        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"])]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=4))

        assert ctx.metadata.get("force_edit") is True, "前置条件：必须真的进入强制期"
        assert str(ctx.metadata.get("edit_failure_target") or "") == "", \
            "前置条件：本用例中尚未发生过 edit 失败"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 1, \
            "强制期入口必须发放一次 grep 目标发现额度"

    def test_entry_keeps_read_allowance_at_zero(self, tmp_path: Path):
        """入口额度只发 grep：定向 read 额度仍只在 edit 失败后发放。"""
        project = tmp_path / "entry_no_read"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])

        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"])]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=4))

        assert ctx.metadata.get("force_edit") is True
        assert _allowance(ctx, "edit_recovery_read_allowance") == 0, \
            "入口不得发放定向 read 额度（read 是定向的，入口还没有目标）"

    def test_entry_does_not_arm_when_target_known(self, tmp_path: Path):
        """已有 edit 失败目标 → 入口不得再发 grep 额度（保持既有不变式）。"""
        project = tmp_path / "entry_known_target"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])

        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"])]
        ctx, _ = asyncio.run(_run_agent(
            project, _ScriptedBackend(script), max_steps=4,
            initial_metadata={"edit_failure_target": "app.py"},
        ))

        assert ctx.metadata.get("force_edit") is True, "前置条件：必须真的进入强制期"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 0, \
            "已有合法 target 时不得再发 grep discovery 额度"

    def test_grep_executes_once_then_rejected(self, tmp_path: Path, monkeypatch):
        """首次 grep 真的执行并拿到源码证据；第二次被拒 —— 一次性额度，非开放权限。"""
        project = tmp_path / "entry_grep_once"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])
        results = _capture_tool_results(monkeypatch)

        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"]),
                  [_GREP_SRC], [_GREP_SRC_AGAIN], None]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=8))

        greps = [r for r in results if r["name"] == "grep"]
        assert len(greps) == 2, f"应观察到两次 grep 调用: {len(greps)}"
        first, second = greps
        assert first["success"] is True and "[FixDriving]" not in first["text"], \
            f"进入强制期后的第一次 grep 必须真的执行: {first['text'][:200]}"
        assert "def index" in first["text"], \
            f"grep 必须真的返回源码内容（而非仅放行）: {first['text'][:200]}"
        assert second["success"] is False and "[FixDriving]" in second["text"], \
            f"第二次 grep 必须被拒: {second['text'][:200]}"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 0, "额度消耗即失效"

    def test_read_file_does_not_consume_the_allowance(self, tmp_path: Path, monkeypatch):
        """被拒的 read_file 不得消耗 grep 额度：额度只认它自己的工具。"""
        project = tmp_path / "entry_read_no_consume"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])
        results = _capture_tool_results(monkeypatch)

        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"]),
                  [_read_file("app.py", "r9")], [_GREP_SRC], None]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=8))

        reads = [r for r in results if r["name"] == "read_file"]
        assert reads[-1]["success"] is False and "[FixDriving]" in reads[-1]["text"], \
            f"强制期内 read_file 必须仍被拒: {reads[-1]['text'][:200]}"
        greps = [r for r in results if r["name"] == "grep"]
        assert greps and greps[0]["success"] is True and "def index" in greps[0]["text"], \
            f"read_file 被拒不得消耗 grep 额度: {greps[0]['text'][:200] if greps else '无'}"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 0

    def test_after_grep_read_file_is_still_blocked(self, tmp_path: Path, monkeypatch):
        """**已知未解决限制**（固化现状，不是期望行为）。

        grep 只解决"该改哪个文件"，不解决"该改成什么"。额度用尽后 read_file 仍被
        结构性拒绝，模型只能靠 grep 返回的行级摘要 + edit 失败后才发放的定向 read
        继续取证。若本用例失败，说明读取限制被有意放开 —— 需同步更新
        agent.py 的拦截语义与本文件的其他断言，而不是当作回归。
        """
        project = tmp_path / "entry_gap_after_grep"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])
        results = _capture_tool_results(monkeypatch)

        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"]),
                  [_GREP_SRC], [_read_file("app.py", "r9")], None]
        asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=8))

        greps = [r for r in results if r["name"] == "grep"]
        reads = [r for r in results if r["name"] == "read_file"]
        assert greps and greps[0]["success"] is True, "grep 额度应已被消费"
        assert reads[-1]["success"] is False and "[FixDriving]" in reads[-1]["text"], \
            "grep 之后 read_file 仍被拒 —— 这是当前未解决的限制，不是回归"

    # ── D1 回归：强制期内的边界重置不得清掉入口额度 ──

    def test_allowance_survives_boundary_reset_inside_force_edit(
            self, tmp_path: Path, monkeypatch):
        """进入强制期 → 额度未消费 → 工作区变了但调用失败 → 仍必须保留取证机会。

        "工作区变了但调用失败"这条边界（agent.py `_ws_changed and not result.success`）
        会调用 `_reset_edit_failure_recovery`，但**不解除** force_edit。修复前那里
        把 grep 额度一并清零，而上升沿不会再来 → 本周期唯一的取证机会永久丢失。
        """
        project = tmp_path / "d1_boundary_reset"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])
        (project / "conftest.py").write_text(TAMPER_CONFTEST, encoding="utf-8")
        results = _capture_tool_results(monkeypatch)

        # 进入强制期后写入哨兵（新文件，不算修复进展/不解除 force_edit），
        # 让第二次 pytest 在导入期改写 app.py 并继续失败。
        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"]),
                  [ToolCall(id="arm", name="write_file",
                            params={"path": "arm_tamper.flag", "content": "1"})],
                  [_PYTEST], [_GREP_SRC], None]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=8))

        # ── 前置条件：必须真的命中那条边界（否则本用例不具区分力）──
        assert "# tampered by conftest" in (project / "app.py").read_text(encoding="utf-8"), \
            "前置条件：测试命令必须真的执行并改动了工作区"
        shell = [r for r in results if r["name"] == "shell_exec"]
        assert shell[-1]["success"] is False, "前置条件：第二次测试必须失败"

        # ── 核心断言 ──
        assert ctx.metadata.get("force_edit") is True, \
            "工作区变动但调用失败 → 仍处于强制期（既有语义不得改变）"
        # 判定句：边界重置若清掉了入口额度，这一步的 grep 会被 FixDriving 拒绝。
        greps = [r for r in results if r["name"] == "grep"]
        assert greps and greps[0]["success"] is True and "def index" in greps[0]["text"], \
            f"强制期内仍必须保留一次取证机会: {greps[0]['text'][:200] if greps else '无'}"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 0, \
            "额度应被这次成功的 grep 正常消费掉（能被消费即证明它没被重置清除）"

    # ── D2 回归：执行失败的 grep 不得永久吃掉唯一额度 ──

    def test_failed_grep_returns_the_allowance(self, tmp_path: Path, monkeypatch):
        """正则错误（ToolResult.err）→ 未取到任何证据 → 额度必须归还。"""
        project = tmp_path / "d2_bad_regex"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])
        results = _capture_tool_results(monkeypatch)

        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"]),
                  [_GREP_BAD_REGEX, _GREP_SRC], [_GREP_SRC_AGAIN], None]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=8))

        greps = [r for r in results if r["name"] == "grep"]
        assert len(greps) == 3, f"应观察到三次 grep 调用: {len(greps)}"
        assert greps[0]["success"] is False and "regex error" in greps[0]["text"], \
            f"第一次必须因正则错误失败: {greps[0]['text'][:200]}"
        assert greps[1]["success"] is True and "def index" in greps[1]["text"], \
            f"额度归还后必须能再做一次有效 grep: {greps[1]['text'][:200]}"
        assert greps[2]["success"] is False and "[FixDriving]" in greps[2]["text"], \
            f"成功消费之后仍必须是一次性额度: {greps[2]['text'][:200]}"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 0

    def test_missing_pattern_grep_returns_the_allowance(self, tmp_path: Path, monkeypatch):
        """缺必需参数（注册表校验失败）→ 同样不得消耗唯一额度。"""
        project = tmp_path / "d2_no_pattern"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])
        results = _capture_tool_results(monkeypatch)

        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"]),
                  [_GREP_NO_PATTERN, _GREP_SRC], None]
        asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=8))

        greps = [r for r in results if r["name"] == "grep"]
        assert len(greps) == 2
        assert greps[0]["success"] is False, f"缺参必须失败: {greps[0]['text'][:200]}"
        assert greps[1]["success"] is True and "def index" in greps[1]["text"], \
            f"额度归还后必须能再做一次有效 grep: {greps[1]['text'][:200]}"

    def test_no_match_grep_still_consumes_the_allowance(self, tmp_path: Path, monkeypatch):
        """边界：空结果是**正常证据**（ToolResult.ok），额度照常消费、不归还。

        这条守住 D2 的归还条件不被放宽 —— 归还只针对"执行失败"，
        否则一次性额度会退化成"可反复试探"。
        """
        project = tmp_path / "d2_no_match"
        _write_flask_project(project)
        _write_extra_files(project, ["module_a.py", "module_b.py"])
        results = _capture_tool_results(monkeypatch)

        script = [[_PYTEST], *_only_reads(["app.py", "module_a.py", "module_b.py"]),
                  [_GREP_NO_MATCH], [_GREP_SRC], None]
        ctx, _ = asyncio.run(_run_agent(project, _ScriptedBackend(script), max_steps=8))

        greps = [r for r in results if r["name"] == "grep"]
        assert len(greps) == 2
        assert greps[0]["success"] is True and "no matches" in greps[0]["text"], \
            f"空结果必须是成功返回: {greps[0]['text'][:200]}"
        assert greps[1]["success"] is False and "[FixDriving]" in greps[1]["text"], \
            f"空结果已消耗额度，第二次必须被拦: {greps[1]['text'][:200]}"
        assert _allowance(ctx, "edit_recovery_grep_allowance") == 0


# ═══════════════════════════════════════════════════════════════════
# 测试 3: 修复计划注入 + 修复阶段状态机
# ═══════════════════════════════════════════════════════════════════


def _messages_text(ctx) -> str:
    """拼接所有消息文本，便于断言。"""
    messages = ctx.metadata.get("messages", [])
    parts = []
    for m in messages:
        if isinstance(m, dict):
            parts.append(m.get("content", "") or "")
        else:
            parts.append(getattr(m, "content", "") or "")
    return " ".join(parts)


class TestRepairPlanAndPhases:
    def test_first_test_failure_injects_repair_plan(self, tmp_path: Path):
        """第一次测试失败 → 注入 [Repair Plan]，提示制定修改方案并指向 edit/write_file。"""
        project = tmp_path / "flask_app4"
        _write_flask_project(project)

        # 脚本: pytest(失败) → read → edit(修复) → pytest(通过) → 结束
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [_PYTEST],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        joined = _messages_text(ctx)
        assert action.type == "complete", f"应 complete, 实际 {action.type}"
        assert "[Repair Plan]" in joined, "测试失败后应注入 Repair Plan 提示"
        assert "edit" in joined and "write_file" in joined, "Repair Plan 应指向 edit/write_file"

    def test_repair_phase_reaches_verify_after_fix(self, tmp_path: Path):
        """修复闭环后，repair_phase 状态机应推进并停留在 verify/done。"""
        project = tmp_path / "flask_app5"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [_PYTEST],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 经历 edit(→edit) 与通过 pytest(→verify)；末轮应处于 verify 或 idle
        phase = ctx.metadata.get("repair_phase", "idle")
        assert phase in ("verify", "idle", "done"), \
            f"修复闭环后阶段应为 verify/idle/done, 实际 {phase}"
        # 确保曾进入修改阶段
        assert backend.calls_seen.count("edit") >= 1, "应实际调用 edit 修改"

    def test_repair_plan_injected_once_not_spammed(self, tmp_path: Path):
        """连续多次测试失败只应注入一次 Repair Plan（避免刷屏）。"""
        project = tmp_path / "flask_app6"
        _write_flask_project(project)

        # 脚本: 失败 → read → edit(无效) → 失败 → read → edit(无效) → 失败 → 结束
        script: list[list[ToolCall] | None] = []
        for i in range(3):
            script.append([_PYTEST])
            script.append([ToolCall(id=f"t{i}b", name="read_file",
                                    params={"path": "app.py"})])
            script.append([ToolCall(id=f"t{i}c", name="edit",
                                    params={"path": "app.py", "mode": "regex_replace",
                                            "old_text": r"def index\(\):",
                                            "new_text": "@app.route('/')\ndef index():"})])
        script.append(None)
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        joined = _messages_text(ctx)
        assert joined.count("[Repair Plan]") == 1, "Repair Plan 应只注入一次，避免重复刷屏"


# ═══════════════════════════════════════════════════════════════════
# 测试 4: 动态修复状态注入 system prompt（面向真实 LLM）
# ═══════════════════════════════════════════════════════════════════


class _CaptureBackend(_ScriptedBackend):
    """记录每次 backend 收到的 system_prompt 和 max_tokens。"""

    def __init__(self, script: list[list[ToolCall] | None]):
        super().__init__(script)
        self.system_prompts: list[str] = []
        self.max_tokens_list: list[int] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        if request.system_prompt:
            self.system_prompts.append(request.system_prompt)
        self.max_tokens_list.append(request.max_tokens)
        return super().invoke(request)


class TestFixStateDirective:
    def test_system_prompt_injects_fix_directive_after_failure(self, tmp_path: Path):
        """测试失败后，下一个 step 的 system prompt 必须包含修复状态指令（逼模型修改）。"""
        project = tmp_path / "flask_app7"
        _write_flask_project(project)

        # step1: pytest 失败 → 持久化 test_failed
        # step2: end_turn —— 此时构建 system prompt，应含"Current Repair State / MUST emit"
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert len(backend.system_prompts) >= 2, "应至少发起 2 次 backend 调用"
        # 第 2 次调用发生在 pytest 失败之后，其 system prompt 应带修复指令
        post_failure = backend.system_prompts[1]
        assert "(dynamic, act on this now)" in post_failure, \
            "失败后 system prompt 应包含动态修复状态块"
        assert "MUST emit" in post_failure and "edit" in post_failure, \
            "修复指令应强制 edit/write_file"

    def test_no_directive_before_failure(self, tmp_path: Path):
        """尚未失败时不应注入修复指令（避免噪音）。"""
        project = tmp_path / "flask_app8"
        _write_flask_project(project)

        # 只 read（无 pytest），repair_phase 保持 idle，不应有修复指令
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="read_file", params={"path": "app.py"})],
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert len(backend.system_prompts) >= 1
        assert "(dynamic, act on this now)" not in backend.system_prompts[0], \
            "未进入修复态前不应注入动态修复指令"

    def test_force_edit_phase_declares_reads_disabled(self, tmp_path: Path):
        """force_edit 置位后，模型必须被告知"读取已被禁用"。

        真实故障（e2e_run_after_fix2，agent_7068）：FixDriving 拦截 read 并把它
        的计数清零（这是刻意的，防止拦截反过来喂数），但动态状态块仍然照读
        reads_after_fail，于是每步都告诉模型 "you have used 0/3 reads since the
        failure"。模型据此认为诊断预算还满着 → 反复 read/shell 被拒 → 8 步零修改
        → Force-edit 预算耗尽。拦截是硬性的，但模型看到的状态不是。
        """
        project = tmp_path / "flask_app12"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),            # 攒满阈值 → 本步结束时 force_edit=True
            [_read_file("app.py")],         # force_edit 生效 → 被拒绝
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=4))

        assert ctx.metadata["force_edit"] is True, "本用例必须真的进入强制修改期"

        # 1) 拒绝结果必须让模型知道这是结构性禁用，而不是一次可重试的失败
        rejected = _messages_text(ctx)
        assert "[FixDriving]" in rejected
        assert "DISABLED" in rejected, "被拒的调用必须显式说明读取已被禁用"
        assert "重复调用不会成功" in rejected, \
            "必须说明重复调用不会成功（否则模型只会换个参数重试）"

        # 2) force_edit 生效后的每个 system prompt 都必须声明禁用，
        #    且不得再宣称还剩读取预算（幽灵预算正是空转的燃料）
        armed = backend.system_prompts[2:]
        assert armed, "force_edit 生效后应至少还有一次 backend 调用"
        for prompt in armed:
            assert "(dynamic, act on this now)" in prompt, "强制期仍应有动态状态块"
            assert "DISABLED" in prompt, \
                f"强制期的 system prompt 必须声明读取已禁用:\n{prompt[-600:]}"
            assert "reads since the failure" not in prompt, \
                "读取已被结构性拒绝，不得再向模型宣称还有读取预算"

    def test_files_already_read_injected_after_failure_and_read(self, tmp_path: Path):
        """pytest 失败后若已读取过文件，下一次 system prompt 必须含 'Files already read' 列表。

        这是第二轮新增的提示，用于减少诊断阶段重复读取。本测试用 _CaptureBackend
        截取真实发送给 backend.invoke() 的 system_prompt，验证提示确实进入请求对象。
        """
        project = tmp_path / "flask_readlist1"
        _write_flask_project(project)

        # step1: pytest 失败 → test_failed=True
        # step2: read_file(app.py) → _read_files_seen={"app.py"}
        # step3: end_turn → 此时构建的 system prompt 应含已读文件列表
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [_read_file("app.py")],
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=3))

        assert ctx.metadata.get("test_failed") is True, "pytest 应失败并设置 test_failed"
        assert "app.py" in ctx.metadata.get("_read_files_seen", set()), \
            "read_file 后 _read_files_seen 应包含 app.py"
        assert len(backend.system_prompts) >= 3, "应至少发起 3 次 backend 调用"

        # 第 3 次调用发生在 pytest 失败 + 读取之后，其 system prompt 应含已读文件列表
        post_read = backend.system_prompts[2]
        assert "Files already read this session" in post_read, \
            "失败且已读取文件后，system prompt 应包含 'Files already read this session'"
        assert "app.py" in post_read, \
            "已读文件列表应包含实际读取过的文件名 app.py"

    def test_files_already_read_omitted_when_no_reads_yet(self, tmp_path: Path):
        """pytest 刚失败但尚未读取任何文件时，不应注入 'Files already read' 行。

        _read_files_seen 为空时，注入空列表没有意义，反而占用 token。
        验证生产代码正确处理空集合。
        """
        project = tmp_path / "flask_readlist2"
        _write_flask_project(project)

        # step1: pytest 失败 → test_failed=True, _read_files_seen={}
        # step2: end_turn → 此时构建的 system prompt 应有修复指令但无已读文件列表
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=2))

        assert ctx.metadata.get("test_failed") is True
        assert ctx.metadata.get("_read_files_seen", set()) == set(), \
            "未读取文件时 _read_files_seen 应为空集"
        assert len(backend.system_prompts) >= 2

        post_failure = backend.system_prompts[1]
        assert "(dynamic, act on this now)" in post_failure, \
            "失败后仍应有动态修复状态块"
        assert "Files already read this session" not in post_failure, \
            "_read_files_seen 为空时不应注入 'Files already read' 行"

    def test_files_already_read_injected_in_force_edit_phase(self, tmp_path: Path):
        """force_edit=True 且已读取过文件时，system prompt 必须含 'Files already read'。

        根因（A/B 实验发现）：原提示放在 elif test_failed 分支内，force_edit=True
        时走 if force_edit 分支，完全跳过该提示。修复后移到分支外，force_edit 和
        test_failed 阶段都能注入。本测试用 _CaptureBackend 截取真实 BackendRequest.
        system_prompt 验证。
        """
        project = tmp_path / "flask_readlist_fe1"
        _write_flask_project(project)

        # step1: pytest 失败 → test_failed=True
        # step2: 3 次诊断读取 → 攒满阈值 → 本步结束时 force_edit=True,
        #        _read_files_seen={app.py, test_app.py, helpers.py}
        # step3: end_turn → force_edit 生效后的 system prompt 应含已读文件列表
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=3))

        assert ctx.metadata.get("force_edit") is True, "本用例必须真的进入强制修改期"
        assert len(ctx.metadata.get("_read_files_seen", set())) >= 3, \
            "诊断读取后 _read_files_seen 应至少包含 3 个文件"
        assert len(backend.system_prompts) >= 3

        # 第 3 次调用在 force_edit 生效之后，其 system prompt 应含已读文件列表
        force_edit_prompt = backend.system_prompts[2]
        assert "Files already read this session" in force_edit_prompt, \
            "force_edit 阶段的 system prompt 应包含 'Files already read this session'"
        # 列表应包含实际读取过的文件名
        assert "app.py" in force_edit_prompt, \
            "已读文件列表应包含 app.py"
        assert "DISABLED" in force_edit_prompt, \
            "force_edit 阶段仍应声明读取已被禁用（与已读文件列表共存）"

    def test_force_edit_directive_guides_edit_mode_preference(self, tmp_path: Path):
        """force_edit 阶段的 system prompt 必须包含编辑模式选择指引。

        根因（第十六阶段审查，agent_30148 vs agent_16668）：失败任务中模型在
        force_edit 阶段选择 append 模式修改已有代码，导致在文件末尾追加不相关内容、
        原始错误逻辑未被替换，测试恶化后陷入恢复困境。成功任务使用 replace_lines。
        本测试验证指引确实进入 force_edit 阶段的真实 BackendRequest.system_prompt。
        """
        project = tmp_path / "flask_editmode1"
        _write_flask_project(project)

        # step1: pytest 失败 → test_failed=True
        # step2: 3 次诊断读取 → 攒满阈值 → force_edit=True
        # step3: end_turn → force_edit 生效后的 system prompt 应含编辑模式指引
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            _diagnostic_reads(),
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=3))

        assert ctx.metadata.get("force_edit") is True, "本用例必须真的进入强制修改期"
        assert len(backend.system_prompts) >= 3

        force_edit_prompt = backend.system_prompts[2]
        # 必须明确引导优先使用 replace_lines / regex_replace
        assert "replace_lines" in force_edit_prompt, \
            "force_edit 指引应提到 replace_lines 模式"
        assert "regex_replace" in force_edit_prompt, \
            "force_edit 指引应提到 regex_replace 模式"
        # 必须说明 append 仅用于添加全新内容，不应用于修改已有逻辑
        assert "append" in force_edit_prompt, \
            "force_edit 指引应提到 append 模式的适用范围"
        assert "never use it to modify existing logic" in force_edit_prompt, \
            "force_edit 指引应明确禁止用 append 修改已有逻辑"
        # 必须提醒编辑后核对目标代码
        assert "verify the target code was actually changed" in force_edit_prompt, \
            "force_edit 指引应提醒编辑后核对目标代码"

    def test_edit_mode_guide_absent_in_idle_phase(self, tmp_path: Path):
        """idle 阶段（未进入修复态）的 system prompt 不应包含编辑模式指引。

        编辑模式指引仅在 force_edit 阶段有意义。idle 阶段 _build_fix_state_directive
        提前返回空字符串，不应注入任何修复状态相关内容。
        """
        project = tmp_path / "flask_editmode_idle1"
        _write_flask_project(project)

        # 直接 end_turn，不触发 pytest 失败或 force_edit
        script: list[list[ToolCall] | None] = [None]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=1))

        assert ctx.metadata.get("force_edit", False) is False
        assert ctx.metadata.get("test_failed", False) is False
        assert backend.system_prompts, "应至少有一次 backend 调用"

        idle_prompt = backend.system_prompts[0]
        # 检查 force_edit 特有的编辑模式指引文本，而非工具定义中的模式名称
        assert "never use it to modify existing logic" not in idle_prompt, \
            "idle 阶段不应包含 force_edit 特有的编辑模式指引"
        assert "verify the target code was actually changed" not in idle_prompt, \
            "idle 阶段不应包含 force_edit 特有的编辑后核对指引"

    def test_files_already_read_omitted_when_only_empty_paths(self, tmp_path: Path):
        """_read_files_seen 只包含空路径时，不应生成 'Files already read' 行。

        过滤空路径，避免输出 ", app.py"（开头多余逗号）或纯空条目。
        用 initial_metadata 直接注入 test_failed=True 和 _read_files_seen={""}，
        避免依赖沙箱 pytest。
        """
        project = tmp_path / "flask_readlist_empty1"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [None]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(
            project, backend, max_steps=1,
            initial_metadata={
                "test_failed": True,
                "repair_phase": "diagnosing",
                "_read_files_seen": {""},
            },
        ))

        assert ctx.metadata.get("test_failed") is True
        assert "" in ctx.metadata.get("_read_files_seen", set())
        assert len(backend.system_prompts) >= 1

        prompt = backend.system_prompts[0]
        assert "(dynamic, act on this now)" in prompt, \
            "test_failed 阶段仍应有动态修复状态块"
        assert "Files already read this session" not in prompt, \
            "_read_files_seen 只含空路径时不应注入 'Files already read' 行"


class TestForceEditMaxTokens:
    """force_edit 阶段必须提高 max_tokens 以覆盖思考模型的推理+工具调用。

    真实故障（zmai_bug_demo v3）：FixDriving 触发后模型连续 3 次返回
    stop_reason=length。API 响应显示 completion_tokens 全部是 reasoning_tokens
    （思考 token），模型没有剩余 token 生成 edit/write_file 工具调用。
    deepseek-v4-flash 默认开启思考模式，且无法通过参数关闭；tool_choice="required"
    在思考模式下返回 HTTP 400。因此唯一可行方案是在 force_edit 阶段提高
    max_tokens，给"思考 + 工具调用"留出足够预算。

    注：用 initial_metadata 直接注入 force_edit=True，避免依赖沙箱 pytest
    （沙箱 Python 无 pytest 模块，无法通过真实 pytest 失败触发 FixDriving）。
    """

    def test_max_tokens_increased_when_force_edit_active(self, tmp_path: Path):
        """force_edit=True 时，BackendRequest.max_tokens 必须 >= 8192。"""
        project = tmp_path / "flask_mt1"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [None]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(
            project, backend, max_steps=1,
            initial_metadata={"force_edit": True, "test_failed": True},
        ))

        assert ctx.metadata["force_edit"] is True
        assert len(backend.max_tokens_list) >= 1
        assert backend.max_tokens_list[0] >= 8192, \
            f"force_edit 阶段 max_tokens 应 >= 8192，实际: {backend.max_tokens_list[0]}"

    def test_max_tokens_default_when_not_force_edit(self, tmp_path: Path):
        """非 force_edit 阶段，max_tokens 保持默认值（不无故提高）。"""
        project = tmp_path / "flask_mt2"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [None]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=1))

        assert ctx.metadata.get("force_edit", False) is False
        for mt in backend.max_tokens_list:
            assert mt <= 4096, \
                f"非 force_edit 阶段不应提高 max_tokens，实际: {mt}"

    def test_max_tokens_restored_after_force_edit_removed(self, tmp_path: Path):
        """force_edit 解除后，后续请求的 max_tokens 应恢复默认值。"""
        project = tmp_path / "flask_mt3"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            None,
        ]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(
            project, backend, max_steps=2,
            initial_metadata={"force_edit": True, "test_failed": True},
        ))

        assert len(backend.max_tokens_list) >= 1
        assert backend.max_tokens_list[0] >= 8192, "force_edit 阶段应提高 max_tokens"
        if len(backend.max_tokens_list) > 1:
            assert backend.max_tokens_list[1] <= 4096, \
                "force_edit 解除后应恢复默认 max_tokens"


class TestFixDirectiveReadCache:
    """test_failed 阶段指令必须明确 [ReadCache] 的含义，减少无价值重复读取。

    v5 实测：36 次 read_file，23 次重复读取，日志多次出现 [ReadCache] 命中。
    ReadCache 机制已正确返回"内容未变化、请复用"的提示，且不计入
    reads_after_fail，但模型仍主动发起命中缓存的读取。在 test_failed 阶段
    指令中明确 [ReadCache] 含义，引导模型复用已有上下文。
    """

    def test_readcache_note_in_test_failed_directive(self, tmp_path: Path):
        """test_failed=True 且未 force_edit 时，系统提示应包含 [ReadCache] 说明。"""
        project = tmp_path / "flask_rc1"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [None]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(
            project, backend, max_steps=1,
            initial_metadata={"test_failed": True, "repair_phase": "diagnose"},
        ))

        assert ctx.metadata.get("test_failed") is True
        assert len(backend.system_prompts) >= 1
        prompt = backend.system_prompts[0]
        assert "[ReadCache]" in prompt, \
            "test_failed 阶段指令应包含 [ReadCache] 说明"
        assert "unchanged" in prompt.lower() or "未变化" in prompt, \
            "应说明 [ReadCache] 表示文件未变化"

    def test_no_readcache_note_when_not_test_failed(self, tmp_path: Path):
        """test_failed=False 时，不应注入 [ReadCache] 说明（避免噪音）。"""
        project = tmp_path / "flask_rc2"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [None]
        backend = _CaptureBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=1))

        assert ctx.metadata.get("test_failed", False) is False
        for prompt in backend.system_prompts:
            assert "[ReadCache]" not in prompt, \
                "非 test_failed 阶段不应注入 [ReadCache] 说明"


class TestPytestStatsClassification:
    """pytest_calls 只统计真实测试执行，不误计环境探测命令。

    第九阶段修复：原逻辑用 "pytest" in cmd 判定，会把 where pytest、
    pip install pytest、cat pytest.ini 等误计为测试执行。改用已有的
    is_test_command() 后，只有段首为测试运行器的真实调用才被计数。
    """

    def test_where_pytest_not_counted_as_pytest_call(self, tmp_path: Path):
        """where pytest 是环境探测，不应计入 pytest_calls。"""
        project = tmp_path / "flask_pc1"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "where pytest"})],
            [ToolCall(id="2", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=3))

        stats = ctx.metadata.get("swe_stats", {})
        assert stats.get("pytest_calls", 0) == 1, \
            f"where pytest 不应被计入，pytest_calls={stats.get('pytest_calls')}"

    def test_real_pytest_command_counted(self, tmp_path: Path):
        """python -m pytest 和完整路径解释器调用均应计入 pytest_calls。"""
        project = tmp_path / "flask_pc2"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            # 定向范围：编辑前重跑完整套件会被 REPEATED_FULL_SUITE 拦截，
            # 本用例验证的是"完整路径解释器调用"能被识别为 pytest 调用。
            [ToolCall(id="2", name="shell_exec",
                      params={"command": r"C:\venv\Scripts\python.exe -m pytest test_app.py -q"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=3))

        stats = ctx.metadata.get("swe_stats", {})
        assert stats.get("pytest_calls", 0) == 2, \
            f"两次真实 pytest 调用均应计入，pytest_calls={stats.get('pytest_calls')}"


# ═══════════════════════════════════════════════════════════════════
# 测试 5: 修改后不重测 → 不得错误判定 completed（release-readiness 审计项）
# ═══════════════════════════════════════════════════════════════════


class TestNoFalseCompletionAfterEdit:
    def test_edit_without_retest_does_not_complete(self, tmp_path: Path):
        """edit 后 end_turn（未重测）→ Agent 必须强制重测，不得误判 completed。

        对应审计项：测试失败 → 修改 → 停止（不重测）绝不能 claim 完成。
        """
        project = tmp_path / "flask_app9"
        _write_flask_project(project)

        # 脚本: pytest(失败) → read → edit(修复但【不重测】) → end_turn
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": "python -m pytest -q"})],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=8))

        # 关键断言：绝不 claim 完成（测试从未全绿）
        assert action.type != "complete", \
            f"测试从未全绿却误判 completed: {action.output}"
        # 且必须注入"强制重测"指令（而不是放行完成）
        assert "全绿运行" in _messages_text(ctx), \
            "应注入 [Workflow] 强制重测提示"
        # 反复拦截而模型始终不推进 → 有界失败（不空转到 max_steps 伪装成 timeout）
        assert action.type == "fail", f"应明确失败: {action.type}"

    def test_edit_then_green_retest_completes(self, tmp_path: Path):
        """对比：edit 后【重测全绿】→ 正常 complete（正向闭环不被破坏）。"""
        project = tmp_path / "flask_app10"
        _write_flask_project(project)

        # 脚本: pytest(失败) → read → edit → pytest(通过) → 完成
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [ToolCall(id="2", name="read_file", params={"path": "app.py"})],
            [ToolCall(id="3", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [_PYTEST],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        assert action.type == "complete", f"重测全绿应正常完成: {action.type}: {action.output}"


# ═══════════════════════════════════════════════════════════════════
# 测试 6: FixDriving 粘性 —— 失败 pytest 重跑不能逃逸强制修改
# ═══════════════════════════════════════════════════════════════════
#
# 真实故障：执着的 LLM 可反复用 shell_exec 重跑失败的 pytest 来"逃逸"
# （失败重跑会清零 reads_after_fail），导致 agent 无限 read/重跑、
# 永远不到 edit，直到 LoopGuard → 超时。
#
# 逃逸的封堵方式随 RC-3 调整：force_edit 不再拦截 pytest 重跑（完成守卫要求
# "修改后重跑验证"，两条指令必须能同时满足），改为**状态层面**封堵 ——
# 重跑既不解除 force_edit，也不重置读取计数，因此不会重新触发 FixDriving，
# agent 仍被确定性地逼入 edit/write_file，最终修复并全绿完成。


class TestFixDrivingStickyEscape:
    def test_failing_pytest_rerun_cannot_escape_force_edit(self, tmp_path: Path):
        """force_edit 下重跑失败 pytest 允许执行但无法逃逸 → 仍被逼入 edit。"""
        project = tmp_path / "flask_app11"
        _write_flask_project(project)

        # step1 pytest(失败) → step2 连续3次read(触发force_edit)
        # step3 再次 shell_exec 跑定向 pytest（允许执行，但不能逃逸）
        # step4 edit(唯一被允许的推进动作) → step5 pytest(全绿) → 完成
        # 用定向范围而非完整套件：编辑前重跑完整套件会被 REPEATED_FULL_SUITE 拦截。
        script: list[list[ToolCall] | None] = [
            [ToolCall(id="1", name="shell_exec",
                      params={"command": f"{sys.executable} -m pytest -q"})],
            _diagnostic_reads(),
            [ToolCall(id="5", name="shell_exec",
                      params={"command": f"{sys.executable} -m pytest test_app.py -q"})],
            [ToolCall(id="6", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": r"def index\(\):",
                              "new_text": "@app.route('/')\ndef index():"})],
            [ToolCall(id="7", name="shell_exec",
                      params={"command": f"{sys.executable} -m pytest -q"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 1) 逃逸闭合（RC-3 语义）：force_edit 下失败 pytest 重跑**允许执行**
        #    （完成守卫要求"修改后重跑验证"，两条指令必须能同时满足），
        #    但它不能成为逃逸口 —— 重跑既不解除 force_edit，也不重置读取计数，
        #    因此 FixDriving 不会被反复喂数，agent 仍被确定性地逼入 edit。
        assert ctx.metadata["swe_stats"]["pytest_calls"] == 3, \
            "force_edit 激活后重跑 pytest 必须真正执行（用于验证）"
        assert ctx.metadata["swe_stats"]["fixdriving_activations"] == 1, \
            "重跑失败 pytest 不得重新触发 FixDriving（否则可无限逃逸）"

        # 2) agent 被逼入 edit：app.py 被真实修改，加上了路由
        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert APP_FIXED_MARKER in app_text, "app.py 应包含 @app.route('/')"

        # 3) 最终完成
        assert action.type == "complete", f"应 complete, 实际 {action.type}: {action.output}"

        # 4) 真实 pytest 全绿
        import subprocess
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=str(project), capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"修复后 pytest 应通过: {r.stdout}{r.stderr}"


# ═══════════════════════════════════════════════════════════════════
# 测试 7: P0-1 根因定位链 —— 闩锁生命周期 / 不伪造诊断态 / 失败分析截断
# ═══════════════════════════════════════════════════════════════════


APP_TWO_BUGS = '''\
from flask import Flask

app = Flask(__name__)


def index():
    return "Hello"


def greet():
    return "Hi"
'''

TEST_TWO_BUGS = '''\
import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_home_returns_200(client):
    assert client.get("/").status_code == 200


def test_greeting_returns_200(client):
    assert client.get("/greet").status_code == 200
'''

_PYTEST = ToolCall(id="p", name="shell_exec",
                   params={"command": f"{sys.executable} -m pytest -q"})

# 只给 index 补路由（greet 仍然 404）→ 第二次重测必然产生**新的**失败
_FIX_INDEX_ROUTE = ToolCall(
    id="e1", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "def index", "new_text": """@app.route('/')
def index"""})

def _capture_messages(monkeypatch, marker: str) -> list[str]:
    """在注入点截获含 marker 的 user 消息原文。

    不能事后从 ctx.metadata["messages"] 取：上下文预算会把消息压缩成摘要 / 挤出
    recent window，观察到的已不是模型当时收到的内容。
    """
    from zmai.context.manager import ContextManager

    captured: list[str] = []
    original = ContextManager.add_message

    def _spy(self, role, content, metadata=None):
        if role == "user" and content and marker in content:
            captured.append(content)
        return original(self, role, content, metadata)

    monkeypatch.setattr(ContextManager, "add_message", _spy)
    return captured


def _capture_repair_plans(monkeypatch) -> list[str]:
    """在注入点截获 [Repair Plan] 消息原文。"""
    return _capture_messages(monkeypatch, "[Repair Plan]")


class TestRootCauseLatchLifecycle:
    def test_parse_exception_does_not_arm_fix_driving(self, tmp_path: Path, monkeypatch):
        """P0-1 A: 解析异常不得伪造"诊断已完成"，也不得因此进入强制修改阶段。"""
        project = tmp_path / "flask_parse_boom"
        _write_flask_project(project)

        import zmai.swe.failure as failure_mod
        real = failure_mod.parse_test_failure
        seen = {"n": 0}

        def _flaky(text, project_root=None):
            seen["n"] += 1
            if seen["n"] == 1:
                raise RuntimeError("parser exploded")
            return real(text, project_root=project_root)

        monkeypatch.setattr(failure_mod, "parse_test_failure", _flaky)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],            # 失败 → 解析抛异常
            _diagnostic_reads(),  # 攒满 fix.read_limit
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert ctx.metadata["swe_stats"].get("fixdriving_activations", 0) == 0, \
            "诊断未就绪时不得触发 FixDriving 强制修改"
        assert "[FixDriving]" not in _messages_text(ctx), "不得注入强制修改提示"
        assert ctx.metadata.get("repair_plan_injected") is False, \
            "解析异常不得伪造『计划已就绪』状态"

    def test_parse_exception_does_not_burn_latch(self, tmp_path: Path, monkeypatch):
        """P0-1 A: 解析异常后，下一次失败必须能重试解析（闩锁未被烧掉）。"""
        project = tmp_path / "flask_parse_retry"
        _write_flask_project(project)

        import zmai.swe.failure as failure_mod
        real = failure_mod.parse_test_failure
        seen = {"n": 0}

        def _flaky(text, project_root=None):
            seen["n"] += 1
            if seen["n"] == 1:
                raise RuntimeError("parser exploded")
            return real(text, project_root=project_root)

        monkeypatch.setattr(failure_mod, "parse_test_failure", _flaky)

        # 第二次用定向范围：编辑前重跑完整套件会被 REPEATED_FULL_SUITE 拦截，
        # 而本用例要验证的是"新的失败会让解析重试"。
        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [ToolCall(id="p2", name="shell_exec",
                      params={"command": f"{sys.executable} -m pytest test_app.py -q"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert seen["n"] >= 2, "下一次失败必须重试解析，不得永久锁死"
        assert ctx.metadata["swe_stats"].get("failure_parser_used", 0) == 1, \
            "只有第二次（成功的）解析应计入失败分析"
        # 闩锁置位即证明 _issue 非空且 _plan_msg（含 file:line/源码上下文）已生成；
        # 消息本身可能被上下文预算压缩，故不断言消息文本（见
        # test_failure_injects_semantic_analysis_and_plan 对文本的覆盖）。
        assert ctx.metadata.get("repair_plan_injected") is True, \
            "重试成功后应真正产出计划"

    def test_second_real_failure_reenters_analysis(self, tmp_path: Path):
        """P0-1 B: 第一次失败修复后的第二次真实失败必须能重新进入失败分析。"""
        project = tmp_path / "flask_two_bugs"
        project.mkdir(parents=True, exist_ok=True)
        (project / "app.py").write_text(APP_TWO_BUGS, encoding="utf-8")
        (project / "test_app.py").write_text(TEST_TWO_BUGS, encoding="utf-8")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],            # 失败 #1（两个测试都失败）
            [_FIX_INDEX_ROUTE],   # 只修 index
            [_PYTEST],            # 失败 #2（greet 仍 404）→ 必须重新分析
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert "@app.route('/')" in app_text, "第一次修改应已生效"
        # failure_parser_used 只在 _issue 非空时 +1：计数为 2 即证明第二次真实失败
        # 重新走完了 parse → plan 全链路，未被首次失败的闩锁阻断。
        assert ctx.metadata["swe_stats"].get("failure_parser_used", 0) == 2, \
            "第二次真实失败必须重新进入 failure analysis，不得被首次闩锁永久阻断"


class TestFailureAnalysisTruncation:
    def test_failure_analysis_keeps_traceback_tail(self, tmp_path: Path, monkeypatch):
        """P0-1 C: 失败分析段必须携带 traceback 尾部/file:line，而非只有 pytest 头部。"""
        project = tmp_path / "flask_noisy"
        _write_flask_project(project)
        # collection 阶段打印大量噪声（-s 使其进入 stdout），把 FAILURES 段挤出输出头部
        (project / "conftest.py").write_text(
            'def pytest_configure(config):\n'
            '    for _ in range(140):\n'
            '        print("[noise] " + "-" * 60)\n',
            encoding="utf-8",
        )
        captured = _capture_repair_plans(monkeypatch)

        script: list[list[ToolCall] | None] = [
            [ToolCall(id="p", name="shell_exec",
                      params={"command": f"{sys.executable} -m pytest -q -s"})],
            None,
        ]
        backend = _ScriptedBackend(script)
        asyncio.run(_run_agent(project, backend))

        assert captured, "应注入 [Repair Plan]"
        section = captured[0].split("失败分析：", 1)[1]
        assert len(section) > 1200, \
            f"失败分析应保留尾部（旧实现只留头部 800 字符）: {len(section)}"
        assert "FAILURES" in section, "应含 traceback 尾部（FAILURES 段）"
        assert "test_app.py:" in section, "应含 file:line"


# ═══════════════════════════════════════════════════════════════════
# 测试 8: LoopGuard 升级门控 —— "诊断未成功不得强制进入普通 edit 路径"
# ═══════════════════════════════════════════════════════════════════

# 不存在的文件：read 必然失败且失败签名稳定 → 用于廉价地攒满 LoopGuard 阈值
_MISSING = "no_such_file_xyz.py"


def _missing_reads(n: int, prefix: str) -> list[ToolCall]:
    return [ToolCall(id=f"{prefix}{i}", name="read_file",
                     params={"path": _MISSING}) for i in range(n)]


class TestLoopGuardEscalationGate:
    def test_no_diagnosis_no_edit_cannot_force_edit(self, tmp_path: Path, monkeypatch):
        """A: 解析持续失败且从未修改过代码 → LoopGuard 达标也不得强制 edit。

        必须同时证明"阈值确实达到了"（loop_recovery_count >= recover_limit），
        否则本用例会退化成"路径没走到"的假通过。
        """
        project = tmp_path / "flask_lg_no_target"
        _write_flask_project(project)

        import zmai.swe.failure as failure_mod

        def _boom(*a, **k):
            raise RuntimeError("parser exploded")

        monkeypatch.setattr(failure_mod, "parse_test_failure", _boom)
        loopguard_msgs = _capture_messages(monkeypatch, "[LoopGuard]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],                    # 失败 → 解析抛异常（诊断不成功）
            _missing_reads(5, "a"),       # 5 次相同失败 → LoopGuard 阻断（恢复 1）
            _missing_reads(5, "b"),       # 再 5 次 → 恢复 2 = 达到 recover_limit
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        # 前提：升级阈值确实被触达（否则断言无意义）
        assert ctx.metadata.get("loop_recovery_count", 0) >= 2, \
            "本用例必须真的走到 LoopGuard 升级阈值"
        # 门控生效：不进入强制修改 / 不伪装成 plan 态
        assert ctx.metadata.get("repair_plan_injected") is False
        assert ctx.metadata.get("force_edit") is not True, \
            "诊断未成功且从未修改过代码时不得强制进入普通 edit 路径"
        assert ctx.metadata.get("repair_phase") == "diagnose", \
            f"应保持诊断态, 实际 {ctx.metadata.get('repair_phase')}"
        assert ctx.metadata["swe_stats"].get("fixdriving_activations", 0) == 0
        joined = _messages_text(ctx)
        assert "[FixDriving]" not in joined, "不得注入 FixDriving 强制修改提示"
        # 恢复提示本身仍应注入（LoopGuard 未被删除），但不得宣告"升级/必须改代码"
        assert loopguard_msgs, "LoopGuard 恢复提示应照常注入"
        assert not any("进入修复升级" in m for m in loopguard_msgs), \
            "未升级时不得声称进入修复升级"

    def test_with_diagnosis_and_edit_still_escalates(self, tmp_path: Path, monkeypatch):
        """B: 已有成功诊断 + 已有真实 edit → LoopGuard 升级行为保持原样。"""
        project = tmp_path / "flask_lg_with_target"
        _write_flask_project(project)
        loopguard_msgs = _capture_messages(monkeypatch, "[LoopGuard]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],                                  # 失败 → 诊断成功（闩锁置位）
            [ToolCall(id="e1", name="edit",             # 真实修改 → ever_modified
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": "def index",
                              "new_text": "@app.route('/')\ndef index"})],
            _missing_reads(5, "a"),                     # 恢复 1
            _missing_reads(5, "b"),                     # 恢复 2 → 应升级
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend))

        assert ctx.metadata.get("repair_plan_injected") is True, "前提：诊断成功"
        assert ctx.metadata.get("ever_modified") is True, "前提：已发生过真实修改"
        assert ctx.metadata.get("loop_recovery_count", 0) >= 2
        assert ctx.metadata.get("force_edit") is True, \
            "有诊断/有修改时 LoopGuard 升级不得被错误封堵"
        assert ctx.metadata.get("repair_phase") == "plan"
        assert any("进入修复升级" in m for m in loopguard_msgs), \
            "升级时应注入升级提示"

    def test_force_edit_budget_still_bounds_escalation(self, tmp_path: Path):
        """C: 升级置位后 MAX_FORCE_EDIT_STEPS 仍然生效，不产生无限强制修改。"""
        project = tmp_path / "flask_lg_budget"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [ToolCall(id="e1", name="edit",
                      params={"path": "app.py", "mode": "regex_replace",
                              "old_text": "def index",
                              "new_text": "@app.route('/')\ndef index"})],
            _missing_reads(5, "a"),
            _missing_reads(5, "b"),     # 此处升级 force_edit
        ]
        # 升级后只发被拒绝的调用：force_edit 预算应把强制期截断为有界失败
        script += [_missing_reads(1, f"s{i}") for i in range(12)]
        script.append(None)

        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=30))

        assert ctx.metadata.get("force_edit") is True, "前提：确实进入强制修改期"
        assert action.type == "fail", \
            f"强制期内无修改应有界失败, 实际 {action.type}: {action.output}"
        assert "force_edit armed" in (action.error or ""), \
            f"应由 force_edit 预算终止: {action.error}"


# ═══════════════════════════════════════════════════════════════════
# 测试 9: P1-A — auto-verify 只能使用与当前 workspace 相关的证据
# ═══════════════════════════════════════════════════════════════════

_FIX_INDEX = ToolCall(
    id="e1", name="edit",
    params={"path": "app.py", "mode": "regex_replace",
            "old_text": "def index", "new_text": """@app.route('/')
def index"""})


class TestAutoVerifyEvidenceScope:
    def test_stale_failure_does_not_block_after_edit(self, tmp_path: Path, monkeypatch):
        """A/B/C/D: 旧 failure → edit → 未重测 → auto-verify 不得再拿旧 failure 判失败。

        观测：auto-verify 通过（无 [Verification Results]），从而走到 completion
        守卫并注入"必须全绿重测"指令 —— 完成仍然被阻断（fail-closed 不变）。
        """
        project = tmp_path / "flask_av_stale"
        _write_flask_project(project)
        verify_msgs = _capture_messages(monkeypatch, "[Verification Results]")
        workflow_msgs = _capture_messages(monkeypatch, "[Workflow]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],        # 失败（edit 之前 = 旧证据）
            [_FIX_INDEX],     # 真实修改工作区
            None,             # 未重测直接 end_turn
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=8))

        assert not verify_msgs, \
            f"edit 前的旧 failure 不得再被当成当前 workspace 的失败: {verify_msgs[:1]}"
        assert any("全绿运行" in m for m in workflow_msgs), \
            "应走到 completion 守卫并要求全绿重测"
        assert action.type != "complete", "未重测不得完成"
        assert ctx.metadata.get("ever_modified") is True

    def test_retest_still_failing_keeps_blocking(self, tmp_path: Path, monkeypatch):
        """E: edit 后重测仍失败 → 必须继续阻断（新失败证据仍参与判定）。"""
        project = tmp_path / "flask_av_retest_fail"
        _write_flask_project(project)
        verify_msgs = _capture_messages(monkeypatch, "[Verification Results]")
        workflow_msgs = _capture_messages(monkeypatch, "[Workflow]")

        script: list[list[ToolCall] | None] = [
            [_PYTEST],        # 失败
            [ToolCall(id="e1", name="edit",   # 真实修改了工作区，但没有修好 bug
                      params={"path": "app.py", "mode": "append",
                              "new_text": "# no-op change"})],
            [_PYTEST],        # 改后重测：仍然失败（这就是"当前 workspace 的失败证据"）
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=8))

        assert action.type != "complete", "重测仍失败时不得完成"
        assert verify_msgs, "改后产生的失败证据必须继续参与 auto-verify 判定"
        assert not any("全绿运行" in m for m in workflow_msgs), \
            "重测仍失败时不应注入『去重测』指令（已经在重测）"

    def test_green_retest_still_completes(self, tmp_path: Path):
        """F: edit 后测试通过 → 不得被旧 failure 阻断。"""
        project = tmp_path / "flask_av_green"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [_FIX_INDEX],
            [_PYTEST],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=8))

        assert action.type == "complete", \
            f"改后全绿应可完成, 实际 {action.type}: {action.error or action.output}"


# ═══════════════════════════════════════════════════════════════════
# 常驻失败证据的生命周期（真实运行，非纯函数）
# ═══════════════════════════════════════════════════════════════════


class TestFailureEvidenceLifecycle:
    def test_failing_run_records_evidence(self, tmp_path: Path):
        """真实失败运行：结构化证据落进 metadata，并进入每步的常驻块。"""
        project = tmp_path / "flask_evidence_fail"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [[_PYTEST], None]
        backend = _ScriptedBackend(script)
        ctx, _action = asyncio.run(_run_agent(project, backend, max_steps=4))

        issue = ctx.metadata.get("last_failure_issue")
        assert issue is not None, "测试失败后应记录结构化失败证据"
        assert issue.test_name, "证据必须含测试名"
        assert issue.file, "证据必须含根因/断言位置"

        assert "FAILURE EVIDENCE" in _build_fix_state_directive(ctx), \
            "常驻证据块必须出现在后续每步的 system prompt 里"

    def test_full_green_clears_evidence(self, tmp_path: Path):
        """全绿后旧证据必须清除，避免围绕已修复的根因继续调查。"""
        project = tmp_path / "flask_evidence_green"
        _write_flask_project(project)

        script: list[list[ToolCall] | None] = [
            [_PYTEST],
            [_FIX_INDEX],
            [_PYTEST],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend, max_steps=8))

        assert action.type == "complete", \
            f"改后全绿应可完成, 实际 {action.type}: {action.error or action.output}"
        assert ctx.metadata.get("test_success_count", 0) >= 1, "前提：确实走到全绿"
        assert ctx.metadata.get("last_failure_issue") is None, "全绿后不得残留失败证据"
