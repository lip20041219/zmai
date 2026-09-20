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


# ═══════════════════════════════════════════════════════════════════
# P0-1 follow-up — 语义解析精度（长输出 / [test summary] 前缀）
# ═══════════════════════════════════════════════════════════════════

def _noise(lines: int = 240) -> str:
    """前置噪声：把 FAILURES 段挤出输出头部（真实 Runtime 验证的场景）。"""
    return "".join(f"[noise] line {i:03d} " + "-" * 60 + "\n" for i in range(lines))


# ShellTool 注入失败证据时会前置 [test summary] 与 exit code
_PREFIX = "[test summary] 1 passed, 1 failed, 0 errors\nexit 1: "

# 真实 pytest 输出的 FAILURES 段（fixture 项目 app.py:7 的 ValueError）
_FAILURES_VALUEERROR = """\
================================== FAILURES ===================================
______________________ test_parse_config_splits_on_comma ______________________

    def test_parse_config_splits_on_comma():
>       assert parse_config("a=1,b=2") == {"a": "1", "b": "2"}
               ^^^^^^^^^^^^^^^^^^^^^^^

test_app.py:11:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

text = 'a=1,b=2'

    def parse_config(text):
        \"\"\"Parse 'k=v,k=v' into a dict.\"\"\"
        pairs = text.split(";")            # BUG: should be ","
>       return dict(p.split("=") for p in pairs)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       ValueError: dictionary update sequence element #0 has length 3; 2 is required

app.py:7: ValueError
=========================== short test summary info ===========================
FAILED test_app.py::test_parse_config_splits_on_comma - ValueError: dictionar...
1 failed, 1 passed in 0.16s
"""

# 长噪声在前：FAILURES 段落在 1000 字符之后
_LONG_VALUEERROR = _PREFIX + "============================= test session starts =============================\ncollected 2 items\n\n" + _noise() + _FAILURES_VALUEERROR  # noqa: E501

# 长噪声 + KeyError（命中 MissingField 语义规则，用于验证不再退化成 Generic）
_LONG_KEYERROR = _PREFIX + _noise() + """\
================================== FAILURES ===================================
_______________________________ test_response_payload _______________________________

    def test_response_payload():
>       assert payload["user_id"] == 7
E       KeyError: 'user_id'

app.py:12: KeyError
=========================== short test summary info ===========================
FAILED test_app.py::test_response_payload - KeyError: 'user_id'
1 failed, 1 passed in 0.16s
"""


class TestSemanticParsingWindow:
    """语义解析必须命中 FAILURES 段，而不是输出头部（detail 窗口修复）。"""

    def test_error_type_found_after_test_summary_prefix(self):
        """ShellTool 前置的 [test summary] 不再是 ^ 锚点的障碍。"""
        issue = parse_test_failure(_PREFIX + _FAILURES_VALUEERROR)
        assert issue is not None
        assert issue.error_type == "ValueError"

    def test_test_name_found_beyond_head_window(self):
        """FAILURES 位于前 1000 字符之后，仍能识别失败测试名。"""
        assert _LONG_VALUEERROR.index("FAILURES") > 1000      # 场景成立
        issue = parse_test_failure(_LONG_VALUEERROR)
        assert issue is not None
        assert issue.test_name == "test_parse_config_splits_on_comma"

    def test_semantic_rule_not_degraded_to_generic(self):
        """长噪声 + 语义规则可命中的失败 → 具体语义，而不是 Generic。"""
        issue = parse_test_failure(_LONG_KEYERROR)
        assert issue is not None
        assert issue.test_name == "test_response_payload"
        assert issue.error_type == "KeyError"
        assert issue.issue_type == "MissingField"
        assert "user_id" in issue.semantic

    def test_short_failure_unchanged(self):
        """短输出（≤ 窗口）行为不变：整段参与语义解析。"""
        text = (
            "FAILED test_app.py::test_home_returns_200 - AssertionError: "
            "assert 404 == 200\n"
            "+  where 404 = <WrapperTestResponse streamed [404 NOT FOUND]>.status_code"
        )
        issue = parse_test_failure(text)
        assert issue is not None
        assert issue.test_name == "test_home_returns_200"
        assert issue.issue_type == "NotFound"
        # 该文本只有 short summary 行，没有 `E   Xxx:` / 顶格 traceback 行，
        # error_type 与修复前一致（"Error"）——不是本轮引入的回归。

    def test_short_real_failure_full_semantics(self):
        """真实短输出（含 FAILURES 段）语义解析完整。"""
        issue = parse_test_failure(_PREFIX + _FAILURES_VALUEERROR)
        assert issue is not None
        assert issue.test_name == "test_parse_config_splits_on_comma"
        assert issue.error_type == "ValueError"
        assert (issue.file, issue.line) == ("app.py", 7)


class TestErrorTypeDetection:
    """异常类型识别 —— 逐行匹配，但不误伤普通文本。"""

    def test_pytest_e_prefixed_line(self):
        issue = parse_test_failure("E       TypeError: unsupported operand type(s)\n")
        assert issue is not None
        assert issue.error_type == "TypeError"

    def test_leading_exception_name_still_matches(self):
        """原生 traceback 形态（异常名顶格）不能回归。"""
        issue = parse_test_failure("ValueError: bad value\napp.py:3: ValueError\n")
        assert issue is not None
        assert issue.error_type == "ValueError"

    def test_plain_text_not_misread(self):
        """源码回显 / 普通叙述不得被当成异常类型（MULTILINE 后仍不误伤）。"""
        for text in (
            "some prose mentioning ValueError in the middle of a sentence\n",
            "raise KeyError('source echo, not the failure')\n",
            "# ValueError is documented below\n",
            "assert isinstance(exc, TypeError)\n",
        ):
            issue = parse_test_failure(text)
            assert issue is not None, text
            assert issue.error_type == "Error", f"{text!r} → {issue.error_type}"

    def test_multiline_source_echo_does_not_override(self):
        """多行文本里异常名只出现在源码回显中 → 不识别为异常类型。"""
        text = (
            "app.py:9: in <module>\n"
            "    TypeError = object  # a variable named like an exception\n"
        )
        issue = parse_test_failure(text)
        assert issue is not None
        assert issue.error_type == "Error"


class TestRootCauseNoRegression:
    """P0-1 原有定位能力在长输出下不得回归。"""

    def test_frames_and_snippet_survive_long_output(self, tmp_path: Path):
        lines = [f"# filler {i}" for i in range(1, 61)]
        lines[41] = 'pairs = text.split(";")'
        (tmp_path / "app.py").write_text("\n".join(lines), encoding="utf-8")

        text = _PREFIX + _noise() + (
            "Traceback (most recent call last):\n"
            '  File "app.py", line 42, in parse_config\n'
            "ValueError: bad config\n"
        )
        assert text.index('File "app.py"') > 1000      # 根因帧在头部窗口之外

        issue = parse_test_failure(text, tmp_path)
        assert issue is not None
        assert (issue.file, issue.line, issue.function) == ("app.py", 42, "parse_config")
        assert "text.split" in issue.source_snippet
        assert ">>" in format_failure(issue)


# ═══════════════════════════════════════════════════════════════════
# P1 — 多 failure 归因一致性（test_name / file:line / snippet 必须同源）
# ═══════════════════════════════════════════════════════════════════

# 两个不同文件、不同异常的 failure（真实 pytest -q 输出形态）
_MULTI_TWO_FILES = """\
=================================== FAILURES ===================================
_________________________________ test_alpha __________________________________

    def test_alpha():
>       check_alpha()

test_x.py:6:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def check_alpha():
>       raise ValueError("alpha boom")
E       ValueError: alpha boom

alpha.py:2: ValueError
__________________________________ test_beta __________________________________

    def test_beta():
>       check_beta()

test_x.py:10:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def check_beta():
        data = {}
>       return data["k"]
E       KeyError: 'k'

beta.py:3: KeyError
=========================== short test summary info ============================
FAILED test_x.py::test_alpha - ValueError: alpha boom
FAILED test_x.py::test_beta - KeyError: 'k'
2 failed in 0.15s
"""

# 同一文件两个 assert failure（无源码 frame，帧全在测试文件内）
_MULTI_SAME_FILE = """\
=================================== FAILURES ===================================
_________________________________ test_first __________________________________

    def test_first():
>       assert 1 == 2
E       assert 1 == 2

test_y.py:2: AssertionError
_________________________________ test_second _________________________________

    def test_second():
>       assert 3 == 4
E       assert 3 == 4

test_y.py:6: AssertionError
=========================== short test summary info ============================
FAILED test_y.py::test_first - assert 1 == 2
FAILED test_y.py::test_second - assert 3 == 4
2 failed in 0.08s
"""

# collection/import error（无 FAILURES 段，只有 ERRORS 段与 import traceback）
_COLLECTION_ERROR = """\
==================================== ERRORS ====================================
_________________________ ERROR collecting test_z.py __________________________
ImportError while importing test module 'test_z.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
test_z.py:1: in <module>
    import nonexistent_mod_xyz
E   ModuleNotFoundError: No module named 'nonexistent_mod_xyz'
=========================== short test summary info ============================
ERROR test_z.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.41s
"""


# 类内测试的多 failure（真实 pytest -q 输出原样捕获）：
# 头名形态为 `TestAlpha.test_alpha`，与模块级 `test_alpha` 不同。
_MULTI_CLASS_STYLE = """\
FF                                                                       [100%]
================================== FAILURES ===================================
____________________________ TestAlpha.test_alpha _____________________________

self = <test_c.TestAlpha object at 0x000001E0BC603590>

    def test_alpha(self):
>       check_alpha()

test_c.py:7:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def check_alpha():
>       raise ValueError("alpha boom")
E       ValueError: alpha boom

alpha.py:2: ValueError
_____________________________ TestBeta.test_beta ______________________________

self = <test_c.TestBeta object at 0x000001E0BDCC1150>

    def test_beta(self):
>       check_beta()

test_c.py:12:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def check_beta():
        data = {}
>       return data["k"]
               ^^^^^^^^^
E       KeyError: 'k'

beta.py:3: KeyError
=========================== short test summary info ============================
FAILED test_c.py::TestAlpha::test_alpha - ValueError: alpha boom
FAILED test_c.py::TestBeta::test_beta - KeyError: 'k'
2 failed in 0.22s
"""

_CLASS_MODULES = {
    "alpha.py": 'def check_alpha():\n    raise ValueError("alpha boom")\n',
    "beta.py": 'def check_beta():\n    data = {}\n    return data["k"]\n',
}


def _write_modules(root: Path, **files: str) -> None:
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")


class TestMultiFailureAttribution:
    def test_two_files_name_and_location_same_failure(self, tmp_path: Path):
        """2 个不同文件的 failure：test_name 与 file:line/snippet 必须来自同一个。"""
        _write_modules(tmp_path, **_CLASS_MODULES)
        issue = parse_test_failure(_MULTI_TWO_FILES, tmp_path)
        assert issue is not None
        # 选择语义：稳定取第一个 failure（test_alpha）
        assert issue.test_name == "test_alpha"
        assert issue.error_type == "ValueError"
        # 定位必须同源 —— 修复前这里是 beta.py:3（最后一个 failure）
        assert (issue.file, issue.line) == ("alpha.py", 2)
        assert "raise ValueError" in issue.source_snippet
        assert 'data["k"]' not in issue.source_snippet, "不得混入第二个 failure 的源码"
        assert 'KeyError' not in issue.source_snippet

    def test_class_style_name_and_location_same_failure(self, tmp_path: Path):
        """类内测试（`TestAlpha.test_alpha`）：归属必须与模块级同样同源。

        pytest 对类内 failure 打印的头是 `TestAlpha.test_alpha`，与模块级
        `test_alpha` 形态不同 —— 修复前该形态切不出 block，退回全文扫描，
        于是 test_name 取自第一个 failure、file:line 取自最后一个。
        """
        _write_modules(tmp_path, **_CLASS_MODULES)
        issue = parse_test_failure(_MULTI_CLASS_STYLE, tmp_path)
        assert issue is not None
        assert issue.test_name == "test_alpha", "类内失败应取方法名（与修复前取值一致）"
        assert issue.error_type == "ValueError"
        assert (issue.file, issue.line) == ("alpha.py", 2), \
            "类内 failure 也必须定位到第一个 failure（修复前为 beta.py:3）"
        assert "raise ValueError" in issue.source_snippet
        assert 'data["k"]' not in issue.source_snippet, "不得混入第二个 failure 的源码"

    def test_class_style_parametrize_header_is_a_boundary(self, tmp_path: Path):
        """类内 + parametrize 头（`TestCalc.test_calc[1-2]`）同样可切分。"""
        _write_modules(tmp_path, **{
            "calc.py": "def total():\n    raise ValueError('calc boom')\n",
            "other.py": "def other():\n    return {}\n",
        })
        text = (
            "________________________ TestCalc.test_calc[1-2] _____________________________\n"
            "    def test_calc(self):\n>       total()\n\n"
            "test_c.py:5: \n"
            "_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _\n"
            "    def total():\n>       raise ValueError('calc boom')\n"
            "E       ValueError: calc boom\n\n"
            "calc.py:2: ValueError\n"
            "__________________________ TestOther.test_other[3-4] __________________________\n"
            "    def test_other(self):\n>       return {}\n"
            "E       KeyError: 'nope'\n\n"
            "other.py:2: KeyError\n"
        )
        issue = parse_test_failure(text, tmp_path)
        assert issue is not None
        assert issue.test_name == "test_calc"
        assert issue.error_type == "ValueError"
        assert (issue.file, issue.line) == ("calc.py", 2)

    def test_same_file_expected_actual_matches_location(self, tmp_path: Path):
        """同文件 2 个 assert failure：expected/actual 与 file:line 不得错配。"""
        _write_modules(
            tmp_path,
            **{"test_y.py": "def test_first():\n    assert 1 == 2\n\n\n"
                             "def test_second():\n    assert 3 == 4\n"},
        )
        issue = parse_test_failure(_MULTI_SAME_FILE, tmp_path)
        assert issue is not None
        assert issue.test_name == "test_first"
        assert (issue.file, issue.line) == ("test_y.py", 2), "应定位第一个 failure"
        # expected/actual 取自 assert 1 == 2（第一个 failure），与 file:line 同源
        assert (issue.expected, issue.actual) == ("2", "1")
        marked = [ln for ln in issue.source_snippet.splitlines() if ln.startswith(">>")]
        assert marked and "assert 1 == 2" in marked[0], f"根因行应同源: {marked}"

    def test_first_failure_selection_is_stable(self, tmp_path: Path):
        """三个 failure：选择语义明确且可重复（稳定取第一个块）。"""
        text = (
            "_________________________________ test_one ___________________________________\n"
            "    def test_one():\n>       assert 1 == 2\nE       assert 1 == 2\n\n"
            "a.py:2: AssertionError\n"
            "_________________________________ test_two ___________________________________\n"
            "    def test_two():\n>       assert 3 == 4\nE       assert 3 == 4\n\n"
            "b.py:5: AssertionError\n"
            "________________________________ test_three __________________________________\n"
            "    def test_three():\n>       assert 5 == 6\nE       assert 5 == 6\n\n"
            "c.py:9: AssertionError\n"
        )
        _write_modules(tmp_path, **{"a.py": "x = 1\n", "b.py": "y = 2\n", "c.py": "z = 3\n"})
        first = parse_test_failure(text, tmp_path)
        again = parse_test_failure(text, tmp_path)
        assert first is not None and again is not None
        assert first.test_name == "test_one"
        assert (first.file, first.line) == ("a.py", 2)
        assert (again.test_name, again.file, again.line) == ("test_one", "a.py", 2)

    def test_single_failure_behavior_unchanged(self, tmp_path: Path):
        """单 failure：现有行为保持不变（多帧 traceback 不得被切成两块）。

        fixture 用真实 pytest 形态：failure 头是整行下划线夹名字，**帧之间**
        的分隔是带空格的 `_ _ _ _`（无名字）。
        """
        _write_modules(tmp_path, **{"app.py": "def parse_config():\n    raise ValueError('bad')\n"})
        text = (
            "============================= test session starts =============================\n"
            "collected 1 item\n\n"
            "=================================== FAILURES ===================================\n"
            "_________________________________ test_config _________________________________\n\n"
            "    def test_config():\n>       parse_config()\n\n"
            "test_app.py:5: \n"
            "_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _\n\n"
            "    def parse_config():\n>       raise ValueError('bad')\n"
            "E       ValueError: bad\n\n"
            "app.py:2: ValueError\n"
            "=========================== short test summary info ============================\n"
            "FAILED test_app.py::test_config - ValueError: bad\n"
            "1 failed in 0.05s\n"
        )
        issue = parse_test_failure(text, tmp_path)
        assert issue is not None
        assert issue.test_name == "test_config"
        assert issue.error_type == "ValueError"
        assert (issue.file, issue.line) == ("app.py", 2)
        assert "raise ValueError" in issue.source_snippet
        assert ">>" in format_failure(issue)

    def test_non_test_header_is_not_a_boundary(self):
        """非测试名的整行下划线头不得被当成 failure 边界（纯单元级守卫）。

        仅断言分块判据本身；不主张真实 pytest 会打印这种头。
        """
        from zmai.swe.failure import _split_failure_blocks

        text = (
            "______ test_one ______\n"
            "    def test_one():\n>       helper()\n\n"
            "test_a.py:2: \n"
            "______ helper ______\n"
            "    def helper():\n>       raise ValueError('x')\n\n"
            "lib.py:9: ValueError\n"
        )
        assert [n for n, _ in _split_failure_blocks(text)] == ["test_one"]

    def test_collection_error_behavior_unchanged(self, tmp_path: Path):
        """collection/import error：现有行为保持（test_name 空，定位到出错测试文件）。"""
        _write_modules(tmp_path, **{"test_z.py": "import nonexistent_mod_xyz\n"})
        issue = parse_test_failure(_COLLECTION_ERROR, tmp_path)
        assert issue is not None
        assert issue.test_name == "", "collection error 无失败测试名，不得凭空造名"
        assert issue.error_type == "ImportError"
        assert (issue.file, issue.line) == ("test_z.py", 1)

    def test_no_header_falls_back_to_whole_text(self, tmp_path: Path):
        """无可切分头（非 pytest 格式）时保持原单块行为。"""
        _write_modules(tmp_path, **{"app.py": "def f():\n    raise ValueError('x')\n"})
        text = (
            'Traceback (most recent call last):\n'
            '  File "app.py", line 2, in f\n'
            "    raise ValueError('x')\n"
            "ValueError: x\n"
        )
        issue = parse_test_failure(text, tmp_path)
        assert issue is not None
        assert (issue.file, issue.line, issue.function) == ("app.py", 2, "f")
        assert "raise ValueError" in issue.source_snippet
