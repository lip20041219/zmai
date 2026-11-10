"""P0-4 — 测试失败证据必须真正到达模型上下文。

问题：ShellTool 已经把测试证据整理成 ``[test summary] + exit N: <头+尾>``，
但 ContextManager.add_tool_result() 又按 ``context.tool_truncate``（默认 500）
做**头部**截断，模型最终只看到 pytest 的 session/collection 头 —— 失败根因
（FAILURES 段、traceback、P0-1 的 file:line:function + source_snippet）全部丢失。

覆盖：
  1. 普通工具仍走默认 500 字符头部截断（行为不变）
  2. 测试失败结果不会只剩 session 头：summary / FAILED / 异常类型可见
  3. traceback frame（file / line / function）在长输出尾部仍可见
  4. P0-1 的 根因位置 + 源码上下文 在最终 context 里仍然可见
  5. 测试成功结果不因证据逻辑膨胀
  6. wiring：只有"测试命令的失败结果"才切到 evidence 预算
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from zmai.agent import AgentContext
from zmai.context.manager import ContextManager
from zmai.swe.agent import SWEAgent, _build_fix_state_directive, _evidence_budget
from zmai.swe.failure import format_failure, parse_test_failure
from zmai.swe.tools import _cap_shell_output, _test_summary_prefix
from zmai.tool import ToolCall, ToolRegistry, ToolResult

# ═══════════════════════════════════════════════════════════════════
# 测试数据
# ═══════════════════════════════════════════════════════════════════

_SESSION_HEAD = (
    "============================= test session starts =============================\n"
    "platform win32 -- Python 3.11.5, pytest-8.0.0, pluggy-1.4.0\n"
    "rootdir: D:\\proj\n"
    "plugins: anyio-4.3.0\n"
    "collected 302 items\n\n"
)

# pytest 帧形态（带函数名）+ raise 点，紧挨 FAILURES 段
_FAILURE_BLOCK = '''\
_____________________________ test_parse_config ______________________________

    def test_parse_config():
>       cfg = parse_config(text)
E       TypeError: parse_config() missing 1 required positional argument: 'defaults'

src/app.py:42: in parse_config
    return _merge(text, defaults)
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
'''

# CPython 原生帧形态（Test 3 用）
_CPYTHON_FRAME_BLOCK = '''\
_____________________________ test_parse_config ______________________________

Traceback (most recent call last):
  File "src/app.py", line 42, in parse_config
    return _merge(text, defaults)
TypeError: parse_config() missing 1 required positional argument: 'defaults'
'''


def _pytest_output(failure_block: str = "", pad_before: int = 0,
                   pad_after: int = 0, failed: bool = True) -> str:
    """构造一段 pytest 输出：session 头 + 可选大段中间输出 + 失败段 + 汇总尾。"""
    pad = "".join(
        f"tests/test_bulk.py::test_bulk_{i:03d} PASSED                       [  1%]\n"
        for i in range(pad_before)
    )
    pad_tail = "".join(
        f"tests/test_bulk.py::test_bulk_tail_{i:03d} PASSED                  [  1%]\n"
        for i in range(pad_after)
    )
    if failed:
        foot = (
            "=========================== short test summary info ===========================\n"
            "FAILED tests/test_app.py::test_parse_config - TypeError: parse_config() "
            "missing 1 required positional argument: 'defaults'\n"
            "========================= 1 failed, 301 passed in 0.16s =========================\n"
        )
    else:
        foot = (
            "============================= 302 passed in 0.42s ==============================\n"
        )
    return _SESSION_HEAD + pad + failure_block + pad_tail + foot


def _shell_error(cmd: str, raw: str) -> str:
    """复现 ShellTool 的失败证据形态：summary 前缀 + exit code + 头尾截断。"""
    return f"{_test_summary_prefix(raw)}exit 1: {_cap_shell_output(raw, cmd, 5000)}"


def _model_view(cm: ContextManager) -> str:
    """模型实际看到的全部上下文文本。"""
    return "\n".join(str(m.get("content", "")) for m in cm.get_context())


# ═══════════════════════════════════════════════════════════════════
# Test 1 — 普通工具保持默认截断
# ═══════════════════════════════════════════════════════════════════


class TestNormalToolUnchanged:
    def test_default_tool_truncate_is_500(self):
        assert ContextManager(config={}).tool_truncate == 500

    def test_normal_tool_result_still_head_truncated(self):
        """普通工具（read_file）仍按默认 500 字符头部截断，尾部丢弃。"""
        cm = ContextManager(config={})
        cm.add_tool_result("read_file", True, "a" * 5000 + "TAIL_MARKER_9f3c")
        msg = cm._recent[-1]["content"]
        assert "...(截断)" in msg
        assert "TAIL_MARKER_9f3c" not in msg
        assert len(msg) < 600

    def test_normal_tool_result_not_enlarged(self):
        """未显式声明证据的调用，budget 与改动前完全一致。"""
        cm = ContextManager(config={"context.tool_truncate": 120})
        cm.add_tool_result("grep", True, "x" * 900)
        assert len(cm._recent[-1]["content"]) < 200


# ═══════════════════════════════════════════════════════════════════
# Test 2 / 3 — 失败证据不会被头部截断吃掉
# ═══════════════════════════════════════════════════════════════════


class TestFailureEvidenceVisible:
    def test_summary_and_failure_detail_survive(self):
        """failure detail 在远端（300 行中间输出之后）仍进入上下文。"""
        cm = ContextManager(config={})
        raw = _pytest_output(_FAILURE_BLOCK, pad_before=300)
        err = _shell_error("python -m pytest -q", raw)
        cm.add_tool_result("shell_exec", False, "", error=err,
                           truncate=cm.test_evidence_chars)

        text = _model_view(cm)
        assert "[test summary]" in text
        assert "FAILED tests/test_app.py::test_parse_config" in text
        assert "TypeError" in text

    def test_traceback_frame_survives(self):
        """traceback frame 在长输出后部仍可见（file / line / function）。"""
        cm = ContextManager(config={})
        raw = _pytest_output(_CPYTHON_FRAME_BLOCK, pad_before=300)
        err = _shell_error("python -m pytest -q", raw)
        cm.add_tool_result("shell_exec", False, "", error=err,
                           truncate=cm.test_evidence_chars)

        text = _model_view(cm)
        assert "src/app.py" in text
        assert "line 42" in text
        assert "parse_config" in text

    def test_without_evidence_budget_failure_detail_is_lost(self):
        """反向对照：默认 500 头部截断下，failure detail 确实看不到（P0-4 的现象）。"""
        cm = ContextManager(config={})
        raw = _pytest_output(_FAILURE_BLOCK, pad_before=300)
        cm.add_tool_result("shell_exec", False, "",
                           error=_shell_error("python -m pytest -q", raw))

        text = _model_view(cm)
        assert "[test summary]" in text      # 前缀保住了计数
        assert "FAILED tests/test_app.py::test_parse_config" not in text
        assert "TypeError" not in text       # 根因被 500 头部截断吃掉


# ═══════════════════════════════════════════════════════════════════
# Test 4 — P0-1 的 file:line:function + source_snippet 仍然可见
# ═══════════════════════════════════════════════════════════════════


class TestP01EvidenceStillVisible:
    def test_root_cause_and_source_snippet_reach_context(self, tmp_path: Path):
        """P0-1 生成的 根因位置 / 源码上下文 必须出现在最终模型上下文里。"""
        src = tmp_path / "src"
        src.mkdir()
        lines = [f"# filler {i}" for i in range(1, 61)]
        lines[41] = "def parse_config(text, defaults=None):"   # 第 42 行
        (src / "app.py").write_text("\n".join(lines), encoding="utf-8")

        cm = ContextManager(config={})
        raw = _pytest_output(_FAILURE_BLOCK, pad_after=300)
        evidence = _shell_error("python -m pytest -q", raw)

        # 1) 工具结果走 evidence 路径（agent.py 的 add_tool_result 调用）
        cm.add_tool_result("shell_exec", False, "", error=evidence,
                           truncate=cm.test_evidence_chars)

        # 2) 修复计划消息（agent.py 的 [Repair Plan] 注入路径）
        issue = parse_test_failure(evidence, tmp_path)
        assert issue is not None
        assert issue.file == "src/app.py"
        assert issue.line == 42
        assert issue.function == "parse_config"
        assert issue.source_snippet
        cm.add_message("user",
                       "[Repair Plan] 测试失败。请按以下闭环立即修复\n失败分析：\n"
                       f"{evidence[:800]}\n\n{format_failure(issue)}")

        text = _model_view(cm)
        assert "根因位置: src/app.py:42" in text
        assert "源码上下文" in text
        assert "def parse_config(text, defaults=None):" in text

    def test_source_snippet_survives_even_when_appended_to_tool_result(self):
        """极端情况：P0-1 证据块被追加在长输出之后，也不能被截断丢掉。"""
        cm = ContextManager(config={})
        padding = "".join(f"noise line {i}\n" for i in range(400))
        evidence = (
            "[test summary] 1 passed, 1 failed, 0 errors\n"
            "exit 1: " + padding
            + "- 根因位置: src/app.py:42  (in parse_config)\n"
            "- 源码上下文（>> 标记根因行）:\n"
            ">>   42 | def parse_config(text, defaults=None):\n"
        )
        cm.add_tool_result("shell_exec", False, "", error=evidence,
                           truncate=cm.test_evidence_chars)

        text = _model_view(cm)
        assert "根因位置" in text
        assert "源码上下文" in text
        assert "def parse_config(text, defaults=None):" in text


# ═══════════════════════════════════════════════════════════════════
# Test 5 — 成功结果不膨胀
# ═══════════════════════════════════════════════════════════════════


class TestPassingRunNotInflated:
    def test_passing_run_uses_default_truncation(self):
        cm = ContextManager(config={})
        raw = _pytest_output(pad_before=300, failed=False)
        out = _test_summary_prefix(raw) + _cap_shell_output(raw, "python -m pytest -q", 10000)
        cm.add_tool_result("shell_exec", True, out)

        text = _model_view(cm)
        assert "[test summary]" in text
        assert len(cm._recent[-1]["content"]) < 600

    def test_evidence_budget_is_bounded(self):
        """即使走 evidence 截断，也有硬上限（不会整段塞进上下文）。"""
        cm = ContextManager(config={})
        raw = _pytest_output(_FAILURE_BLOCK, pad_before=3000)
        cm.add_tool_result("shell_exec", False, "",
                           error=_shell_error("python -m pytest -q", raw),
                           truncate=cm.test_evidence_chars)
        msg = cm._recent[-1]["content"]
        assert len(msg) <= cm.test_evidence_chars + 64
        assert "中间省略" in msg


# ═══════════════════════════════════════════════════════════════════
# Test 6 — wiring：谁走 evidence 预算
# ═══════════════════════════════════════════════════════════════════


class TestEvidenceBudgetWiring:
    def _cm(self) -> ContextManager:
        cm = ContextManager(config={})
        cm.test_evidence_chars = 4321
        return cm

    def _tc(self, name: str, **params) -> ToolCall:
        return ToolCall(id="c1", name=name, params=params)

    def test_failed_test_command_uses_evidence_budget(self):
        cm = self._cm()
        for cmd in ("python -m pytest -q", "pytest tests/", "python -m unittest"):
            tc = self._tc("shell_exec", command=cmd)
            assert _evidence_budget(tc, ToolResult.err("exit 1"), cm) == 4321

    def test_successful_test_run_keeps_default(self):
        cm = self._cm()
        tc = self._tc("shell_exec", command="python -m pytest -q")
        assert _evidence_budget(tc, ToolResult.ok("302 passed"), cm) is None

    def test_non_test_command_keeps_default(self):
        cm = self._cm()
        tc = self._tc("shell_exec", command="git status")
        assert _evidence_budget(tc, ToolResult.err("exit 128"), cm) is None

    def test_non_shell_tool_keeps_default(self):
        cm = self._cm()
        tc = self._tc("read_file", path="pytest.log")
        assert _evidence_budget(tc, ToolResult.err("not found"), cm) is None

    # ── 源码正文是一手证据：read_file 成功结果同样走 evidence 预算 ──
    def test_successful_read_uses_evidence_budget(self):
        """读到的源码正文是模型写 edit 的唯一依据，不得按 500 字符头部截断。

        实测 eval_pydata__xarray-4248：678 行 / 24870 字符的
        xarray/core/formatting.py 注入模型后只剩 530 字符（第 1~15 行），
        模型看不到待改函数体，只能反复重读，随后被 FixDriving 硬拒
        —— 60 步、0 次 edit、全部读操作被拒。
        """
        cm = self._cm()
        tc = self._tc("read_file", path="xarray/core/formatting.py")
        assert _evidence_budget(tc, ToolResult.ok("1|def f():\n"), cm) == 4321

    def test_grep_and_git_keep_default(self):
        cm = self._cm()
        assert _evidence_budget(self._tc("grep", pattern="x"),
                                ToolResult.ok("found 3"), cm) is None
        assert _evidence_budget(self._tc("git", args="status"),
                                ToolResult.ok("clean"), cm) is None

    def test_long_file_read_is_visible_to_model(self):
        """机制级复现：一条 678 行的 read_file 结果注入上下文后必须远多于 500 字符。"""
        cm = ContextManager(config={})
        body = "".join(f"{i}|    def helper_{i}(self, value):\n" for i in range(1, 679))
        tc = ToolCall(id="c1", name="read_file", params={"path": "formatting.py"})
        cm.add_tool_result(name="read_file", success=True, output=body,
                           truncate=_evidence_budget(tc, ToolResult.ok(body), cm))

        msg = [m for m in cm.get_context()
               if "read_file" in str(m.get("content", ""))][-1]["content"]
        assert len(msg) > 3000, f"源码正文仍被过度截断: {len(msg)} 字符"
        assert "中间省略" in msg or "...(截断)" in msg, "截断必须有显式标记"


# ═══════════════════════════════════════════════════════════════════
# Test 7 — 常驻失败证据块：压缩后仍可见、可替换、可清除
# ═══════════════════════════════════════════════════════════════════

# 带数值断言的失败块（pytest 摘要里的 `assert <actual> == <expected>`）
_ASSERT_FAILURE_BLOCK = '''\
_____________________________ test_total ______________________________________

    def test_total():
>       assert total(items) == 5
E       assert 6 == 5

src/calc.py:12: in total
    return sum(values)
'''


def _ctx(metadata: dict, **overrides) -> AgentContext:
    """只带 metadata 的上下文：常驻证据块除 metadata 外不读其它字段。"""
    kwargs: dict = {"agent_id": "evidence", "task": "", "backend": None,
                    "tools": ToolRegistry(), "config": {}, "metadata": dict(metadata)}
    kwargs.update(overrides)
    return AgentContext(**kwargs)


def _parse_issue(text: str, root: Path | None = None):
    return parse_test_failure(_shell_error("python -m pytest -q", text), root)


class TestPersistentFailureEvidence:
    def test_evidence_enters_directive(self):
        """失败后，结构化证据必须出现在每轮模型调用的提示词里。"""
        issue = _parse_issue(_pytest_output(_FAILURE_BLOCK))
        assert issue is not None
        block = _build_fix_state_directive(
            _ctx({"last_failure_issue": issue, "repair_phase": "plan",
                  "test_failed": True}))

        assert "FAILURE EVIDENCE" in block
        assert "test_parse_config" in block          # 测试名
        assert "TypeError" in block                  # 错误类型
        assert "根因位置: src/app.py:42" in block     # 根因文件与行号

    def test_expected_actual_included_when_present(self):
        """expected / actual 存在时必须一并给出。"""
        issue = _parse_issue(_pytest_output(_ASSERT_FAILURE_BLOCK))
        assert issue is not None
        assert (issue.expected, issue.actual) == ("5", "6")   # 前提

        block = _build_fix_state_directive(_ctx({"last_failure_issue": issue}))
        assert "expected=5, actual=6" in block

    def test_evidence_survives_compaction(self):
        """压缩把历史消息压成计数摘要后，根因位置仍可从 metadata 重建。"""
        cm = ContextManager(config={"context.max_chars": 1200,
                                    "context.recent_window": 1,
                                    "context.tool_result_window": 1})
        raw = _pytest_output(_FAILURE_BLOCK, pad_before=200)
        evidence = _shell_error("python -m pytest -q", raw)
        cm.add_tool_result("shell_exec", False, "", error=evidence,
                           truncate=cm.test_evidence_chars)
        issue = _parse_issue(_pytest_output(_FAILURE_BLOCK))
        cm.add_message("user", "[Repair Plan] 测试失败。失败分析：\n"
                               f"{format_failure(issue)}")
        assert "根因位置" in _model_view(cm), "前提：压缩前证据在上下文里"

        for i in range(8):
            cm.add_message("user", f"filler round {i} " + "x" * 200)
        cm.compact()

        view = _model_view(cm)
        assert "根因位置" not in view, "前提：压缩后原始证据已滚出窗口"
        assert "Previous" in view or "历史摘要" in view, "前提：摘要确实生成了"

        block = _build_fix_state_directive(_ctx({"last_failure_issue": issue,
                                                 "repair_phase": "plan"}))
        assert "根因位置: src/app.py:42" in block, \
            f"压缩后常驻证据块必须仍给出根因位置:\n{block}"

    def test_no_evidence_no_block(self):
        """没有 FailureIssue 时不得注入空白或虚假的失败信息。"""
        assert _build_fix_state_directive(_ctx({})) == "", "idle 且无证据应保持空"

        block = _build_fix_state_directive(
            _ctx({"repair_phase": "plan", "test_failed": True}))
        assert block, "修复态本身仍应有状态块"
        assert "FAILURE EVIDENCE" not in block
        assert "根因位置" not in block

    def test_new_failure_replaces_old_evidence(self):
        """新失败必须替换旧证据，不得两条并存。"""
        old = _parse_issue(_pytest_output(_FAILURE_BLOCK))          # test_parse_config
        new = _parse_issue(_pytest_output(_ASSERT_FAILURE_BLOCK))   # test_total
        meta = {"last_failure_issue": old, "repair_phase": "plan"}
        assert "test_parse_config" in _build_fix_state_directive(_ctx(meta))

        meta["last_failure_issue"] = new
        block = _build_fix_state_directive(_ctx(meta))
        assert "test_total" in block
        assert "test_parse_config" not in block, "旧证据不得残留"

    def test_block_excludes_source_snippet(self, tmp_path: Path):
        """常驻块不夹带源码片段与 traceback（每步固定开销）。"""
        src = tmp_path / "src"
        src.mkdir()
        lines = [f"# filler {i}" for i in range(1, 61)]
        lines[41] = "def parse_config(text, defaults=None):"
        (src / "app.py").write_text("\n".join(lines), encoding="utf-8")

        issue = _parse_issue(_pytest_output(_FAILURE_BLOCK), tmp_path)
        assert issue is not None and issue.source_snippet, "前提：确实解析出了片段"
        # 一次性注入路径（[Repair Plan]）行为不变：默认仍带片段
        assert "源码上下文" in format_failure(issue)

        block = _build_fix_state_directive(_ctx({"last_failure_issue": issue}))
        assert "根因位置" in block
        assert "源码上下文" not in block, "常驻块不得重复附加源码片段"

    def test_unparsable_output_is_safe(self):
        """解析失败（无可用证据）时不得伪造证据，也不得抛异常。"""
        assert parse_test_failure("", None) is None
        assert parse_test_failure("   \n  ", None) is None

        block = _build_fix_state_directive(_ctx({"repair_phase": "plan"}))
        assert "FAILURE EVIDENCE" not in block

    def test_initialize_clears_stale_evidence(self, tmp_path: Path):
        """新任务初始化后不得残留上一任务的失败证据。"""
        agent = SWEAgent("evidence_init")
        ctx = _ctx({"last_failure_issue": _parse_issue(_pytest_output(_FAILURE_BLOCK))},
                   config={"project_path": str(tmp_path)})
        assert ctx.metadata.get("last_failure_issue") is not None

        asyncio.run(agent.initialize(ctx))

        assert ctx.metadata.get("last_failure_issue") is None
