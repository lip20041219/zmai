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

from pathlib import Path

from zmai.context.manager import ContextManager
from zmai.swe.agent import _test_evidence_budget
from zmai.swe.failure import format_failure, parse_test_failure
from zmai.swe.tools import _cap_shell_output, _test_summary_prefix
from zmai.tool import ToolCall, ToolResult

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
            assert _test_evidence_budget(tc, ToolResult.err("exit 1"), cm) == 4321

    def test_successful_test_run_keeps_default(self):
        cm = self._cm()
        tc = self._tc("shell_exec", command="python -m pytest -q")
        assert _test_evidence_budget(tc, ToolResult.ok("302 passed"), cm) is None

    def test_non_test_command_keeps_default(self):
        cm = self._cm()
        tc = self._tc("shell_exec", command="git status")
        assert _test_evidence_budget(tc, ToolResult.err("exit 128"), cm) is None

    def test_non_shell_tool_keeps_default(self):
        cm = self._cm()
        tc = self._tc("read_file", path="pytest.log")
        assert _test_evidence_budget(tc, ToolResult.err("not found"), cm) is None
