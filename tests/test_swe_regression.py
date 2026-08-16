"""SWE Agent 回归测试 — 覆盖已知缺陷和边缘情况。

这些测试确保已修复的 Bug 不会再次出现（regression）。
每个测试类对应一个或多个已知缺陷。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from zmai.swe.tools import (
    EditTool,
    GrepTool,
    ReadFileTool,
    ShellTool,
    WriteFileTool,
)
from zmai.tool import ToolContext

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def ctx(ws: Path) -> ToolContext:
    return ToolContext(agent_id="test", workspace_path=ws, timeout=10)


# ── Bug: Path Traversal ─────────────────────────────────────────────────────


class TestPathTraversal:
    """SWE 工具应阻止路径穿越到工作区之外。

    原 Bug: ReadFileTool/WriteFileTool/EditTool 使用
    workspace_path / user_path 拼接路径，未验证结果是否在工作区内。
    """

    @pytest.mark.parametrize("bad_path", [
        "../etc/passwd",
        "../../../windows/system32/config",
        "sub/../../outside",
        "foo/../../../bar",
    ])
    def test_read_file_rejects_traversal(self, ctx: ToolContext, bad_path: str):
        """读取工作区外的路径应失败。

        当前先确保返回 false，后续加上路径穿越检测后验证错误信息。
        """
        t = ReadFileTool()
        r = t.execute(ctx, {"path": bad_path})
        assert not r.success, f"应拒绝路径穿越: {bad_path}"

    @pytest.mark.parametrize("bad_path", [
        "../etc/passwd",
        "../../malware.exe",
    ])
    def test_write_file_rejects_traversal(self, ctx: ToolContext, bad_path: str):
        """写入到工作区外的路径应失败。"""
        t = WriteFileTool()
        r = t.execute(ctx, {"path": bad_path, "content": "evil"})
        assert not r.success, f"应拒绝路径穿越: {bad_path}"

    @pytest.mark.parametrize("bad_path", [
        "../outside.txt",
        "../../secret.config",
    ])
    def test_edit_rejects_traversal(self, ctx: ToolContext, bad_path: str):
        """编辑工作区外的路径应失败。"""
        t = EditTool()
        r = t.execute(ctx, {"path": bad_path, "mode": "append", "new_text": "x"})
        assert not r.success, f"应拒绝路径穿越: {bad_path}"

    def test_grep_rejects_traversal(self, ctx: ToolContext):
        """Grep 使用 glob 时应阻止路径穿越。"""
        t = GrepTool()
        # glob 模式中的 .. 可能导致遍历
        r = t.execute(ctx, {"pattern": "test", "glob": "../**/*"})
        assert r.success  # Grep 应安全处理，不崩溃但不保证能找到


class TestPathTraversalSafePaths:
    """合法路径应正常通过。"""

    def test_read_normal_path(self, ctx: ToolContext, ws: Path):
        (ws / "safe.txt").write_text("hello")
        t = ReadFileTool()
        r = t.execute(ctx, {"path": "safe.txt"})
        assert r.success

    def test_read_subdir_path(self, ctx: ToolContext, ws: Path):
        (ws / "sub").mkdir(parents=True, exist_ok=True)
        (ws / "sub" / "nested.txt").write_text("hi")
        t = ReadFileTool()
        r = t.execute(ctx, {"path": "sub/nested.txt"})
        assert r.success

    def test_write_normal_path(self, ctx: ToolContext, ws: Path):
        t = WriteFileTool()
        r = t.execute(ctx, {"path": "normal.txt", "content": "ok"})
        assert r.success

    def test_write_deep_path(self, ctx: ToolContext, ws: Path):
        t = WriteFileTool()
        r = t.execute(ctx, {"path": "a/b/c/deep.txt", "content": "deep"})
        assert r.success


# ── Bug: SWEAgent step 无 Backend 时返回 complete 而非 fail ─────────────────


class TestSWEAgentNoBackend:
    """SWEAgent.step() 没有 Backend 时应返回 fail。

    原 Bug: agent.py:93 返回 AgentAction.complete("无 Backend")，
    语义上失败场景应返回 fail。
    """

    def test_step_without_backend_returns_fail(self):
        from zmai.agent import AgentContext
        from zmai.swe.agent import SWEAgent

        async def run():
            agent = SWEAgent("regr_no_backend")
            ctx = AgentContext(
                agent_id="regr_no_backend",
                task="hello",
                backend=None,
            )
            action = await agent.step(ctx)
            assert action.type == "fail", (
                f"没有 Backend 时应返回 fail，实际返回: {action.type}"
            )
            assert action.error, "应包含错误信息"

        asyncio.run(run())

    def test_step_with_backend_does_not_fail(self):
        """有 Backend 时 step 不应直接 fail。"""
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

        class MinimalBackend(Backend):
            name = "minimal"

            def invoke(self, request: BackendRequest) -> BackendResponse:
                return BackendResponse(
                    content="hello back",
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                )

            def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
                yield BackendEvent(type="text", data="hello")
                yield BackendEvent(type="done", data="", index=1)

            @property
            def capabilities(self) -> set[BackendCapability]:
                return set()

        async def run():
            agent = SWEAgent("regr_with_backend")
            ctx = AgentContext(
                agent_id="regr_with_backend",
                task="hello",
                backend=MinimalBackend(),
                metadata={"messages": []},
            )
            action = await agent.step(ctx)
            # 有 Backend 时不应 fail
            assert action.type != "fail", f"有 Backend 时不应 fail: {action.error}"

        asyncio.run(run())


# ── Bug: 空 assistant 消息 ──────────────────────────────────────────────────


class TestEmptyAssistantMessage:
    """只有 tool_calls 时不应追加空 content 的 assistant 消息。

    原 Bug: swe/agent.py:115 总是追加 response.content，即使 content 为空。
    """

    def test_no_empty_assistant_on_tool_only(self):
        """当 Backend 只返回 tool_calls 时不应有空的 assistant 消息。"""
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

        class ToolBackend(Backend):
            name = "tooler"
            _call_count = 0

            def invoke(self, request: BackendRequest) -> BackendResponse:
                return BackendResponse(
                    content="",  # 空内容
                    tool_calls=[ToolCall(id="call_1", name="shell_exec", params={"command": "echo hi"})],  # noqa: E501
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                    stop_reason="tool_use",
                )

            def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
                yield BackendEvent(type="done", data="", index=1)

            @property
            def capabilities(self) -> set[BackendCapability]:
                return {BackendCapability.TOOL_USE}

        async def run():
            agent = SWEAgent("regr_empty_msg")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="regr_empty_msg",
                task="test",
                tools=registry,
            ))
            ctx = AgentContext(
                agent_id="regr_empty_msg",
                task="test",
                backend=ToolBackend(),
                tools=registry,
                metadata={"messages": []},
            )
            await agent.step(ctx)
            msgs = ctx.metadata.get("messages", [])
            # 不应有空 content 的 assistant 消息
            for msg in msgs:
                if msg.get("role") == "assistant":
                    assert msg.get("content", "") != "", (
                        "不应有空 content 的 assistant 消息"
                    )
            # 应有工具调用结果消息
            user_msgs = [m for m in msgs if m.get("role") == "user"]
            tool_result_msgs = [m for m in user_msgs if "工具" in m.get("content", "") or "Tool" in m.get("content", "")]  # noqa: E501
            assert len(tool_result_msgs) >= 1, "应有工具调用结果消息"

        asyncio.run(run())

    def test_assistant_message_with_content_preserved(self):
        """当 Backend 返回文本内容时，assistant 消息应正常保留。"""
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
        from zmai.tool import ToolRegistry

        class TextBackend(Backend):
            name = "texter"

            def invoke(self, request: BackendRequest) -> BackendResponse:
                return BackendResponse(
                    content="这是一条正常的回复",
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                )

            def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
                yield BackendEvent(type="done", data="", index=1)

            @property
            def capabilities(self) -> set[BackendCapability]:
                return set()

        async def run():
            agent = SWEAgent("regr_content_preserved")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="regr_content_preserved",
                task="test",
                tools=registry,
            ))
            ctx = AgentContext(
                agent_id="regr_content_preserved",
                task="test",
                backend=TextBackend(),
                tools=registry,
                metadata={"messages": []},
            )
            await agent.step(ctx)
            msgs = ctx.metadata.get("messages", [])
            assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
            assert len(assistant_msgs) >= 1
            assert "正常" in assistant_msgs[-1].get("content", "")

        asyncio.run(run())


# ── Bug: step_count 未递增 ──────────────────────────────────────────────────


class TestStepCountTracking:
    """SWEAgent.step() 应递增 context.step_count。"""

    def test_step_count_incremented(self):
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
        from zmai.tool import ToolRegistry

        class SimpleBackend(Backend):
            name = "simple"

            def invoke(self, request: BackendRequest) -> BackendResponse:
                return BackendResponse(
                    content="done",
                    usage=TokenUsage(input_tokens=5, output_tokens=3),
                )

            def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
                yield BackendEvent(type="done", data="", index=1)

            @property
            def capabilities(self) -> set[BackendCapability]:
                return set()

        async def run():
            agent = SWEAgent("regr_step_count")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="regr_step_count",
                task="test",
                tools=registry,
            ))
            ctx = AgentContext(
                agent_id="regr_step_count",
                task="test",
                backend=SimpleBackend(),
                tools=registry,
                metadata={"messages": []},
            )
            assert ctx.step_count == 0
            await agent.step(ctx)
            assert ctx.step_count == 1, (
                f"第一次 step 后 step_count 应为 1，实际为 {ctx.step_count}"
            )
            await agent.step(ctx)
            assert ctx.step_count == 2, (
                f"第二次 step 后 step_count 应为 2，实际为 {ctx.step_count}"
            )

        asyncio.run(run())


# ── Bug: EditTool 边界条件 ───────────────────────────────────────────────────


class TestEditToolEdgeCases:
    """EditTool 极端参数处理。"""

    def test_insert_beyond_end(self, ctx: ToolContext, ws: Path):
        """在工作区中插入超出末尾的行应正常工作（在末尾追加）。"""
        (ws / "f.py").write_text("a\nb\n")
        t = EditTool()
        # 在第 10 行插入 — 远超出 2 行
        r = t.execute(ctx, {
            "path": "f.py", "mode": "insert",
            "start_line": 10, "new_text": "c\n",
        })
        # 应该成功，Python 列表切片处理超出范围的索引
        assert r.success
        content = (ws / "f.py").read_text()
        assert content.endswith("c\n"), f"应在末尾追加: {repr(content)}"

    def test_replace_lines_beyond_end(self, ctx: ToolContext, ws: Path):
        """replace_lines 的 end_line 超出文件范围应能处理。"""
        (ws / "f.py").write_text("a\nb\n")
        t = EditTool()
        r = t.execute(ctx, {
            "path": "f.py", "mode": "replace_lines",
            "start_line": 1, "end_line": 100, "new_text": "replaced\n",
        })
        assert r.success
        assert (ws / "f.py").read_text() == "replaced\n"

    def test_replace_lines_start_zero(self, ctx: ToolContext, ws: Path):
        """start_line 为 0 时应返回错误。"""
        (ws / "f.py").write_text("a\n")
        t = EditTool()
        r = t.execute(ctx, {
            "path": "f.py", "mode": "replace_lines",
            "start_line": 0, "end_line": 1, "new_text": "x\n",
        })
        assert not r.success, "start_line 为 0 应失败"
        assert "out of range" in r.error.lower()

    def test_replace_lines_negative(self, ctx: ToolContext, ws: Path):
        """start_line 为负数时应返回错误。"""
        (ws / "f.py").write_text("a\n")
        t = EditTool()
        r = t.execute(ctx, {
            "path": "f.py", "mode": "replace_lines",
            "start_line": -1, "end_line": 1, "new_text": "x\n",
        })
        assert not r.success, "负数 start_line 应失败"

    def test_append_empty_content(self, ctx: ToolContext, ws: Path):
        """追加空内容到已存在的文件。"""
        (ws / "f.txt").write_text("hello")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.txt", "mode": "append", "new_text": ""})
        assert r.success
        # 空追加不应改变文件
        assert (ws / "f.txt").read_text() == "hello", (
            "空追加不应修改文件内容"
        )

    def test_regex_replace_with_empty_old_text(self, ctx: ToolContext, ws: Path):
        """regex_replace 的 old_text 为空应返回错误。"""
        (ws / "f.py").write_text("hello")
        t = EditTool()
        r = t.execute(ctx, {
            "path": "f.py", "mode": "regex_replace",
            "old_text": "", "new_text": "x",
        })
        assert not r.success, "空的 old_text 应失败"

    def test_regex_invalid_pattern(self, ctx: ToolContext, ws: Path):
        """非法的正则模式应返回错误而不是崩溃。"""
        (ws / "f.py").write_text("hello")
        t = EditTool()
        r = t.execute(ctx, {
            "path": "f.py", "mode": "regex_replace",
            "old_text": "[invalid", "new_text": "x",
        })
        assert not r.success, "非法正则应失败"
        assert "regex" in r.error.lower() or "error" in r.error.lower()

    def test_edit_nonexistent_file_replaces(self, ctx: ToolContext):
        """编辑不存在的文件在 replace 模式下应失败。"""
        t = EditTool()
        r = t.execute(ctx, {
            "path": "noexist.py", "mode": "replace_lines",
            "start_line": 1, "end_line": 1, "new_text": "x\n",
        })
        assert not r.success, "不存在文件的 replace 应失败"


# ── Bug: GrepTool 空模式 ────────────────────────────────────────────────────


class TestGrepEdgeCases:
    """GrepTool 边界条件。"""

    def test_empty_pattern_fails(self, ctx: ToolContext):
        t = GrepTool()
        r = t.execute(ctx, {"pattern": ""})
        assert not r.success, "空模式应失败"

    def test_invalid_regex(self, ctx: ToolContext):
        t = GrepTool()
        r = t.execute(ctx, {"pattern": "(unclosed"})
        assert not r.success, "非法正则应失败"
        assert "regex" in r.error.lower()

    def test_unicode_pattern(self, ctx: ToolContext, ws: Path):
        # 显式 utf-8：Windows 默认 cp1252 无法编码中文
        (ws / "data.txt").write_text("你好世界\nhello world\n", encoding="utf-8")
        t = GrepTool()
        r = t.execute(ctx, {"pattern": "你好"})
        assert r.success
        assert "你好" in r.output


# ── Bug: ShellTool 边界条件 ──────────────────────────────────────────────────


class TestShellEdgeCases:
    """ShellTool 边界条件。"""

    def test_empty_command_fails(self, ctx: ToolContext):
        t = ShellTool()
        r = t.execute(ctx, {"command": ""})
        assert not r.success

    def test_long_command(self, ctx: ToolContext):
        t = ShellTool()
        # Windows 命令行长度有限制，使用合理长度
        long_cmd = "echo " + "a" * 500
        r = t.execute(ctx, {"command": long_cmd})
        assert r.success


# ── 工具验证 — 参数校验 ──────────────────────────────────────────────────────


class TestToolSchemaValidation:
    """工具 JSON Schema 参数校验。"""

    def test_read_file_missing_required(self, ctx: ToolContext):
        """缺少 path 参数应报错。"""
        t = ReadFileTool()
        assert not t.validate({}), "缺少 path 应不通过校验"
        assert t.validate({"path": "x.txt"}), "有 path 应通过"

    def test_write_file_missing_required(self, ctx: ToolContext):
        """缺少 path 或 content 参数应报错。"""
        t = WriteFileTool()
        assert not t.validate({}), "空参数应不通过"
        assert not t.validate({"path": "x.txt"}), "缺少 content 应不通过"
        assert t.validate({"path": "x.txt", "content": "hi"}), "完整参数应通过"

    def test_edit_missing_required(self):
        t = EditTool()
        assert not t.validate({}), "空参数应不通过"
        assert t.validate({"path": "x", "mode": "append", "new_text": "hi"}), \
            "最小参数应通过"


# ── 多工具组合流程 ────────────────────────────────────────────────────────────


class TestMultiToolFlow:
    """多个 SWE 工具组合使用的流程测试。"""

    def test_write_edit_read_cycle(self, ctx: ToolContext, ws: Path):
        wt = WriteFileTool()
        et = EditTool()
        rt = ReadFileTool()

        # 1. 写入初始文件
        r = wt.execute(ctx, {"path": "app.py", "content": "x = 1\ny = 2\nz = 3\n"})
        assert r.success

        # 2. 替换行
        r = et.execute(ctx, {
            "path": "app.py", "mode": "replace_lines",
            "start_line": 2, "end_line": 2, "new_text": "y = 42\n",
        })
        assert r.success
        assert "y = 42" in (ws / "app.py").read_text()

        # 3. 插入行
        r = et.execute(ctx, {
            "path": "app.py", "mode": "insert",
            "start_line": 1, "new_text": "# header\n",
        })
        assert r.success

        # 4. 读取验证
        r = rt.execute(ctx, {"path": "app.py"})
        assert r.success
        assert "# header" in r.output
        assert "y = 42" in r.output
        assert "z = 3" in r.output

    def test_grep_integration(self, ctx: ToolContext, ws: Path):
        (ws / "src").mkdir(parents=True, exist_ok=True)
        (ws / "src/main.py").write_text("def hello():\n    pass\n")
        (ws / "src/utils.py").write_text("def helper():\n    pass\n")

        t = GrepTool()
        r = t.execute(ctx, {"pattern": "def "})
        assert r.success
        assert "main.py" in r.output
        assert "utils.py" in r.output

        r = t.execute(ctx, {"pattern": "nonexistent"})
        assert r.success
        assert "no matches" in r.output
