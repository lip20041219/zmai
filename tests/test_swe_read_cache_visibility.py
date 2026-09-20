"""context-aware ReadCache（P1-1）。

故障（修复前）：
  ReadFileTool 命中缓存时**无条件**返回"请复用之前的读取结果"且不含内容。但
  "文件没变"与"模型现在还看得见"是两件事 —— `agent.py` 每步调用 `cm.compact()`
  会把早先的 read 结果滚出 recent window 压成摘要，于是：

      读文件 → compact 淘汰 → 再读同一未变文件
      → 只拿到"请复用之前的读取结果"，而原文已不在上下文

  read_file 是唯一带行号的读取路径，被关死后"源码读取 → 根因定位 → 精确修改"
  整条闭环失去依据。

修复（两个维度分开判定）：
  ① 文件维度：sha256 一致            → `_cache_hit`（既有判据，未改动）
  ② 上下文维度：内容仍在可见窗口      → `ctx.read_visible(read_key)`

      ① 且 ②           → 轻量 [ReadCache] 提示，不重复注入正文（保住 token 优势）
      ① 但非 ②         → 走正常渲染路径，重新交回源码 + 行号（cached 仍为 True）
      非 ①             → 重新读盘（新鲜度不变）
      ② 无法判定        → fail-closed，按"不可见"处理

判据只查最近消息窗口（模型实际读到的那份），不查 `_memory` 历史摘要；摘要不是
原文，模型无法据它拿到源码。
"""

from __future__ import annotations

import json
from pathlib import Path

from zmai.context.manager import ContextManager
from zmai.swe.tools import ReadFileTool
from zmai.tool import ToolContext

SRC = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"


def _ctx(root: Path, read_visible=None) -> ToolContext:
    return ToolContext(agent_id="p1_1_cache", workspace_path=root,
                       project_path=root, timeout=10,
                       read_visible=read_visible)


def _body(src: str = SRC) -> str:
    """ReadFileTool 的编号正文：行号 + 原始行的直接拼接（行自带换行）。"""
    return "".join(f"{i:>4}|{ln}"
                   for i, ln in enumerate(src.splitlines(keepends=True), 1))


def _project(tmp_path: Path, name: str = "app.py") -> Path:
    p = tmp_path / name
    p.write_text(SRC, encoding="utf-8")
    return p


def _register(cm: ContextManager, result) -> None:
    """模拟 agent 侧把一次工具结果注入上下文（agent.py 的真实调用形态）。"""
    cm.add_tool_result("read_file", True, result.output, meta=result.metadata)


# ═══════════════════════════════════════════════════════════════════
# T1 — 内容仍在 recent window → 保持轻量 cache hit，不重复注入正文
# ═══════════════════════════════════════════════════════════════════


class TestStillVisibleKeepsLightweightHit:
    def _cm(self) -> ContextManager:
        # 窗口放大：确保本用例只考察"可见"分支，不掺入淘汰
        return ContextManager({"context.max_chars": 10 ** 7,
                               "context.recent_window": 50,
                               "context.tool_result_window": 50})

    def test_repeat_read_stays_lightweight(self, tmp_path: Path):
        _project(tmp_path)
        cm = self._cm()
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, cm.is_read_visible)

        r1 = tool.execute(ctx, {"path": "app.py"})
        assert r1.success, r1.error
        assert _body() in r1.output, "首次读取必须给出正文"
        read_key = r1.metadata["read_key"]
        assert read_key, "首次读取必须携带 read_key"

        _register(cm, r1)
        assert cm.is_read_visible(read_key) is True, \
            "前置条件：注入后该内容应在可见窗口内"

        r2 = tool.execute(ctx, {"path": "app.py"})
        assert r2.success, r2.error
        # 轻量命中：标记与计数语义保留
        assert r2.metadata.get("cached") is True
        assert "[ReadCache]" in r2.output
        # 核心：**不重复注入完整正文**（保住 ReadCache 的 token 优势）
        assert _body() not in r2.output, \
            f"内容仍可见时不得重复注入正文: {r2.output}"
        assert "return 2" not in r2.output, f"不得回正文: {r2.output}"
        # read_key 稳定，后续仍能据此判断可见性
        assert r2.metadata["read_key"] == read_key

    def test_lightweight_hit_survives_repeated_calls(self, tmp_path: Path):
        """连续多次重复读取，每次都应是轻量命中（不因次数累积而漏正文）。"""
        _project(tmp_path)
        cm = self._cm()
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, cm.is_read_visible)

        r1 = tool.execute(ctx, {"path": "app.py"})
        _register(cm, r1)
        for _ in range(3):
            r = tool.execute(ctx, {"path": "app.py"})
            assert r.metadata.get("cached") is True
            assert _body() not in r.output, f"重复读取不得注入正文: {r.output}"


# ═══════════════════════════════════════════════════════════════════
# T2 — 被 compact 淘汰 → 必须重新交回源码 + 行号（P1-1 核心）
# ═══════════════════════════════════════════════════════════════════


class TestEvictedContentIsReinjected:
    def _tiny_cm(self) -> ContextManager:
        return ContextManager({"context.max_chars": 150,
                               "context.recent_window": 1,
                               "context.tool_result_window": 1})

    def test_body_reinjected_after_eviction(self, tmp_path: Path):
        _project(tmp_path)
        cm = self._tiny_cm()
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, cm.is_read_visible)

        # 第 1 次读取 → 注入上下文
        r1 = tool.execute(ctx, {"path": "app.py"})
        read_key = r1.metadata["read_key"]
        _register(cm, r1)
        assert cm.is_read_visible(read_key) is True, \
            "前置条件：刚注入时应可见"

        # 用无关结果 + 足量后续消息把它挤出窗口，并真正触发压缩
        cm.add_tool_result("shell_exec", True, "unrelated " + "y" * 80)
        for i in range(6):
            cm.add_message("user", f"第 {i} 轮说明：" + "x" * 200)
        cm.compact()

        # 前置条件：淘汰确实发生，且用**生产判据**确认已不可见
        assert cm.compact_count >= 1, "前置条件：必须真的发生过上下文压缩"
        assert cm.is_read_visible(read_key) is False, \
            "前置条件：首次读取的原文必须已被淘汰出可见窗口"

        # 修复点：文件没变，但内容已不可见 → 必须重新交回正文 + 行号
        r2 = tool.execute(ctx, {"path": "app.py"})
        assert r2.success, r2.error
        assert r2.metadata.get("cached") is True, \
            "文件本身没变，仍应标记 cached=True（既有语义）"
        assert _body() in r2.output, \
            f"淘汰后必须重新注入完整正文: {r2.output}"
        assert "   6|" in r2.output, f"必须保留行号信息: {r2.output}"
        assert r2.metadata["read_key"] == read_key

    def test_eviction_check_uses_message_window_not_tool_entries(self, tmp_path: Path):
        """可见性判据只认模型可见的消息窗口，不认 tool-result 平行结构。

        tool_result_window 远大于 recent_window 时，entry 仍在但消息早已滑出 ——
        此时必须判"不可见"，否则会退回 P1-1。
        """
        _project(tmp_path)
        cm = ContextManager({"context.max_chars": 10 ** 7,
                             "context.recent_window": 1,
                             "context.tool_result_window": 50})
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, cm.is_read_visible)

        r1 = tool.execute(ctx, {"path": "app.py"})
        read_key = r1.metadata["read_key"]
        _register(cm, r1)
        for i in range(5):
            cm.add_message("user", f"后续第 {i} 轮：" + "z" * 40)

        # entry 仍在（tool_result_window=50），但注入消息已被 recent_window=1 挤掉
        _entries = json.dumps(cm._recent_win.tool_results, ensure_ascii=False)
        assert read_key in _entries, "前置条件：tool-result entry 仍在"
        assert cm.is_read_visible(read_key) is False, \
            "模型看不到的消息窗口必须判为不可见"

        r2 = tool.execute(ctx, {"path": "app.py"})
        assert _body() in r2.output, f"消息已不可见 → 必须回正文: {r2.output}"


# ═══════════════════════════════════════════════════════════════════
# T3 — 文件变化 → 走新鲜读取，不得 stale、不得轻量命中
# ═══════════════════════════════════════════════════════════════════


class TestChangedFileStillReadsFresh:
    def test_modified_file_returns_new_content(self, tmp_path: Path):
        p = _project(tmp_path)
        cm = ContextManager({"context.max_chars": 10 ** 7,
                             "context.recent_window": 50,
                             "context.tool_result_window": 50})
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, cm.is_read_visible)

        r1 = tool.execute(ctx, {"path": "app.py"})
        _register(cm, r1)
        # 前置条件：此时确实"可见"，若判据写错就会走轻量分支
        assert cm.is_read_visible(r1.metadata["read_key"]) is True

        p.write_text(SRC.replace("return 2", "return 99"), encoding="utf-8")
        r2 = tool.execute(ctx, {"path": "app.py"})

        assert r2.success, r2.error
        assert "return 99" in r2.output, f"必须拿到新内容: {r2.output}"
        assert "return 2\n" not in r2.output.replace("return 99", ""), \
            f"不得返回 stale content: {r2.output}"
        assert "[ReadCache]" not in r2.output, "内容已变不得报缓存命中"
        assert r2.metadata.get("cached") is False
        assert r2.metadata["read_key"] != r1.metadata["read_key"], \
            "内容变化后 read_key 必须更新"


# ═══════════════════════════════════════════════════════════════════
# T4 — 探针缺失/失败 → fail-closed，必须返回正文
# ═══════════════════════════════════════════════════════════════════


class TestFailClosedWithoutProbe:
    def test_missing_probe_returns_body(self, tmp_path: Path):
        """不注入探针（read_visible=None）时不得认为内容仍可见。"""
        _project(tmp_path)
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, None)

        tool.execute(ctx, {"path": "app.py"})
        r2 = tool.execute(ctx, {"path": "app.py"})
        assert ctx.read_visible is None
        assert _body() in r2.output, f"探针缺失必须 fail-closed 回正文: {r2.output}"
        assert "return 2" in r2.output

    def test_probe_returning_false_returns_body(self, tmp_path: Path):
        _project(tmp_path)
        tool = ReadFileTool()
        ctx = _ctx(tmp_path, lambda _key: False)

        tool.execute(ctx, {"path": "app.py"})
        r2 = tool.execute(ctx, {"path": "app.py"})
        assert _body() in r2.output, f"探针判不可见必须回正文: {r2.output}"
        assert "[ReadCache]" not in r2.output

    def test_probe_raising_is_treated_as_invisible(self, tmp_path: Path):
        """探针抛错不得冒泡成工具错误，也不得被当成"可见"。"""
        _project(tmp_path)

        def _boom(_key):
            raise RuntimeError("probe exploded")

        tool = ReadFileTool()
        ctx = _ctx(tmp_path, _boom)

        r1 = tool.execute(ctx, {"path": "app.py"})
        assert r1.success, r1.error
        r2 = tool.execute(ctx, {"path": "app.py"})
        assert r2.success, f"探针抛错不得让读取失败: {r2.error}"
        assert _body() in r2.output, f"探针抛错必须按不可见处理: {r2.output}"

    def test_probe_receives_the_read_key(self, tmp_path: Path):
        """探针收到的必须是本次读取的 read_key（接线正确性）。"""
        _project(tmp_path)
        seen: list[object] = []

        def _probe(key):
            seen.append(key)
            return False

        tool = ReadFileTool()
        ctx = _ctx(tmp_path, _probe)
        r1 = tool.execute(ctx, {"path": "app.py"})
        r2 = tool.execute(ctx, {"path": "app.py"})

        assert seen == [r1.metadata["read_key"]], \
            f"探针应收到首次读取的 read_key: {seen}"
        assert r2.metadata["read_key"] == r1.metadata["read_key"]


# ═══════════════════════════════════════════════════════════════════
# T5 — ContextManager 既有调用方兼容（不传 meta）
# ═══════════════════════════════════════════════════════════════════


class TestContextManagerCompatibility:
    def test_add_tool_result_without_meta_unchanged(self):
        """不传 meta 的既有调用（位置参数形态）必须完全照旧工作。"""
        cm = ContextManager()
        cm.add_tool_result("read_file", True, "file content")
        cm.add_tool_result("shell_exec", False, "", error="boom", duration_ms=3,
                           truncate=64)

        msgs = cm.get_context()
        assert any("file content" in (m.get("content") or "") for m in msgs), \
            "结果仍应注入上下文"
        assert cm.is_read_visible("any-key") is False, \
            "没有注册过 read_key 时必须返回 False（fail-closed）"
        assert cm.is_read_visible(None) is False
        assert cm.is_read_visible("") is False

    def test_meta_cannot_override_structural_fields(self):
        """meta 不得覆盖 name/success/output/duration_ms/tool 这些结构性字段。"""
        cm = ContextManager()
        cm.add_tool_result("read_file", True, "real output",
                           meta={"name": "hacked", "output": "hacked",
                                 "tool": "hacked", "read_key": "k1"})

        entry = cm._recent_win.tool_results[-1]
        assert entry["name"] == "read_file"
        assert entry["success"] is True
        assert entry["output"].startswith("real output")
        # meta 自身携带的信息必须保留下来
        assert entry["read_key"] == "k1"
        # 注入消息同样带上 read_key，供 is_read_visible 查询
        assert cm.is_read_visible("k1") is True

    def test_unregistered_key_is_invisible(self):
        """注册过其它 key 时，未注册的 key 仍必须返回 False。"""
        cm = ContextManager()
        cm.add_tool_result("read_file", True, "x", meta={"read_key": "k1"})
        assert cm.is_read_visible("k1") is True
        assert cm.is_read_visible("k2") is False
