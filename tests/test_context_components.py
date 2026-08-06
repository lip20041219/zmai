"""Tests for zmai.context — SlidingWindow, RecentMessages, SummaryMemory, ContextPruner."""

from __future__ import annotations

import pytest
from zmai.context.window import SlidingWindow, RecentMessages
from zmai.context.memory import SummaryMemory
from zmai.context.pruner import ContextPruner, PruneAction


class TestSlidingWindow:
    def test_create_default(self):
        w = SlidingWindow(size=5)
        assert w.size == 5
        assert w.count == 0
        assert w.items == []

    def test_add_within_size(self):
        w = SlidingWindow(size=3)
        slid = w.add({"role": "user", "content": "hello"})
        assert slid == []
        assert w.count == 1

    def test_slide_when_full(self):
        w = SlidingWindow(size=2)
        w.add({"role": "user", "content": "a"})
        w.add({"role": "user", "content": "b"})
        slid = w.add({"role": "user", "content": "c"})
        assert len(slid) == 1
        assert slid[0]["content"] == "a"
        assert w.count == 2
        assert w.items[-1]["content"] == "c"

    def test_slide_multiple(self):
        w = SlidingWindow(size=1)
        w.add({"role": "user", "content": "a"})
        slid = w.add({"role": "user", "content": "b"})
        assert len(slid) == 1
        assert slid[0]["content"] == "a"

    def test_add_many(self):
        w = SlidingWindow(size=2)
        all_slid = w.add_many([
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "user", "content": "d"},
        ])
        # 4 adds, size 2 = 2 slid out
        assert len(all_slid) == 2
        assert all_slid[0]["content"] == "a"
        assert w.count == 2

    def test_remove_oldest(self):
        w = SlidingWindow(size=5)
        w.add_many([{"role": "user", "content": str(i)} for i in range(5)])
        removed = w.remove_oldest(2)
        assert len(removed) == 2
        assert removed[0]["content"] == "0"
        assert w.count == 3

    def test_resize(self):
        w = SlidingWindow(size=5)
        w.add_many([{"role": "user", "content": str(i)} for i in range(5)])
        assert w.count == 5
        w.size = 2
        assert w.count == 2
        assert w.items[-1]["content"] == "4"

    def test_is_full(self):
        w = SlidingWindow(size=2)
        assert w.is_full is False
        w.add({"role": "user", "content": "a"})
        assert w.is_full is False
        w.add({"role": "user", "content": "b"})
        assert w.is_full is True
        # Still full after slide: add removes one, adds one -> still 2
        w.add({"role": "user", "content": "c"})
        assert w.is_full is True

    def test_total_added(self):
        w = SlidingWindow(size=3)
        w.add({"role": "user", "content": "a"})
        w.add({"role": "user", "content": "b"})
        assert w.total_added == 2
        w.add({"role": "user", "content": "c"})
        assert w.total_added == 3

    def test_on_slide_callback(self):
        cb_history = []
        def on_slide(msg, idx):
            cb_history.append((msg["content"], idx))
        w = SlidingWindow(size=2, on_slide=on_slide)
        w.add_many([{"content": "a"}, {"content": "b"}, {"content": "c"}])
        assert len(cb_history) == 1
        assert cb_history[0][0] == "a"

    def test_clear(self):
        w = SlidingWindow(size=3)
        w.add_many([{"content": "a"}, {"content": "b"}])
        w.clear()
        assert w.count == 0
        assert w.total_added == 0

    def test_get_status(self):
        w = SlidingWindow(size=3)
        w.add({"content": "a"})
        s = w.get_status()
        assert s["count"] == 1
        assert s["size"] == 3


class TestRecentMessages:
    def test_add_message(self):
        r = RecentMessages(window_size=5)
        slid = r.add_message("user", "hello")
        assert len(r.messages) == 1
        assert slid == []

    def test_add_tool_result(self):
        r = RecentMessages(window_size=5)
        slid = r.add_tool_result({"name": "read", "success": True, "output": "content"})
        assert len(r.tool_results) == 1
        assert r.tool_results[0]["name"] == "read"

    def test_add_tool_result_triggers_slide(self):
        r = RecentMessages(window_size=5, tool_window=2)
        for i in range(3):
            r.add_tool_result({"name": f"tool_{i}", "success": True, "output": ""})
        assert len(r.tool_results) == 2  # 只保留 2 个

    def test_message_slide(self):
        r = RecentMessages(window_size=2)
        r.add_message("user", "a")
        r.add_message("user", "b")
        slid = r.add_message("user", "c")
        assert len(slid) == 1
        assert slid[0]["content"] == "a"
        assert len(r.messages) == 2

    def test_clear(self):
        r = RecentMessages(window_size=5)
        r.add_message("user", "a")
        r.add_message("user", "b")
        r.clear()
        assert r.message_count == 0
        assert r.tool_results == []

    def test_get_status(self):
        r = RecentMessages(window_size=5)
        r.add_message("user", "hello")
        s = r.get_status()
        assert s["messages"]["count"] == 1

    def test_pop_oldest_message(self):
        r = RecentMessages(window_size=5)
        r.add_message("user", "a")
        r.add_message("user", "b")
        popped = r.pop_oldest_message()
        assert popped is not None
        assert popped["content"] == "a"
        assert r.message_count == 1

    def test_pop_oldest_tool_result(self):
        r = RecentMessages(window_size=5)
        r.add_tool_result({"name": "t1", "success": True, "output": ""})
        r.add_tool_result({"name": "t2", "success": True, "output": ""})
        popped = r.pop_oldest_tool_result()
        assert popped is not None
        assert popped["name"] == "t1"
        assert len(r.tool_results) == 1


class TestSummaryMemory:
    def test_add_summary(self):
        m = SummaryMemory()
        m.add_summary("已完成步骤 1")
        assert m.summary_count == 1
        assert "已完成步骤 1" in m.get_combined_summary()

    def test_dedup(self):
        m = SummaryMemory()
        m.add_summary("相同摘要")
        m.add_summary("相同摘要")
        assert m.summary_count == 1

    def test_compress_generates_summary(self):
        m = SummaryMemory()
        old_msgs = [{"role": "user", "content": "hello"}]
        text = m.compress(old_msgs, [])
        assert text != ""
        assert "Previous 1" in text
        assert m.summary_count == 1

    def test_compress_with_tool_info(self):
        m = SummaryMemory()
        old_msgs = [
            {"role": "user", "content": "FAIL: build error", "metadata": {"tool": "shell"}},
        ]
        text = m.compress(old_msgs, [])
        assert "FAIL" in text or "Previous" in text

    def test_track_file(self):
        m = SummaryMemory()
        m.track_file("src/main.py")
        m.track_file("src/main.py")  # 去重
        assert len(m.modified_files) == 1
        m.track_file("src/utils.py")
        assert len(m.modified_files) == 2

    def test_track_failure(self):
        m = SummaryMemory()
        m.track_failure("deploy: connection refused")
        assert "connection refused" in m.failures[0]

    def test_shrink(self):
        m = SummaryMemory(max_chars=50)
        m.add_summary("a" * 40)
        m.add_summary("b" * 40)
        assert m.estimate_size() > 50
        m.shrink(50)
        assert m.estimate_size() <= 50

    def test_clear(self):
        m = SummaryMemory()
        m.add_summary("test")
        m.track_file("x.py")
        m.clear()
        assert m.summary_count == 0
        assert m.modified_files == []

    def test_get_combined_empty(self):
        m = SummaryMemory()
        assert m.get_combined_summary() == ""

    def test_to_dict(self):
        m = SummaryMemory()
        m.add_summary("hello")
        d = m.to_dict()
        assert d["summary_count"] == 1
        assert "combined_size" in d


class TestContextPruner:
    def test_evaluate_within_budget(self):
        p = ContextPruner(max_chars=1000)
        action = p.evaluate(500)
        assert action.should_prune is False
        assert action.should_compact is False

    def test_evaluate_over_budget(self):
        p = ContextPruner(max_chars=100)
        action = p.evaluate(150)
        assert action.should_prune is True
        assert action.should_compact is True
        assert action.should_force_truncate is False

    def test_evaluate_over_hard_limit(self):
        p = ContextPruner(max_chars=100)
        action = p.evaluate(200)  # hard_limit=150
        assert action.should_prune is True
        assert action.should_compact is True
        assert action.should_force_truncate is True

    def test_default_config(self):
        p = ContextPruner()
        assert p.max_chars == 32000
        assert p.hard_limit == 48000

    def test_get_status(self):
        p = ContextPruner(max_chars=1000)
        s = p.get_status()
        assert s["max_chars"] == 1000


class TestPruneAction:
    def test_defaults(self):
        a = PruneAction()
        assert a.should_prune is False
        assert a.should_compact is False
        assert a.reason == ""
