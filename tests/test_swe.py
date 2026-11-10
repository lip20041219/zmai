"""SWE Agent tool tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from zmai.swe.tools import (
    EditTool,
    GitTool,
    GrepTool,
    OpenInBrowserTool,
    ReadFileTool,
    ShellTool,
    ShowToUserTool,
    WriteFileTool,
)
from zmai.swe.verifier import verify_test_output
from zmai.tool import ToolContext


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def ctx(ws: Path) -> ToolContext:
    return ToolContext(agent_id="test", workspace_path=ws, timeout=10)


class TestReadFileTool:
    def test_tool_defined(self):
        t = ReadFileTool()
        assert t.name == "read_file"
        assert t.description
        assert "path" in t.parameters.get("required", [])

    def test_read_file(self, ctx, ws: Path):
        (ws / "test.txt").write_text("hello\nworld\nline3\n")
        t = ReadFileTool()
        r = t.execute(ctx, {"path": "test.txt"})
        assert r.success
        assert "hello" in r.output
        assert "3 lines" in r.output

    def test_read_partial(self, ctx, ws: Path):
        (ws / "test.txt").write_text("a\nb\nc\nd\ne\n")
        t = ReadFileTool()
        r = t.execute(ctx, {"path": "test.txt", "start_line": 2, "end_line": 4})
        assert r.success
        assert "b" in r.output
        assert "a" not in r.output

    def test_read_nonexistent(self, ctx):
        t = ReadFileTool()
        r = t.execute(ctx, {"path": "nope.txt"})
        assert not r.success


class TestWriteFileTool:
    def test_tool_defined(self):
        t = WriteFileTool()
        assert t.name == "write_file"

    def test_write_and_read_back(self, ctx, ws: Path):
        t = WriteFileTool()
        r = t.execute(ctx, {"path": "sub/hello.py", "content": "print('hi')"})
        assert r.success
        assert (ws / "sub" / "hello.py").exists()
        assert (ws / "sub" / "hello.py").read_text() == "print('hi')"

    def test_overwrite(self, ctx, ws: Path):
        (ws / "f.txt").write_text("old")
        t = WriteFileTool()
        t.execute(ctx, {"path": "f.txt", "content": "new"})
        assert (ws / "f.txt").read_text() == "new"

    def test_content_shrink_warning_for_python_file(self, ctx, ws: Path):
        """Python 文件内容大幅缩减（不足 50%）时应附加 CONTENT_SHRINK_WARNING。"""
        large_py = "\n".join([
            "def foo():",
            "    return 1",
            "",
            "def bar():",
            "    return 2",
            "",
            "def baz():",
            "    return 3",
            "",
            "class MyClass:",
            "    pass",
        ]) + "\n"
        (ws / "mod.py").write_text(large_py)
        t = WriteFileTool()
        # 只保留 1 行（远不足 50%）
        r = t.execute(ctx, {"path": "mod.py", "content": "x = 1\n"})
        assert r.success
        assert "CONTENT_SHRINK_WARNING" in r.output
        assert "foo" in r.output  # 原文件定义应出现在警告中
        assert "bar" in r.output
        assert "MyClass" in r.output

    def test_no_warning_for_moderate_shrink(self, ctx, ws: Path):
        """内容缩减但不少于 50% 时不应警告。"""
        large_py = "\n".join([f"line{i}" for i in range(20)]) + "\n"
        (ws / "mod.py").write_text(large_py)
        t = WriteFileTool()
        # 保留 15 行（75%，超过 50%）
        new_content = "\n".join([f"line{i}" for i in range(15)]) + "\n"
        r = t.execute(ctx, {"path": "mod.py", "content": new_content})
        assert r.success
        assert "CONTENT_SHRINK_WARNING" not in r.output

    def test_no_warning_for_non_python_file(self, ctx, ws: Path):
        """非 Python 文件大幅缩减不应警告。"""
        (ws / "data.txt").write_text("a\n" * 50)
        t = WriteFileTool()
        r = t.execute(ctx, {"path": "data.txt", "content": "x\n"})
        assert r.success
        assert "CONTENT_SHRINK_WARNING" not in r.output

    def test_no_warning_for_new_file(self, ctx, ws: Path):
        """新建文件（无原内容）不应警告。"""
        t = WriteFileTool()
        r = t.execute(ctx, {"path": "new.py", "content": "x = 1\n"})
        assert r.success
        assert "CONTENT_SHRINK_WARNING" not in r.output

    def test_shrink_warning_does_not_block_write(self, ctx, ws: Path):
        """内容缩减警告不应阻止写入，文件应被实际覆盖。"""
        (ws / "mod.py").write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
        t = WriteFileTool()
        r = t.execute(ctx, {"path": "mod.py", "content": "x = 1\n"})
        assert r.success
        assert (ws / "mod.py").read_text() == "x = 1\n"

    def test_write_file_backs_up_original_content(self, ctx, ws: Path):
        """write_file 覆盖已有文件时，应将原始内容备份到 context.config['_file_backups']。

        这是 destructive regression 后 [Recovery] 消息能提供原始内容的前提。
        """
        original = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
        (ws / "mod.py").write_text(original)
        t = WriteFileTool()
        r = t.execute(ctx, {"path": "mod.py", "content": "x = 1\n"})
        assert r.success
        # 验证备份存在且内容正确
        backups = ctx.config.get("_file_backups", {})
        assert "mod.py" in backups
        assert backups["mod.py"] == original

    def test_write_file_no_backup_for_new_file(self, ctx, ws: Path):
        """新建文件（无原内容）时，不应创建备份条目。"""
        t = WriteFileTool()
        r = t.execute(ctx, {"path": "new.py", "content": "x = 1\n"})
        assert r.success
        backups = ctx.config.get("_file_backups", {})
        assert "new.py" not in backups


class TestGrepTool:
    def test_tool_defined(self):
        t = GrepTool()
        assert t.name == "grep"

    def test_grep_found(self, ctx, ws: Path):
        (ws / "a.py").write_text("import os\nx = 1\n")
        (ws / "b.py").write_text("import sys\n")
        t = GrepTool()
        r = t.execute(ctx, {"pattern": "import"})
        assert r.success
        assert "a.py" in r.output
        assert "b.py" in r.output

    def test_grep_not_found(self, ctx, ws: Path):
        (ws / "a.py").write_text("hello")
        t = GrepTool()
        r = t.execute(ctx, {"pattern": "zzzzz"})
        assert r.success
        assert "no matches" in r.output

    def test_file_glob_searches_nested_source_without_broadening_path_glob(self, ctx, ws: Path):
        (ws / "top.py").write_text("target = 1\n")
        (ws / "pkg").mkdir()
        (ws / "pkg" / "nested.py").write_text("target = 2\n")
        (ws / "other").mkdir()
        (ws / "other" / "nested.py").write_text("target = 3\n")

        recursive = GrepTool().execute(ctx, {"pattern": "target", "glob": "*.py"})
        assert recursive.success
        assert "top.py" in recursive.output
        assert "pkg/nested.py" in recursive.output
        assert "other/nested.py" in recursive.output

        scoped = GrepTool().execute(ctx, {"pattern": "target", "glob": "pkg/*.py"})
        assert scoped.success
        assert "pkg/nested.py" in scoped.output
        assert "top.py" not in scoped.output
        assert "other/nested.py" not in scoped.output


class TestShellTool:
    def test_tool_defined(self):
        t = ShellTool()
        assert t.name == "shell_exec"

    def test_shell_exec(self, ctx):
        t = ShellTool()
        r = t.execute(ctx, {"command": "echo hello"})
        assert r.success
        assert "hello" in r.output
        # Case 1: 正常成功命令 → SUCCESS
        assert r.metadata.get("exit_code") == 0

    def test_shell_fail(self, ctx):
        t = ShellTool()
        r = t.execute(ctx, {"command": "exit 1"})
        assert not r.success
        # Case 2: 正常失败命令 → FAIL
        assert r.metadata.get("exit_code") == 1

    def test_nonzero_exit_beats_success_word_in_output(self, ctx):
        """Case 3: stdout 含 SUCCESS 但退出码 1 → 仍然 FAIL。"""
        cmd = "echo SUCCESS & exit 1" if os.name == "nt" else "echo SUCCESS; exit 1"
        r = ShellTool().execute(ctx, {"command": cmd})
        assert not r.success
        assert r.metadata.get("exit_code") == 1


class TestShellToolTestVerdict:
    """shell_exec 的测试结论可靠性（Verification / Test Result）。

    核心不变量：真实 pytest 失败 → ToolResult.success 必须为 False，且与
    Agent 内部 verify_test_output() 的判定一致。
    """

    @staticmethod
    def _write_failing_suite(ws: Path) -> None:
        """2 passed / 2 failed 的最小测试套件（对应 Case 4 的场景）。"""
        (ws / "test_verdict_suite.py").write_text(
            "def test_a():\n"
            "    assert True\n\n\n"
            "def test_b():\n"
            "    assert True\n\n\n"
            "def test_c():\n"
            "    assert False\n\n\n"
            "def test_d():\n"
            "    assert False\n",
            encoding="utf-8",
        )

    def test_pytest_failure_reports_failure(self, ctx, ws: Path):
        """Case 4: 真实 pytest 2 failed / 2 passed → FAIL。"""
        self._write_failing_suite(ws)
        cmd = f'"{sys.executable}" -m pytest -q test_verdict_suite.py'
        r = ShellTool().execute(ctx, {"command": cmd})
        assert not r.success
        assert r.metadata.get("exit_code") != 0

    def test_pytest_failure_behind_pager_pipeline_reports_failure(self, ctx, ws: Path):
        """Case 4b: `... 2>&1 | more` 的管道退出码掩盖 → 仍必须是 FAIL。

        这是本次修复的根因场景：cmd.exe 返回 `more` 的 0，pytest 的失败被吞掉。
        """
        self._write_failing_suite(ws)
        cmd = f'"{sys.executable}" -m pytest -q test_verdict_suite.py 2>&1 | more'
        r = ShellTool().execute(ctx, {"command": cmd})
        assert not r.success

    def test_masked_exit_code_is_corrected_from_parsed_output(self, ctx):
        """Case 4c: 退出码被掩盖为 0，但汇总行报告失败 → 改判为 FAIL。

        不依赖具体的 pytest 进程，直接构造"退出码 0 + pytest 失败汇总行"。
        """
        r = ShellTool().execute(
            ctx, {"command": "echo pytest 2 failed, 2 passed in 0.16s"}
        )
        assert not r.success
        assert r.metadata.get("exit_code") == 1

    def test_green_pytest_run_still_reports_success(self, ctx, ws: Path):
        """反向保证：全绿的 pytest 不能被误判为失败。"""
        (ws / "test_verdict_green.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
        cmd = f'"{sys.executable}" -m pytest -q test_verdict_green.py'
        r = ShellTool().execute(ctx, {"command": cmd})
        assert r.success
        assert r.metadata.get("exit_code") == 0

    def test_success_matches_verify_test_output_verdict(self, ctx, ws: Path):
        """Case 5: ToolResult.success 必须与 verify_test_output() 判定一致。"""
        self._write_failing_suite(ws)
        r = ShellTool().execute(
            ctx,
            {"command": f'"{sys.executable}" -m pytest -q test_verdict_suite.py'},
        )
        verdict = verify_test_output(r.output + (r.error or ""))
        assert verdict.passed is False
        assert r.success is False

    def test_failure_summary_survives_context_truncation(self, ctx, ws: Path):
        """失败摘要不会被下游头部截断（context.tool_truncate 默认 500）切掉。"""
        self._write_failing_suite(ws)
        r = ShellTool().execute(
            ctx,
            {"command": f'"{sys.executable}" -m pytest -q test_verdict_suite.py'},
        )
        model_view = (r.output or "") + (r.error or "")
        assert "[test summary]" in model_view[:500]
        assert "2 failed" in model_view[:500]


class TestGitTool:
    def test_tool_defined(self):
        t = GitTool()
        assert t.name == "git"


class TestEditTool:
    def test_tool_defined(self):
        t = EditTool()
        assert t.name == "edit"
        assert "path" in t.parameters["required"]

    def test_description_covers_all_modes(self):
        """工具描述必须明确说明四种模式的用途，特别是 regex_replace 的正则表达式要求。"""
        t = EditTool()
        desc = t.description.lower()
        # 四种模式都应在描述中提及
        for mode in ["replace_lines", "regex_replace", "insert", "append"]:
            assert mode in desc, f"description missing mode: {mode}"
        # regex_replace 必须明确说明 old_text 是正则表达式
        assert "regex" in desc or "regular expression" in desc
        assert "escape" in desc or "special char" in desc or "pattern" in desc
        # append 必须说明只用于文件末尾新增内容
        assert "end" in desc or "append" in desc

    def test_parameter_schema_matches_implementation(self):
        """参数 schema 必须与实际实现一致：regex_replace 需要 old_text，replace_lines 使用行号。"""
        t = EditTool()
        props = t.parameters["properties"]
        # 所有四种模式都在 enum 中
        modes = props["mode"]["enum"]
        assert set(modes) == {"replace_lines", "regex_replace", "insert", "append"}
        # old_text 参数存在且有描述
        assert "old_text" in props
        assert "description" in props["old_text"]
        # mode 参数有描述
        assert "description" in props["mode"]
        # required 字段包含 path, mode, new_text
        assert set(t.parameters["required"]) == {"path", "mode", "new_text"}

    def test_regex_replace_old_text_is_regex_not_literal(self, ctx, ws: Path):
        """验证 regex_replace 的 old_text 确实被当作正则表达式处理（与 schema 描述一致）。"""
        t = EditTool()
        (ws / "f.py").write_text("result = max(a, b)\n")
        # "max(" 不是有效正则（未闭合的分组），应该失败
        # 这证明 old_text 确实是正则表达式，而不是字面量
        r = t.execute(ctx, {"path": "f.py", "mode": "regex_replace",
                            "old_text": "max(", "new_text": "min("})
        assert not r.success
        assert "regex error" in r.error.lower() or "missing" in r.error.lower()

    def test_replace_lines(self, ctx, ws: Path):
        (ws / "f.py").write_text("line1\nline2\nline3\nline4\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "replace_lines",
                            "start_line": 2, "end_line": 3, "new_text": "modified\n"})
        assert r.success
        content = (ws / "f.py").read_text()
        assert "line1" in content
        assert "modified" in content
        assert "line2" not in content
        assert "line3" not in content
        assert "line4" in content

    def test_regex_replace(self, ctx, ws: Path):
        (ws / "f.py").write_text("foo = 1\nfoo = 2\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "regex_replace",
                            "old_text": "foo", "new_text": "qux", "count": 1})
        assert r.success
        content = (ws / "f.py").read_text()
        assert content.startswith("qux")
        assert "foo" in content.split("\n")[1]

    def test_insert(self, ctx, ws: Path):
        (ws / "f.py").write_text("a\nc\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "insert",
                            "start_line": 2, "new_text": "b\n"})
        assert r.success
        assert (ws / "f.py").read_text() == "a\nb\nc\n"

    def test_append(self, ctx, ws: Path):
        t = EditTool()
        r = t.execute(ctx, {"path": "new.txt", "mode": "append",
                            "new_text": "hello"})
        assert r.success
        assert (ws / "new.txt").exists()
        assert "hello" in (ws / "new.txt").read_text()

    def test_out_of_range(self, ctx, ws: Path):
        (ws / "f.py").write_text("a\nb\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "replace_lines",
                            "start_line": 10, "end_line": 12, "new_text": "x"})
        assert not r.success

    # ── 编辑后回显（Gap 1）──

    def test_replace_lines_echo_contains_modified_content(self, ctx, ws: Path):
        """replace_lines 成功后，返回值应包含修改区域及上下文的带行号内容。"""
        (ws / "f.py").write_text("line1\nline2\nline3\nline4\nline5\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "replace_lines",
                            "start_line": 3, "end_line": 3, "new_text": "CHANGED\n"})
        assert r.success
        assert "replaced f.py:3-3" in r.output
        # 修改行内容出现在回显中
        assert "CHANGED" in r.output
        # 绝对行号正确：第 3 行被修改，回显应包含 "   3|CHANGED"
        assert "   3|CHANGED" in r.output
        # 上下文行也在回显中（±3 行）
        assert "   2|line2" in r.output
        assert "   4|line4" in r.output

    def test_replace_lines_echo_at_file_start(self, ctx, ws: Path):
        """修改文件首行时，echo_start 不小于 1。"""
        (ws / "f.py").write_text("a\nb\nc\nd\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "replace_lines",
                            "start_line": 1, "end_line": 1, "new_text": "X\n"})
        assert r.success
        assert "   1|X" in r.output
        # 不应出现行号 0 或负数
        assert "   0|" not in r.output

    def test_regex_replace_echo_shows_first_match(self, ctx, ws: Path):
        """regex_replace 多处匹配时，回显只展示第一处修改区域，控制输出规模。"""
        # 每处匹配间隔 5 行，第三处在第 11 行，超出第一处 ±3 + 10 行上限
        (ws / "f.py").write_text(
            "foo = 1\nx2\nx3\nx4\nx5\n"
            "foo = 7\nx8\nx9\nx10\nx11\n"
            "foo = 13\nx14\nx15\n"
        )
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "regex_replace",
                            "old_text": "foo", "new_text": "QUX"})
        assert r.success
        assert "replaced 3 matches in f.py" in r.output
        # 第一处匹配在第 1 行，回显应包含 "   1|QUX"
        assert "   1|QUX" in r.output
        # 第二处在第 6 行（在 ±3+10 范围内可能出现），第三处在第 11 行
        # 回显上限 20 行且从第 1 行开始，第 11 行可能在范围内——
        # 改为验证回显不包含文件末尾（第 15 行），证明输出被截断而非全文
        assert "  15|x15" not in r.output
        # 回显区域标题中的结束行应小于文件总行数（15）
        import re
        m = re.search(r"lines (\d+)-(\d+)", r.output)
        assert m
        assert int(m.group(2)) < 15, "回显不应覆盖整个文件"

    def test_regex_replace_echo_correct_line_numbers(self, ctx, ws: Path):
        """regex_replace 修改中间行时，回显行号为绝对行号。"""
        (ws / "f.py").write_text("a\nb\nc\nd\ne\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "regex_replace",
                            "old_text": "c", "new_text": "CCC"})
        assert r.success
        # 第 3 行被修改
        assert "   3|CCC" in r.output
        # 上下文行号正确
        assert "   2|b" in r.output
        assert "   4|d" in r.output

    def test_insert_echo_contains_inserted_and_context(self, ctx, ws: Path):
        """insert 成功后，回显包含插入内容及前后上下文。"""
        (ws / "f.py").write_text("a\nb\nd\ne\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "insert",
                            "start_line": 3, "new_text": "c\n"})
        assert r.success
        assert "inserted at f.py:3" in r.output
        # 插入内容在第 3 行
        assert "   3|c" in r.output
        # 原第 3 行（d）后移到第 4 行
        assert "   4|d" in r.output
        # 上下文
        assert "   2|b" in r.output

    def test_insert_echo_at_file_end(self, ctx, ws: Path):
        """在文件末尾插入时，echo_end 不超过文件总行数。"""
        (ws / "f.py").write_text("a\nb\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "insert",
                            "start_line": 3, "new_text": "c\n"})
        assert r.success
        assert "   3|c" in r.output
        # 文件只有 3 行，不应出现行号 4+
        assert "   4|" not in r.output

    def test_echo_capped_for_large_modification(self, ctx, ws: Path):
        """大段替换时，回显行数不超过 _MAX_ECHO_LINES（20 行）。"""
        many = "\n".join(f"line{i}" for i in range(50)) + "\n"
        (ws / "f.py").write_text(many)
        t = EditTool()
        # 替换第 10-30 行（21 行），加上下文会超过 20 行限制
        r = t.execute(ctx, {"path": "f.py", "mode": "replace_lines",
                            "start_line": 10, "end_line": 30,
                            "new_text": "NEW\n"})
        assert r.success
        # 回显区域标题中的结束行 - 起始行 + 1 不应超过 20
        # 解析 "--- f.py (lines N-M) ---"
        import re
        m = re.search(r"lines (\d+)-(\d+)", r.output)
        assert m, "回显应包含 lines 范围标题"
        echo_lines = int(m.group(2)) - int(m.group(1)) + 1
        assert echo_lines <= 20, f"回显行数 {echo_lines} 超过上限 20"

    def test_echo_not_present_on_failure(self, ctx, ws: Path):
        """编辑失败时，不应包含回显内容（只有错误信息）。"""
        (ws / "f.py").write_text("a\nb\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "replace_lines",
                            "start_line": 99, "end_line": 100, "new_text": "x"})
        assert not r.success
        assert "--- f.py (lines" not in r.output


class TestShowToUserTool:
    def test_tool_defined(self):
        t = ShowToUserTool()
        assert t.name == "show_to_user"

    def test_show_content(self, ctx):
        t = ShowToUserTool()
        r = t.execute(ctx, {"content": "hello world", "title": "Test"})
        assert r.success


class TestOpenInBrowserTool:
    def test_tool_defined(self):
        t = OpenInBrowserTool()
        assert t.name == "open_in_browser"

    def test_file_not_found(self, ctx):
        t = OpenInBrowserTool()
        r = t.execute(ctx, {"path": "nonexistent.html"})
        assert not r.success


class TestIntegration:
    def test_read_write_grep_flow(self, ctx, ws: Path):
        wt = WriteFileTool()
        assert wt.execute(ctx, {"path": "main.py", "content": "def foo():\n    pass\n"}).success
        rt = ReadFileTool()
        r = rt.execute(ctx, {"path": "main.py"})
        assert r.success
        assert "foo" in r.output
        gt = GrepTool()
        r = gt.execute(ctx, {"pattern": "def "})
        assert r.success
        assert "main.py" in r.output


# ═══════════════════════════════════════════════════════════════
# _resolve_tool_path 路径安全性测试
# ═══════════════════════════════════════════════════════════════


class TestResolveToolPath:
    """_resolve_tool_path 的路径隔离行为验证。"""

    def test_relative_path_within_workspace(self, ctx: ToolContext) -> None:
        """相对路径在 workspace 内应通过。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "output/file.txt")
        assert safe is True
        assert resolved.exists() is False  # 文件不存在不影响路径合法性
        assert err == ""

    def test_dot_path_within_workspace(self, ctx: ToolContext) -> None:
        """. 应解析为 workspace 根目录。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, ".")
        assert safe is True
        assert resolved == ctx.workspace_path.resolve()

    def test_dotdot_escape_rejected(self, ctx: ToolContext) -> None:
        """.. 逃逸应被拒绝。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "../../etc/passwd")
        assert safe is False
        assert "不在工作区" in err or "安全限制" in err

    def test_absolute_path_outside_rejected(self, ctx: ToolContext) -> None:
        """workspace 外部的绝对路径应被拒绝。"""
        import sys

        from zmai.swe.tools import _resolve_tool_path
        if sys.platform == "win32":
            outside = "C:\\Windows\\System32\\drivers\\etc\\hosts"
        else:
            outside = "/etc/passwd"
        safe, resolved, err = _resolve_tool_path(ctx, outside)
        assert safe is False
        assert "不在" in err or "安全限制" in err

    def test_absolute_path_inside_allowed_with_project(self, tmp_path: Path) -> None:
        """project_path 内的绝对路径应允许。"""
        from zmai.swe.tools import _resolve_tool_path
        from zmai.tool import ToolContext
        (tmp_path / "allowed.txt").write_text("ok")
        ctx = ToolContext(agent_id="test", workspace_path=tmp_path,
                          project_path=tmp_path, timeout=10)
        safe, resolved, err = _resolve_tool_path(ctx, str(tmp_path / "allowed.txt"))
        assert safe is True
        assert resolved.exists()

    def test_sibling_prefix_rejected(self, ctx: ToolContext) -> None:
        """前缀相似的目录名不得绕过。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "../a-extra/file.txt")
        assert safe is False

    def test_deep_nested_allowed(self, ctx: ToolContext) -> None:
        """深层嵌套路径应允许。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "a/b/c/d/e/f/g/file.txt")
        assert safe is True

    def test_mixed_dot_resolved(self, ctx: ToolContext) -> None:
        """混合 . 和 .. 应正确解析。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "./a/./b/../c/file.txt")
        assert safe is True
        # ./a/./b/../c/file.txt → ./a/c/file.txt
        assert str(resolved).endswith("a/c/file.txt") or str(resolved).endswith("a\\c\\file.txt")
