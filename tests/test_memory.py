"""Memory 模块测试 — WorkingMemory + LongTermMemory + MemoryManager。"""

from __future__ import annotations

from pathlib import Path

import pytest

from zmai.memory.base import MemoryEntry
from zmai.memory.long_term import LongTermMemory
from zmai.memory.manager import MemoryManager
from zmai.memory.working import WorkingMemory


class TestMemoryEntry:
    def test_create(self):
        e = MemoryEntry(key="k", value="v")
        assert e.key == "k"
        assert e.value == "v"
        assert e.namespace == "default"
        assert e.created_at
        assert e.updated_at

    def test_to_dict(self):
        e = MemoryEntry(key="k", value={"a": 1}, namespace="ns")
        d = e.to_dict()
        assert d["key"] == "k"
        assert d["value"] == {"a": 1}
        assert d["namespace"] == "ns"

    def test_from_dict(self):
        d = {"key": "k", "value": 42, "namespace": "ns", "created_at": "", "updated_at": "", "ttl": None}
        e = MemoryEntry.from_dict(d)
        assert e.key == "k"
        assert e.value == 42

    def test_expired(self):
        import time
        e = MemoryEntry(key="k", value="v", ttl=1)
        assert not e.is_expired
        time.sleep(1.1)
        assert e.is_expired


class TestWorkingMemory:
    def test_store_and_read(self):
        wm = WorkingMemory()
        wm.store("name", "alice")
        assert wm.read("name") == "alice"

    def test_read_nonexistent(self):
        wm = WorkingMemory()
        assert wm.read("nope") is None

    def test_update(self):
        wm = WorkingMemory()
        wm.store("x", 1)
        wm.update("x", 2)
        assert wm.read("x") == 2

    def test_delete(self):
        wm = WorkingMemory()
        wm.store("x", 1)
        wm.delete("x")
        assert wm.read("x") is None

    def test_search(self):
        wm = WorkingMemory()
        wm.store("username", "alice")
        wm.store("user_age", 30)
        wm.store("config", "dark")
        results = wm.search("user")
        assert len(results) == 2

    def test_namespace_isolation(self):
        wm = WorkingMemory()
        wm.store("key", "ns1_val", namespace="ns1")
        wm.store("key", "ns2_val", namespace="ns2")
        assert wm.read("key", "ns1") == "ns1_val"
        assert wm.read("key", "ns2") == "ns2_val"

    def test_clear_namespace(self):
        wm = WorkingMemory()
        wm.store("a", 1)
        wm.store("b", 2)
        wm.clear()
        assert wm.read("a") is None

    def test_list_namespaces(self):
        wm = WorkingMemory()
        wm.store("k", "v", namespace="ns1")
        wm.store("k", "v", namespace="ns2")
        assert set(wm.list_namespaces()) == {"ns1", "ns2"}

    def test_lru_eviction(self):
        wm = WorkingMemory(max_size=3)
        wm.store("a", 1)
        wm.store("b", 2)
        wm.store("c", 3)
        wm.store("d", 4)  # 应淘汰 a（最早）
        assert wm.read("a") is None
        assert wm.read("b") == 2
        assert wm.read("c") == 3
        assert wm.read("d") == 4

    def test_lru_eviction_oldest_removed(self):
        wm = WorkingMemory(max_size=2)
        wm.store("x", 1)
        wm.store("y", 2)
        wm.store("z", 3)  # 应淘汰 x
        assert wm.read("x") is None
        assert wm.read("y") == 2
        assert wm.read("z") == 3

    def test_lru_eviction_within_namespace(self):
        """LRU 只在同一 namespace 内生效，不影响其他 namespace。"""
        wm = WorkingMemory(max_size=2)
        wm.store("a", 1, namespace="ns1")
        wm.store("b", 2, namespace="ns1")
        wm.store("c", 3, namespace="ns2")  # ns2 有单独大小
        wm.store("d", 4, namespace="ns1")  # 应淘汰 ns1 中的 a
        assert wm.read("a", namespace="ns1") is None
        assert wm.read("c", namespace="ns2") == 3


class TestLongTermMemory:
    def test_store_and_read(self, tmp_path: Path):
        lm = LongTermMemory(tmp_path)
        lm.store("key1", "value1")
        assert lm.read("key1") == "value1"

    def test_persistence(self, tmp_path: Path):
        lm = LongTermMemory(tmp_path)
        lm.store("k", "v")
        # 新建实例，从文件重建
        lm2 = LongTermMemory(tmp_path)
        assert lm2.read("k") == "v"

    def test_namespace_separation(self, tmp_path: Path):
        lm = LongTermMemory(tmp_path)
        lm.store("k", "a", namespace="x")
        lm.store("k", "b", namespace="y")
        assert lm.read("k", "x") == "a"
        assert lm.read("k", "y") == "b"

    def test_delete(self, tmp_path: Path):
        lm = LongTermMemory(tmp_path)
        lm.store("k", "v")
        lm.delete("k")
        assert lm.read("k") is None

    def test_clear(self, tmp_path: Path):
        lm = LongTermMemory(tmp_path)
        lm.store("k", "v")
        lm.clear()
        assert lm.read("k") is None

    def test_append_reload(self, tmp_path: Path):
        """多次 store 后重建实例，验证所有条目可读取（append-only 持久化）。"""
        lm = LongTermMemory(tmp_path)
        lm.store("k1", "v1")
        lm.store("k2", "v2")
        lm.store("k3", "v3")
        lm2 = LongTermMemory(tmp_path)
        assert lm2.read("k1") == "v1"
        assert lm2.read("k2") == "v2"
        assert lm2.read("k3") == "v3"

    def test_append_overwrite(self, tmp_path: Path):
        """同一 key 后写入的值覆盖先写入的值。"""
        lm = LongTermMemory(tmp_path)
        lm.store("k", "first")
        lm.store("k", "second")  # 覆盖
        lm2 = LongTermMemory(tmp_path)
        assert lm2.read("k") == "second"

    def test_append_delete(self, tmp_path: Path):
        """删除后 key 不可读。"""
        lm = LongTermMemory(tmp_path)
        lm.store("k", "v")
        lm.delete("k")
        lm2 = LongTermMemory(tmp_path)
        assert lm2.read("k") is None

    def test_append_interleaved(self, tmp_path: Path):
        """多 namespace 交错写入。"""
        lm = LongTermMemory(tmp_path)
        lm.store("a", "1", namespace="x")
        lm.store("b", "2", namespace="y")
        lm.store("c", "3", namespace="x")
        lm2 = LongTermMemory(tmp_path)
        assert lm2.read("a", "x") == "1"
        assert lm2.read("c", "x") == "3"
        assert lm2.read("b", "y") == "2"


class TestMemoryManager:
    def test_working_memory_per_agent(self):
        mm = MemoryManager()
        wm_a = mm.working("agent_a")
        wm_b = mm.working("agent_b")
        assert wm_a is not wm_b
        wm_a.store("k", "val_a")
        wm_b.store("k", "val_b")
        assert mm.working("agent_a").read("k") == "val_a"
        assert mm.working("agent_b").read("k") == "val_b"

    def test_long_term_per_agent(self, tmp_path: Path):
        mm = MemoryManager(long_term_root=tmp_path)
        lm = mm.long_term("agent_x")
        lm.store("pref", "dark")
        assert mm.long_term("agent_x").read("pref") == "dark"

    def test_cleanup(self):
        mm = MemoryManager()
        mm.working("a")
        mm.long_term("a")
        mm.cleanup("a")
        assert "a" not in mm._working
        assert "a" not in mm._long_term

    def test_exists(self, tmp_path: Path):
        mm = MemoryManager(long_term_root=tmp_path)
        assert not mm.exists("nobody")
        mm.long_term("someone").store("k", "v")
        assert mm.exists("someone")

    def test_persist_and_restore(self, tmp_path: Path):
        """persist 后将 Working Memory 保存到 Long-term，restore 可恢复。"""
        mm = MemoryManager(long_term_root=tmp_path)
        wm = mm.working("agent_p")
        wm.store("key1", "val1")
        wm.store("key2", {"nested": True})
        mm.persist("agent_p")

        # 新建 Manager，通过 restore 恢复
        mm2 = MemoryManager(long_term_root=tmp_path)
        restored = mm2.restore("agent_p")
        assert restored == 2
        assert mm2.working("agent_p").read("key1") == "val1"
        assert mm2.working("agent_p").read("key2") == {"nested": True}

    def test_persist_empty_no_error(self, tmp_path: Path):
        """没有 Working Memory 的 agent 调用 persist 不报错。"""
        mm = MemoryManager(long_term_root=tmp_path)
        mm.persist("nonexistent")  # 不应抛异常

    def test_restore_empty_no_error(self, tmp_path: Path):
        """没有 Long-term 数据的 agent 调用 restore 返回 0。"""
        mm = MemoryManager(long_term_root=tmp_path)
        count = mm.restore("nobody")
        assert count == 0
