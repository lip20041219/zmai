"""read_file 范围读取的 read_key 粒度（P1-2）。

故障（修复前）：
  read_key 只由**整文件内容 hash** 构成，与本轮实际交付的行范围无关。于是：

      read(1,5)     → 注册 read_key = h(整个文件)
      read(100,105) → 文件没变、hash 相同，且 h(文件) 仍在可见窗口内
                    → 判定缓存命中，返回 [ReadCache]，**不交付 100-105 行**

  模型从未见过 100-105 行，却被要求"复用之前的读取结果"。这与 P1-1 是同一个
  不变量（不得让模型复用一份它看不到的内容）在"行范围"维度上的第二个破口。

修复：
  read_key 加入本次**实际交付的行范围**（``_range_read_key``）。命中路径与
  fresh 路径使用同一算式，因此同一范围两次读取仍得到同一个 key —— token 优化
  不受影响；不同范围必然得到不同 key —— 不会误报缓存命中。

不改动：cache_key（文件维度，仍为 mtime+size+内容 hash 二次确认）、LRU 上限、
``_read_visible`` 判据、``ContextManager.compact()``、completion gate。
"""

from __future__ import annotations

from pathlib import Path

from zmai.context.manager import ContextManager
from zmai.swe.tools import ReadFileTool
from zmai.tool import ToolContext

LINES = 120


def _src(n: int = LINES) -> str:
    return "".join(f"LINE_{i}\n" for i in range(1, n + 1))


def _rendered(src: str) -> str:
    """ReadFileTool 的编号正文（整文件读取时行号与真实行号一致）。"""
    return "".join(f"{i:>4}|{ln}"
                   for i, ln in enumerate(src.splitlines(keepends=True), 1))


def _project(tmp_path: Path, name: str = "app.py") -> str:
    src = _src()
    (tmp_path / name).write_text(src, encoding="utf-8")
    return src


def _ctx(root: Path, read_visible=None) -> ToolContext:
    return ToolContext(agent_id="p1_2_range", workspace_path=root,
                       project_path=root, timeout=10,
                       read_visible=read_visible)


def _cm() -> ContextManager:
    # 窗口放大：本组用例不掺入淘汰，只看范围维度
    return ContextManager({"context.max_chars": 10 ** 7,
                           "context.recent_window": 50,
                           "context.tool_result_window": 50})


def _register(cm: ContextManager, result) -> None:
    """模拟 agent 侧把一次工具结果注入上下文（agent.py 的真实调用形态）。"""
    cm.add_tool_result("read_file", True, result.output, meta=result.metadata)


def _read(tool: ReadFileTool, ctx: ToolContext, **params):
    r = tool.execute(ctx, {"path": "app.py", **params})
    assert r.success, r.error
    return r


# ═══════════════════════════════════════════════════════════════════
# T3-1 — 已读 1-5 行，再读 100-105 行：必须交付 100-105 行
# ═══════════════════════════════════════════════════════════════════


class TestOtherRangeIsNotCachedHit:
    def test_second_range_is_delivered(self, tmp_path: Path):
        _project(tmp_path)
        cm = _cm()
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, cm.is_read_visible)

        r1 = _read(tool, ctx, start_line=1, end_line=5)
        assert "LINE_5\n" in r1.output and "LINE_100" not in r1.output, \
            "前置条件：首次只交付 1-5 行"
        _register(cm, r1)
        assert cm.is_read_visible(r1.metadata["read_key"]) is True, \
            "前置条件：1-5 行确实在可见窗口内"

        r2 = _read(tool, ctx, start_line=100, end_line=105)
        assert "LINE_100" in r2.output, \
            f"模型从未见过 100-105 行，必须真正交付: {r2.output}"
        assert "[ReadCache]" not in r2.output, \
            f"不同行范围不得报缓存命中: {r2.output}"
        assert r2.metadata["read_key"] != r1.metadata["read_key"], \
            "不同行范围的 read_key 必须不同"


# ═══════════════════════════════════════════════════════════════════
# T3-2 — 已读 100-105 行，再整文件读：必须交付完整正文
# ═══════════════════════════════════════════════════════════════════


class TestWholeFileAfterRangeIsNotCachedHit:
    def test_whole_file_is_delivered(self, tmp_path: Path):
        src = _project(tmp_path)
        cm = _cm()
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, cm.is_read_visible)

        r1 = _read(tool, ctx, start_line=100, end_line=105)
        assert "LINE_1\n" not in r1.output, "前置条件：首次不含第 1 行"
        _register(cm, r1)
        assert cm.is_read_visible(r1.metadata["read_key"]) is True

        r2 = _read(tool, ctx)
        assert "LINE_1\n" in r2.output, \
            f"整文件读取必须交付第 1 行: {r2.output[:200]}"
        assert _rendered(src) in r2.output, "整文件读取必须真正交付完整正文"
        assert "[ReadCache]" not in r2.output, \
            f"行范围不同不得被范围读取命中: {r2.output[:200]}"
        # 文件本身没变，cached 语义保留（走的只是"重新交付正文"路径）
        assert r2.metadata.get("cached") is True
        assert r2.metadata["read_key"] != r1.metadata["read_key"]


# ═══════════════════════════════════════════════════════════════════
# T3-3 — 同一范围重复读：仍是轻量命中（token 优化不受影响）
# ═══════════════════════════════════════════════════════════════════


class TestSameRangeStaysLightweight:
    def test_repeat_same_range(self, tmp_path: Path):
        _project(tmp_path)
        cm = _cm()
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, cm.is_read_visible)

        r1 = _read(tool, ctx, start_line=1, end_line=5)
        key = r1.metadata["read_key"]
        _register(cm, r1)

        r2 = _read(tool, ctx, start_line=1, end_line=5)
        assert r2.metadata.get("cached") is True
        assert "[ReadCache]" in r2.output
        assert "LINE_1" not in r2.output, \
            f"同一范围仍应保持轻量命中，不重复交付正文: {r2.output}"
        assert r2.metadata["read_key"] == key, \
            "命中路径与 fresh 路径对同一范围必须生成一致的 key"


# ═══════════════════════════════════════════════════════════════════
# T3-4 — 范围读取被 compact 淘汰：必须重新交付正文
# ═══════════════════════════════════════════════════════════════════


class TestEvictedRangeIsReinjected:
    def test_compacted_range_redelivers_body(self, tmp_path: Path):
        _project(tmp_path)
        cm = ContextManager({"context.max_chars": 150,
                             "context.recent_window": 1,
                             "context.tool_result_window": 1})
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, cm.is_read_visible)

        r1 = _read(tool, ctx, start_line=1, end_line=5)
        key = r1.metadata["read_key"]
        _register(cm, r1)
        assert cm.is_read_visible(key) is True, "前置条件：刚注入时应可见"

        # 用无关结果 + 足量后续消息挤出窗口，并真正触发压缩
        cm.add_tool_result("shell_exec", True, "unrelated " + "y" * 80)
        for i in range(6):
            cm.add_message("user", f"第 {i} 轮说明：" + "x" * 200)
        cm.compact()

        assert cm.compact_count >= 1, "前置条件：必须真的发生过上下文压缩"
        assert cm.is_read_visible(key) is False, \
            "前置条件：首次交付的 1-5 行必须已被淘汰出可见窗口"

        # 文件内容 hash 没变，但正文已不可见 → 必须重新交付，不得回 [ReadCache]
        r2 = _read(tool, ctx, start_line=1, end_line=5)
        assert "[ReadCache]" not in r2.output, \
            f"正文已不可见，不得只回缓存提示: {r2.output}"
        assert "LINE_1" in r2.output and "LINE_5" in r2.output, \
            f"必须重新交付本范围正文: {r2.output}"
        assert r2.metadata.get("cached") is True, "文件本身没变，cached 语义保留"
        assert r2.metadata["read_key"] == key, \
            "同一范围在 fresh 重投路径上必须给出同一 key"
