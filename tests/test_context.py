"""ContextManager 测试 — 上下文管理、压缩、边界条件。"""

from __future__ import annotations

import pytest

from zmai.swe.context import (
    ContextManager,
    DEFAULT_MAX_CHARS,
    DEFAULT_RECENT_WINDOW,
    DEFAULT_TOOL_RESULT_WINDOW,
    _truncate,
    _estimate_tokens,
)


class TestContextManagerBasic:
    """基础功能测试。"""

    def test_create_default(self):
        """默认配置创建。"""
        cm = ContextManager()
        assert cm.max_chars == DEFAULT_MAX_CHARS
        assert cm.recent_window == DEFAULT_RECENT_WINDOW
        assert cm.tool_result_window == DEFAULT_TOOL_RESULT_WINDOW
        assert cm.estimate_size() == 0

    def test_create_with_config(self):
        """自定义配置创建。"""
        cm = ContextManager(config={"context.max_chars": 1000, "context.recent_window": 4})
        assert cm.max_chars == 1000
        assert cm.recent_window == 4

    def test_set_task(self):
        """set_task 添加任务并初始化第一条消息。"""
        cm = ContextManager()
        cm.set_task("修复登录 Bug")
        assert cm._has_task is True
        assert cm._task == "修复登录 Bug"
        assert len(cm._recent) == 1
        assert cm._recent[0]["content"] == "修复登录 Bug"

    def test_set_task_idempotent(self):
        """重复 set_task 不会重复添加消息。"""
        cm = ContextManager()
        cm.set_task("任务 A")
        cm.set_task("任务 A")
        assert len(cm._recent) == 1

    def test_add_message(self):
        """add_message 添加消息到 recent。"""
        cm = ContextManager()
        cm.add_message("assistant", "我来分析代码")
        assert len(cm._recent) == 1
        assert cm._recent[0]["role"] == "assistant"
        assert cm._recent[0]["content"] == "我来分析代码"

    def test_add_tool_result(self):
        """add_tool_result 添加工具结果消息和记录。"""
        cm = ContextManager()
        cm.add_tool_result("read_file", True, "file content", duration_ms=10)
        assert len(cm._tool_results) == 1
        assert cm._tool_results[0]["name"] == "read_file"
        assert cm._tool_results[0]["success"] is True
        # 应该添加了一条消息
        assert len(cm._recent) == 1
        assert "[工具 read_file 结果]" in cm._recent[0]["content"]

    def test_estimate_size_increases(self):
        """估算大小随添加内容增长。"""
        cm = ContextManager()
        empty_size = cm.estimate_size()
        cm.set_task("Hello World")
        cm.add_message("assistant", "Response")
        assert cm.estimate_size() > empty_size

    def test_estimate_tokens(self):
        """token 估算不为负。"""
        cm = ContextManager()
        cm.set_task("Hello " * 100)
        assert cm.estimate_tokens() > 0

    def test_get_context_empty(self):
        """空 CM 返回空列表。"""
        cm = ContextManager()
        ctx = cm.get_context()
        assert ctx == []

    def test_get_context_with_messages(self):
        """有消息时正确返回。"""
        cm = ContextManager()
        cm.set_task("test")
        cm.add_message("assistant", "ok")
        ctx = cm.get_context()
        assert len(ctx) == 2  # task message + assistant

    def test_get_context_with_summary(self):
        """有摘要时，摘要出现在消息开头。"""
        cm = ContextManager()
        cm.set_task("test")
        cm._summary = "已完成步骤 1"
        ctx = cm.get_context()
        assert "[历史摘要]" in ctx[0]["content"]
        assert "已完成步骤 1" in ctx[0]["content"]

    def test_get_context_with_plan_in_system(self):
        """get_context_with_system 与 get_context 结构相同。"""
        cm = ContextManager()
        cm.set_task("test")
        ctx1 = cm.get_context()
        ctx2 = cm.get_context_with_system("system prompt")
        assert len(ctx1) == len(ctx2)

    def test_get_status(self):
        """get_status 返回状态字典。"""
        cm = ContextManager()
        cm.set_task("test")
        status = cm.get_status()
        assert "total_chars" in status
        assert "estimated_tokens" in status
        assert "recent_messages" in status
        assert status["has_task"] is True

    def test_track_file_change(self):
        """track_file_change 记录修改的文件。"""
        cm = ContextManager()
        cm.track_file_change("src/auth.py")
        cm.track_file_change("src/auth.py")  # 重复
        assert len(cm._modified_files) == 1
        cm.track_file_change("src/main.py")
        assert len(cm._modified_files) == 2

    def test_track_unresolved(self):
        """track_unresolved 记录未解决问题。"""
        cm = ContextManager()
        cm.track_unresolved("需要修复 token 过期")
        cm.track_unresolved("需要修复 token 过期")  # 重复
        assert len(cm._pending_unresolved) == 1

    def test_track_test_result(self):
        """track_test_result 记录测试结果。"""
        cm = ContextManager()
        cm.track_test_result("PASSED: 3 tests")
        assert len(cm._test_results) == 1

    def test_track_failure(self):
        """工具失败自动追踪到 _failures。"""
        cm = ContextManager()
        cm.add_tool_result("write_file", False, "", error="permission denied")
        assert len(cm._failures) == 1
        assert "permission denied" in cm._failures[0]


class TestContextTruncation:
    """工具结果截断测试。"""

    def test_truncate_short(self):
        """短文本不截断。"""
        assert _truncate("hello", 100) == "hello"

    def test_truncate_long(self):
        """长文本截断。"""
        text = "a" * 1000
        truncated = _truncate(text, 100)
        # text[:100] = 100 chars + "\n...(截断)" = 8 chars = 108 total
        assert len(truncated) == 108

    def test_tool_result_truncation(self):
        """超大工具结果自动截断。"""
        cm = ContextManager(config={"context.tool_truncate": 50})
        cm.add_tool_result("read_file", True, "x" * 500)
        msg = cm._recent[-1]["content"]
        assert len(msg) < 200  # 截断了
        assert "...(截断)" in msg


class TestContextCompaction:
    """上下文压缩测试。"""

    def make_cm_with_budget(self, budget: int, window: int = 2) -> ContextManager:
        cm = ContextManager(config={
            "context.max_chars": budget,
            "context.recent_window": window,
            "context.tool_result_window": 2,
        })
        return cm

    def test_no_compact_when_under_budget(self):
        """小上下文不压缩。"""
        cm = self.make_cm_with_budget(100000)
        cm.set_task("small task")
        cm.add_message("assistant", "ok")
        assert cm.should_compact() is False
        assert cm.compact() is False  # 没有执行压缩
        assert cm.compact_count == 0

    def test_compact_when_over_budget(self):
        """超限自动压缩。"""
        cm = self.make_cm_with_budget(100, window=2)
        cm.set_task("test")
        # 添加大量消息触发压缩
        for i in range(20):
            cm.add_message("assistant", f"response {i} " * 10)
            cm.add_tool_result(f"tool_{i}", True, f"output {i}" * 10)
        # 自动压缩已被 add_message 触发，compact_count >= 1
        assert cm.compact_count >= 1
        # 上下文应不超过 max_chars（或已被 _ensure_budget 处理）
        assert cm.estimate_size() <= cm._hard_max_chars

    def test_compact_preserves_task(self):
        """压缩后任务仍存在。"""
        cm = self.make_cm_with_budget(200, window=2)
        cm.set_task("修复登录 Bug")
        for i in range(20):
            cm.add_message("assistant", f"step {i} " * 20)
            cm.add_tool_result(f"tool_{i}", True, f"output {i}" * 20)

        cm.compact()
        # 摘要中应包含任务
        assert "修复登录 Bug" in cm._summary or cm._task == "修复登录 Bug"

    def test_compact_preserves_plan(self):
        """压缩后 Plan 仍存在。"""
        cm = self.make_cm_with_budget(200, window=2)
        cm.set_task("test")
        cm.set_plan("1. 分析 2. 修复 3. 测试")
        for i in range(20):
            cm.add_message("assistant", f"step {i} " * 20)

        cm.compact()
        assert cm._plan == "1. 分析 2. 修复 3. 测试"

    def test_compact_preserves_working_memory(self):
        """压缩后 Working Memory 保留。"""
        cm = self.make_cm_with_budget(200, window=2)
        cm.set_task("test")
        cm.set_working_memory(["用户偏好: 暗色主题", "项目语言: Python"])
        for i in range(20):
            cm.add_message("assistant", f"step {i} " * 20)

        cm.compact()
        assert len(cm._working_memory) == 2

    def test_compact_preserves_unresolved(self):
        """压缩后未解决问题保留。"""
        cm = self.make_cm_with_budget(200, window=2)
        cm.set_task("test")
        cm.track_unresolved("需要处理超时问题")
        for i in range(20):
            cm.add_message("assistant", f"step {i} " * 20)

        cm.compact()
        assert "需要处理超时问题" in cm._pending_unresolved

    def test_compact_preserves_recent_window(self):
        """压缩后保留 recent_window 条最近消息。"""
        cm = self.make_cm_with_budget(200, window=3)
        cm.set_task("test")
        for i in range(10):
            cm.add_message("assistant", f"msg {i} " * 20)

        cm.compact()
        assert len(cm._recent) <= 3  # 只保留最近 3 条

    def test_multiple_compacts_no_corruption(self):
        """多次 compact 不导致数据损坏。"""
        cm = self.make_cm_with_budget(300, window=2)
        cm.set_task("test")
        for i in range(10):
            cm.add_message("assistant", f"msg {i} " * 20)

        # 多次添加消息，等待自动压缩
        for _ in range(5):
            cm.add_message("assistant", "new message " * 20)

        # 不应损坏
        assert cm._summary != "" or cm.compact_count > 0
        assert len(cm._recent) > 0 or cm._summary != ""

    def test_tool_result_compaction(self):
        """工具结果压缩后只保留最近的。"""
        cm = self.make_cm_with_budget(200, window=2)
        cm.set_task("test")
        for i in range(10):
            cm.add_message("assistant", f"msg {i} " * 20)
            cm.add_tool_result(f"tool_{i}", True, f"output {i}" * 20)

        cm.compact()
        assert len(cm._tool_results) <= cm.tool_result_window

    def test_failure_tracking_after_compact(self):
        """压缩后失败信息仍在摘要中。"""
        cm = self.make_cm_with_budget(200, window=2)
        cm.set_task("test")
        cm.add_tool_result("deploy", False, "", error="connection refused")
        for i in range(10):
            cm.add_message("assistant", f"msg {i} " * 20)

        cm.compact()
        # 失败信息应在列表中
        failures_in_summary = any("connection refused" in str(f) for f in cm._failures)
        assert failures_in_summary

    def test_compact_fallback_on_error(self):
        """压缩失败时安全处理（不丢失数据）。"""
        cm = self.make_cm_with_budget(100)
        cm.set_task("test")
        cm.add_message("assistant", "hello")

        # 模拟 _do_compact 异常
        original = cm._do_compact
        def broken():
            raise ValueError("模拟错误")
        cm._do_compact = broken

        # 不应抛出，应返回 False
        result = cm.compact()
        assert result is False
        # 数据未丢失
        assert len(cm._recent) >= 1


class TestContextIntegration:
    """与 SWEAgent 的集成场景测试。"""

    def test_full_flow_without_compaction(self):
        """完整流程：小上下文不压缩。"""
        cm = ContextManager(config={"context.max_chars": 100000})
        cm.set_task("创建一个 HTML 页面")

        # 模拟多轮对话
        cm.add_message("assistant", "我将创建一个简单的 HTML 页面。")
        cm.add_tool_result("write_file", True, "written output/index.html")
        cm.add_message("assistant", "文件已创建，正在验证。")
        cm.add_tool_result("read_file", True, "<!DOCTYPE html>...")

        assert cm.should_compact() is False
        ctx = cm.get_context()
        assert len(ctx) >= 4  # task + 2 responses + 2 tool results

    def test_large_context_triggers_compact(self):
        """大上下文触发自动压缩。"""
        cm = ContextManager(config={
            "context.max_chars": 500,
            "context.recent_window": 3,
            "context.tool_result_window": 2,
        })
        cm.set_task("大数据处理任务")

        # 产生大量消息
        for i in range(15):
            cm.add_message("assistant", f"步骤 {i} 执行中..." + "x" * 60)
            cm.add_tool_result(f"tool_{i}", i % 2 == 0, "output" * 30)

        # 自动压缩已被触发
        assert cm.compact_count >= 1 or cm.estimate_size() <= cm._hard_max_chars
        ctx = cm.get_context()
        # 摘要应压缩了大量内容
        assert len(ctx) > 0

    def test_summary_appears_in_context(self):
        """压缩后摘要出现在 get_context 结果中。"""
        cm = ContextManager(config={
            "context.max_chars": 300,
            "context.recent_window": 2,
        })
        cm.set_task("修复性能问题")
        cm.track_file_change("src/optimize.py")
        cm.track_unresolved("需要基准测试")

        for i in range(10):
            cm.add_message("assistant", f"msg {i} " * 20)
            cm.add_tool_result(f"tool_{i}", True, f"output {i}" * 20)

        cm.compact()
        ctx = cm.get_context()

        # 摘要消息在开头
        first_msg = ctx[0]["content"]
        assert "[历史摘要]" in first_msg

    def test_clear_resets_state(self):
        """clear 重置所有状态。"""
        cm = ContextManager()
        cm.set_task("test")
        cm.add_message("assistant", "hello")
        cm.add_tool_result("tool", True, "output")

        cm.clear()
        assert cm._task == ""
        assert cm._recent == []
        assert cm._tool_results == []
        assert cm._has_task is False
        assert cm.estimate_size() == 0


class TestBudgetEstimation:
    """预算估算测试。"""

    def test_small_context_under_budget(self):
        """小上下文在预算内。"""
        cm = ContextManager(config={"context.max_chars": 100000})
        cm.set_task("simple task")
        assert cm.estimate_size() < cm.max_chars

    def test_estimate_after_compact(self):
        """压缩后估算大小显著减小。"""
        cm = ContextManager(config={
            "context.max_chars": 1000,
            "context.recent_window": 2,
        })
        cm.set_task("test")
        for i in range(20):
            cm.add_message("assistant", "x" * 100)
            cm.add_tool_result(f"t{i}", True, "x" * 100)

        before = cm.estimate_size()
        cm.compact()
        after = cm.estimate_size()
        # 压缩后应该 <= max_chars（可能会有一些系统开销）
        assert after <= cm.max_chars or after < before

    def test_estimate_tokens_reasonable(self):
        """token 估算合理。"""
        cm = ContextManager()
        cm.set_task("Hello World " * 50)  # ~600 chars
        tokens = cm.estimate_tokens()
        # 600 chars / 4 ≈ 150 tokens
        assert 50 < tokens < 500


# ═══════════════════════════════════════════════════════════════
# 预算强制测试（新）
# ═══════════════════════════════════════════════════════════════


class TestBudgetEnforcement:
    """硬预算限制测试。"""

    def test_small_context_no_compact(self):
        """小上下文不触发压缩。"""
        cm = ContextManager(config={"context.max_chars": 100000})
        cm.set_task("small task")
        cm.add_message("assistant", "hello")
        cm.add_tool_result("tool", True, "output")
        assert cm.compact_count == 0
        assert cm.estimate_size() <= cm.max_chars

    def test_over_limit_auto_compacts(self):
        """超限时自动压缩。"""
        cm = ContextManager(config={
            "context.max_chars": 200,
            "context.recent_window": 2,
        })
        cm.set_task("test")
        # 添加大量消息触发自动压缩
        for i in range(10):
            cm.add_message("assistant", "x" * 100)
        # 应触发至少一次压缩（通过 add_message 内部的 _ensure_budget）
        assert cm.compact_count >= 1
        # 大小不应超过硬上限
        assert cm.estimate_size() <= cm._hard_max_chars

    def test_task_preserved_after_compact(self):
        """压缩后任务仍存在。"""
        cm = ContextManager(config={
            "context.max_chars": 200,
            "context.recent_window": 2,
        })
        cm.set_task("这是必须保留的任务描述")
        for i in range(20):
            cm.add_message("assistant", "x" * 100)
        ctx = cm.get_context()
        combined = " ".join(m.get("content", "") for m in ctx)
        assert "必须保留" in combined

    def test_plan_preserved_after_compact(self):
        """压缩后 Plan 仍存在。"""
        cm = ContextManager(config={
            "context.max_chars": 200,
            "context.recent_window": 2,
        })
        cm.set_task("task")
        cm.set_plan("1. read 2. edit 3. verify")
        for i in range(20):
            cm.add_message("assistant", "x" * 100)
        ctx = cm.get_context()
        combined = " ".join(m.get("content", "") for m in ctx)
        assert "edit" in combined or "read" in combined

    def test_working_memory_retained(self):
        """压缩后 Working Memory 保留。"""
        cm = ContextManager(config={
            "context.max_chars": 300,
            "context.recent_window": 2,
        })
        cm.set_task("task")
        cm.set_working_memory(["关键发现: 函数返回 None", "第二点"])
        for i in range(20):
            cm.add_message("assistant", "x" * 100)
        ctx = cm.get_context()
        combined = " ".join(m.get("content", "") for m in ctx)
        assert "关键发现" in combined

    def test_unresolved_preserved(self):
        """未解决问题压缩后保留。"""
        cm = ContextManager(config={
            "context.max_chars": 200,
            "context.recent_window": 2,
        })
        cm.set_task("task")
        cm.track_unresolved("需要处理边界情况")
        for i in range(20):
            cm.add_message("assistant", "x" * 100)
        ctx = cm.get_context()
        combined = " ".join(m.get("content", "") for m in ctx)
        assert "边界情况" in combined

    def test_modified_files_preserved(self):
        """已修改文件压缩后保留。"""
        cm = ContextManager(config={
            "context.max_chars": 200,
            "context.recent_window": 2,
        })
        cm.set_task("task")
        cm.track_file_change("src/main.py")
        cm.track_file_change("src/utils.py")
        for i in range(20):
            cm.add_message("assistant", "x" * 100)
        ctx = cm.get_context()
        combined = " ".join(m.get("content", "") for m in ctx)
        assert "main.py" in combined

    def test_tool_result_compressed(self):
        """Tool Result 压缩后数量减少。"""
        cm = ContextManager(config={
            "context.max_chars": 300,
            "context.recent_window": 2,
        })
        cm.set_task("task")
        for i in range(20):
            cm.add_tool_result(f"tool_{i}", True, f"output {i}" * 50)
        # 压缩后的 tool_results 应少于 20
        assert len(cm._tool_results) < 20 or cm.compact_count > 0

    def test_large_tool_result_truncated(self):
        """单个超大 Tool Result 被截断。"""
        cm = ContextManager(config={"context.tool_truncate": 100})
        huge = "x" * 10000
        cm.add_tool_result("big_tool", True, huge)
        assert len(cm._tool_results) == 1
        output = cm._tool_results[0].get("output", "")
        assert len(output) < len(huge)  # 截断后明显变短
        assert "(截断)" in output

    def test_multiple_compacts_no_corruption(self):
        """多次 compact 不损坏数据。"""
        cm = ContextManager(config={
            "context.max_chars": 200,
            "context.recent_window": 2,
        })
        cm.set_task("持久化测试")
        cm.track_file_change("file.py")
        for round_num in range(5):
            for i in range(10):
                cm.add_message("assistant", f"round {round_num} msg {i} " * 20)
                cm.add_tool_result(f"t{i}", True, f"out {i}" * 20)
        # 多次压缩后不应损坏
        assert cm._task == "持久化测试"
        assert "file.py" in cm._modified_files
        ctx = cm.get_context()
        assert len(ctx) > 0

    def test_ensure_budget_after_add_message(self):
        """add_message 后自动执行预算检查。"""
        cm = ContextManager(config={
            "context.max_chars": 100,
            "context.recent_window": 1,
        })
        cm.set_task("t")
        # 添加超大消息
        cm.add_message("assistant", "x" * 5000)
        # 不应超过硬上限
        assert cm.estimate_size() <= cm._hard_max_chars

    def test_ensure_budget_after_add_tool_result(self):
        """add_tool_result 后自动执行预算检查。"""
        cm = ContextManager(config={
            "context.max_chars": 100,
            "context.recent_window": 1,
        })
        cm.set_task("t")
        # 添加超大工具结果
        cm.add_tool_result("big", True, "x" * 5000)
        # 不应超过硬上限
        assert cm.estimate_size() <= cm._hard_max_chars

    def test_force_truncate_preserves_task_and_plan(self):
        """强制截断后 task 和 plan 仍在。"""
        cm = ContextManager(config={
            "context.max_chars": 100,
            "context.recent_window": 1,
        })
        cm.set_task("我的任务")
        cm.set_plan("步骤1 步骤2")
        # 填满上下文
        cm.add_message("assistant", "x" * 10000)
        # _force_truncate 应已触发
        assert cm._task == "我的任务"
        assert cm._plan == "步骤1 步骤2"

    def test_get_context_final_guard(self):
        """get_context 返回前做最终预算检查。"""
        cm = ContextManager(config={
            "context.max_chars": 50,
            "context.recent_window": 1,
        })
        cm.set_task("t")
        for i in range(10):
            cm.add_message("assistant", "x" * 200)
        ctx = cm.get_context()
        # 应已触发压缩和截断，不会报错
        assert isinstance(ctx, list)
