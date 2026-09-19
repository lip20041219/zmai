"""P1/P2 — Failure Parser 与 Fix Planner 单测。

覆盖：
  - P2: pytest traceback → 语义化问题（404→路由缺失、KeyError→字段缺失、依赖缺失）
  - P1: 语义化失败 → 有序修复计划（NotFound→路由步骤，MissingField→字段步骤）
  - 端到端：Agent 修复失败时注入的 [Repair Plan] 含语义分析与计划
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from zmai.swe.failure import format_failure, parse_test_failure
from zmai.swe.fix_planner import format_plan, generate_fix_plan

# ═══════════════════════════════════════════════════════════════════
# P2 — Failure Parser
# ═══════════════════════════════════════════════════════════════════


class TestFailureParser:
    def test_parse_404_as_route_missing(self):
        tb = (
            "FAILED test_app.py::test_home_returns_200 - AssertionError: "
            "assert 404 == 200\n"
            "+  where 404 = <WrapperTestResponse streamed [404 NOT FOUND]>.status_code"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert issue.test_name == "test_home_returns_200"
        assert issue.issue_type == "NotFound"
        assert "404" in issue.semantic or "路由" in issue.semantic or "状态码" in issue.semantic
        assert issue.hints, "404 语义应给出修复提示"

    def test_parse_keyerror_as_missing_field(self):
        tb = (
            "FAILED test_app.py::test_api_returns_username - KeyError: 'username'\n"
            "data = client.get('/api/user').get_json()\n"
            "E   KeyError: 'username'"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert issue.issue_type == "MissingField"
        assert "username" in issue.semantic
        assert issue.hints

    def test_parse_missing_dependency(self):
        tb = (
            "test_app.py:2: in <module>\n"
            "from app import app\n"
            "ModuleNotFoundError: No module named 'flask'"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert issue.issue_type == "MissingDependency"
        assert "flask" in issue.semantic

    def test_parse_empty_returns_none(self):
        assert parse_test_failure("") is None
        assert parse_test_failure("   ") is None

    def test_format_failure_contains_semantic(self):
        issue = parse_test_failure("assert 404 == 200")
        text = format_failure(issue)
        assert "语义化根因" in text
        assert "修复提示" in text


# ═══════════════════════════════════════════════════════════════════
# P0-1 — Root-Cause Localization：traceback frame → file:line:function → 源码上下文
# ═══════════════════════════════════════════════════════════════════


class TestRootCauseLocalization:
    def test_parses_standard_cpython_frame(self):
        """Test 1：File "src/app.py", line 42, in parse_config。"""
        issue = parse_test_failure(
            'File "src/app.py", line 42, in parse_config\n'
            "    return json.loads(raw)\n"
            "TypeError: expected str\n"
        )
        assert issue is not None
        assert issue.file == "src/app.py"
        assert issue.line == 42
        assert issue.function == "parse_config"

    def test_prefers_source_frame_over_test_frame(self):
        """Test 2：测试帧 + 源码帧 → 定位源码帧，而不是测试帧。"""
        tb = (
            "_____________ test_parse _____________\n"
            "tests/test_app.py:10: in test_parse\n"
            "    parse_config()\n"
            "src/app.py:42: in parse_config\n"
            "    return json.loads(raw)\n"
            "TypeError: expected str\n"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert (issue.file, issue.line) == ("src/app.py", 42), \
            f"应定位到被测源码帧: {issue.file}:{issue.line}"
        assert issue.function == "parse_config"
        assert "test_app.py" not in issue.file

    def test_prefers_deepest_frame_in_pytest_long_traceback(self):
        """真实 pytest 长 traceback：测试帧 → 调用帧 → raise 点，应取 raise 点。"""
        tb = (
            "test_ledger.py:5: \n"
            "    l = Ledger()\n"
            "ledger.py:28: in __init__\n"
            '    initial_balance = self._validate_amount(initial_balance, "x")\n'
            "ledger.py:46: ValueError\n"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert (issue.file, issue.line) == ("ledger.py", 46), \
            f"应定位到最深的 raise 帧: {issue.file}:{issue.line}"

    def test_skips_pytest_and_stdlib_internal_frames(self):
        """pytest / site-packages 内部帧不得被当作根因位置。"""
        tb = (
            "tests/test_app.py:10: in test_x\n"
            "    f()\n"
            'File "/usr/lib/python3.11/site-packages/_pytest/python.py", '
            "line 1943, in runtest\n"
            "    self.ihook.pytest_runtest_protocol(item=item)\n"
            'File "/usr/lib/python3.11/importlib/__init__.py", line 126, in import_module\n'
            "    return _bootstrap._gcd_import(name[level:])\n"
            'File "src/app.py", line 42, in parse_config\n'
            "TypeError: expected str\n"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert (issue.file, issue.line) == ("src/app.py", 42), \
            f"应跳过内部帧: {issue.file}:{issue.line}"

    def test_skips_relative_pytest_frame(self):
        """相对路径的 _pytest 帧同样要跳过（不能被当成项目源码）。"""
        tb = (
            "tests/test_app.py:10: in test_x\n"
            "    f()\n"
            "_pytest/python.py:1943: in runtest\n"
            "    self.ihook.pytest_runtest_protocol(item=item)\n"
            "src/app.py:42: in parse_config\n"
            "TypeError: expected str\n"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert (issue.file, issue.line) == ("src/app.py", 42), \
            f"应跳过相对路径的内部帧: {issue.file}:{issue.line}"

    def test_underscore_project_dir_is_not_internal(self):
        """项目内的 `_vendor/` 这类下划线目录不是内部帧，必须照常定位。"""
        issue = parse_test_failure(
            'File "_vendor/parser.py", line 8, in load\nValueError: bad\n')
        assert issue is not None
        assert (issue.file, issue.line) == ("_vendor/parser.py", 8), \
            f"下划线目录不得被误判为内部帧: {issue.file}:{issue.line}"

    def test_windows_path_with_drive_letter(self):
        """Test 4：C:\\... 盘符下的路径不能因 ':' 或反斜杠解析错位。"""
        tb = (
            'File "C:\\project\\src\\app.py", line 42, in parse_config\n'
            "    return json.loads(raw)\n"
            "TypeError: expected str\n"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert issue.file == "C:\\project\\src\\app.py"
        assert issue.line == 42
        assert issue.function == "parse_config"

    def test_windows_pytest_raise_frame(self):
        """Windows 路径 + pytest raise 形态（file:line: Exception）也要能定位。"""
        issue = parse_test_failure("C:\\project\\ledger.py:46: ValueError\n")
        assert issue is not None
        assert issue.file == "C:\\project\\ledger.py"
        assert issue.line == 46

    def test_source_snippet_contains_target_line_and_context(self, tmp_path: Path):
        """Test 3：源码片段包含目标行与上下文，且行号正确。"""
        src = tmp_path / "src"
        src.mkdir()
        body = "".join(f"line_{i} = {i}\n" for i in range(1, 41))
        (src / "app.py").write_text(body, encoding="utf-8")

        issue = parse_test_failure(
            'File "src/app.py", line 20, in parse_config\n',
            project_root=tmp_path,
        )
        assert issue is not None
        assert issue.source_snippet, "应生成源码上下文"
        assert ">>   20 | line_20 = 20" in issue.source_snippet, \
            f"目标行应被标记: {issue.source_snippet}"
        # 上下文 = ±15 行
        assert "    5 | line_5 = 5" in issue.source_snippet
        assert "   35 | line_35 = 35" in issue.source_snippet
        assert "line_4 =" not in issue.source_snippet, "不应包含窗口外的行"
        assert "line_36 =" not in issue.source_snippet

    def test_source_snippet_clamps_at_file_start(self, tmp_path: Path):
        """行号靠近文件头/尾时窗口必须被夹紧，不越界。"""
        (tmp_path / "tiny.py").write_text(
            "".join(f"v{i} = {i}\n" for i in range(1, 4)), encoding="utf-8")
        issue = parse_test_failure(
            'File "tiny.py", line 2, in f\n', project_root=tmp_path)
        assert issue is not None
        assert issue.source_snippet.count("\n") == 2, \
            f"3 行文件应给出 3 行片段: {issue.source_snippet!r}"
        assert ">>    2 | v2 = 2" in issue.source_snippet

    def test_missing_source_file_is_safe(self):
        """Test 5：traceback 指向不存在的文件 → 不崩溃，保留定位，片段为空。"""
        tb = (
            'File "/no/such/dir/ghost.py", line 7, in gone\n'
            "NameError: name 'x' is not defined\n"
        )
        issue = parse_test_failure(tb, project_root="D:/definitely/not/here")
        assert issue is not None
        assert issue.file == "/no/such/dir/ghost.py"
        assert issue.line == 7
        assert issue.function == "gone"
        assert issue.source_snippet == ""

    def test_no_traceback_does_not_crash(self):
        """Test 6：没有 frame 的普通失败文本不得让 parser 崩溃。"""
        for text in ("assert 404 == 200", "KeyError: 'username'", "boom\n"):
            issue = parse_test_failure(text)
            assert issue is not None
            assert issue.source_snippet == ""
            assert issue.function == ""


# ═══════════════════════════════════════════════════════════════════
# P1 — Fix Planner
# ═══════════════════════════════════════════════════════════════════


class TestFixPlanner:
    def test_route_failure_generates_route_plan(self):
        issue = parse_test_failure("assert 404 == 200")
        plan = generate_fix_plan(issue)
        assert not plan.is_empty
        assert plan.issue_type == "NotFound"
        assert any("route" in s.lower() or "路由" in s for s in plan.steps), \
            f"404 计划应含路由步骤: {plan.steps}"

    def test_field_failure_generates_field_plan(self):
        issue = parse_test_failure("KeyError: 'username'")
        plan = generate_fix_plan(issue)
        assert not plan.is_empty
        assert plan.issue_type == "MissingField"
        assert any("字段" in s for s in plan.steps)

    def test_generic_failure_gets_default_steps(self):
        issue = parse_test_failure("assert 1 == 2")
        plan = generate_fix_plan(issue)
        assert not plan.is_empty
        # 未知断言 → 通用计划，仍包含 修改→验证 步骤
        assert any("edit" in s or "write_file" in s for s in plan.steps)

    def test_format_plan(self):
        issue = parse_test_failure("assert 404 == 200")
        plan = generate_fix_plan(issue)
        text = format_plan(plan)
        assert "Fix Plan" in text
        assert "1." in text


# ═══════════════════════════════════════════════════════════════════
# 端到端：Agent 修复失败时注入语义化计划
# ═══════════════════════════════════════════════════════════════════


def _messages_text(ctx) -> str:
    parts = []
    for m in ctx.metadata.get("messages", []):
        if isinstance(m, dict):
            parts.append(m.get("content", "") or "")
        else:
            parts.append(getattr(m, "content", "") or "")
    return " ".join(parts)


class TestRepairPlanEndToEnd:
    def test_failure_injects_semantic_analysis_and_plan(self, tmp_path: Path):
        """Agent 首次失败时应注入 [Repair Plan]，且含语义分析与修复计划。"""
        from collections.abc import Iterator

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
        from zmai.tool import ToolCall, ToolRegistry

        # 真实 Flask 项目（缺路由 → 404）
        (tmp_path / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n\n"
            "def index():\n    return 'Hello'\n",
            encoding="utf-8",
        )
        (tmp_path / "test_app.py").write_text(
            "import pytest\nfrom app import app\n\n"
            "@pytest.fixture\ndef client():\n"
            "    app.config['TESTING'] = True\n"
            "    with app.test_client() as c:\n"
            "        yield c\n\n"
            "def test_home_returns_200(client):\n"
            "    assert client.get('/').status_code == 200\n",
            encoding="utf-8",
        )

        class B(Backend):
            name = "e2e_fix"

            def __init__(self):
                self._i = 0

            def invoke(self, request: BackendRequest) -> BackendResponse:
                self._i += 1
                if self._i == 1:
                    tc = [ToolCall(id="1", name="shell_exec",
                                   params={"command": "python -m pytest -q"})]
                else:
                    tc = None
                return BackendResponse(content="", tool_calls=tc,
                                       usage=TokenUsage(1, 1),
                                       stop_reason="tool_use" if tc else "end_turn")

            def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
                yield BackendEvent(type="done", data="", index=1)

            @property
            def capabilities(self):
                return {BackendCapability.TOOL_USE}

        agent = SWEAgent("e2e_fix")
        ctx = AgentContext(
            agent_id="e2e_fix",
            task=f"项目在 {tmp_path}。修复 bug 使测试通过。",
            backend=B(),
            tools=ToolRegistry(),
            config={"project_path": str(tmp_path), "timeout": 30},
            metadata={},
        )
        asyncio.run(agent.initialize(ctx))
        asyncio.run(agent.step(ctx))  # step1: pytest 失败 → 注入计划

        joined = _messages_text(ctx)
        assert "[Repair Plan]" in joined
        # P2 语义化：识别出 404/路由
        assert "404" in joined or "状态码" in joined or "路由" in joined, \
            f"应含语义化分析: {joined}"
        # P1 计划：包含 Fix Plan 步骤
        assert "Fix Plan" in joined, f"应含修复计划: {joined}"
        assert "edit" in joined or "write_file" in joined
        # P0-1 接入：真实 pytest 失败 → 根因位置 + 源码上下文进入 Agent 上下文
        assert "根因位置" in joined, f"应含根因位置: {joined[-1500:]}"
        assert "源码上下文" in joined, f"应含源码片段: {joined[-1500:]}"
        assert ">>" in joined, "源码片段应标出根因行"
