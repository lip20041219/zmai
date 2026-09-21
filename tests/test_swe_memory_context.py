"""Memory 注入闭环：写入 namespace 与读取 namespace 必须一致。

故障（修复前，实测复现）：
  `SWEAgent.step()` 里的"## Memory Context"注入用

      wm.search("")            # WorkingMemory.search 的默认 namespace="default"

  读取，而工具结果写入用的是 `namespace="tools"`。两端 namespace 不一致 →
  `MemoryManager.restore()` 明明恢复了条目（实测返回 1），`## Memory Context`
  却永远为空 —— 跨 run 记忆整个失效。

  `tests/test_memory.py` 覆盖的是 **default** namespace 的 persist/restore，
  因此这条"写入 tools / 读取 default"的分歧从未被任何测试触及。

本文件覆盖该分歧，并锁住修复后的注入闭环。基础能力（store/read/persist/restore
本身）已由 `tests/test_memory.py` 覆盖，此处不重复。

全部用例走真实 `SWEAgent.step()`，通过拦截 `BackendRequest.system_prompt` 观察
模型实际收到的内容（而不是断言内部字段）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

from zmai.agent import AgentContext
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.memory.manager import MemoryManager
from zmai.swe.agent import _MEMORY_NS_TOOLS, SWEAgent
from zmai.tool import ToolCall, ToolRegistry

APP = "def value():\n    return 1\n"
TEST = "from app import value\n\n\ndef test_value():\n    assert value() == 1\n"

_PYTEST = ToolCall(id="p", name="shell_exec",
                   params={"command": "python -m pytest -q"})


class _PromptCapturingBackend(Backend):
    """记录每次请求的 system_prompt，脚本耗尽后回纯文本。"""

    name = "mem_probe"

    def __init__(self, script):
        self._script = script
        self._i = 0
        self.prompts: list[str] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.prompts.append(request.system_prompt or "")
        calls = self._script[self._i] if self._i < len(self._script) else None
        self._i += 1
        return BackendResponse(
            content="done" if calls is None else "",
            tool_calls=calls,
            usage=TokenUsage(1, 1),
            stop_reason="end_turn" if calls is None else "tool_use",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


def _write_project(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(APP, encoding="utf-8")
    (tmp_path / "test_app.py").write_text(TEST, encoding="utf-8")


async def _run_once(project: Path, mm: MemoryManager, agent_id: str,
                    script) -> _PromptCapturingBackend:
    backend = _PromptCapturingBackend(script)
    ctx = AgentContext(
        agent_id=agent_id, task="修复 app.py",
        backend=backend, tools=ToolRegistry(),
        config={"project_path": str(project), "timeout": 60},
        memory=mm, metadata={},
    )
    agent = SWEAgent(agent_id)
    await agent.initialize(ctx)
    for _ in range(3):
        action = await agent.step(ctx)
        if action.type in ("complete", "fail"):
            break
    return backend


# ═══════════════════════════════════════════════════════════════════
# A — 写入 → persist → restore → 下一 run 的 system prompt 真的带 memory
# ═══════════════════════════════════════════════════════════════════


class TestMemoryReachesSystemPrompt:
    def test_restored_memory_appears_in_memory_context(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        _write_project(project)
        memroot = tmp_path / "mem"
        agent_id = "mem_reach_1"

        # RUN 1：真实执行一次 pytest → 工具结果写入 memory
        mm1 = MemoryManager(long_term_root=memroot)
        asyncio.run(_run_once(project, mm1, agent_id, [[_PYTEST]]))
        assert mm1.working(agent_id).search("", namespace=_MEMORY_NS_TOOLS), \
            "前置条件：工具结果必须已写入 tools namespace"
        mm1.persist(agent_id)
        assert (memroot / agent_id).exists(), "前置条件：memory 必须已落盘"

        # RUN 2：全新 Manager（等价于新进程）→ restore → 注入
        mm2 = MemoryManager(long_term_root=memroot)
        restored = mm2.restore(agent_id)
        assert restored >= 1, f"restore 应恢复条目，实际 {restored}"
        assert mm2.working(agent_id).search("", namespace=_MEMORY_NS_TOOLS), \
            "restore 后必须能在 tools namespace 里检索到条目"

        backend = asyncio.run(_run_once(project, mm2, agent_id, [[_PYTEST]]))
        prompt = backend.prompts[0]

        assert "## Memory Context" in prompt, (
            "修复前 search 用默认 namespace，restore 的数据取不出来 "
            "→ ## Memory Context 永远为空"
        )
        assert f"tool:{_PYTEST.name}" in prompt, \
            "上一次 run 的工具结果必须出现在 Memory Context 中"


# ═══════════════════════════════════════════════════════════════════
# B — namespace 不串读
# ═══════════════════════════════════════════════════════════════════


class TestNamespaceIsolationAcrossPersistence:
    def test_persist_restore_keeps_namespaces_separate(self, tmp_path: Path):
        mm = MemoryManager(long_term_root=tmp_path)
        wm = mm.working("ns_agent")
        wm.store("k_tools", "from-tools", namespace=_MEMORY_NS_TOOLS)
        wm.store("k_other", "from-other", namespace="other")
        mm.persist("ns_agent")

        mm2 = MemoryManager(long_term_root=tmp_path)
        mm2.restore("ns_agent")
        wm2 = mm2.working("ns_agent")

        tools_keys = {e.key for e in wm2.search("", namespace=_MEMORY_NS_TOOLS)}
        other_keys = {e.key for e in wm2.search("", namespace="other")}
        default_keys = {e.key for e in wm2.search("", namespace="default")}

        assert tools_keys == {"k_tools"}, f"tools 命名空间串读: {tools_keys}"
        assert other_keys == {"k_other"}, f"other 命名空间串读: {other_keys}"
        assert default_keys == set(), (
            f"默认 namespace 不应拿到任何条目（写入用的是 tools）: {default_keys}"
        )


# ═══════════════════════════════════════════════════════════════════
# C — 空 memory 不影响正常 run
# ═══════════════════════════════════════════════════════════════════


class TestEmptyMemoryDoesNotAffectRun:
    def test_no_memory_context_when_empty(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        _write_project(project)
        mm = MemoryManager(long_term_root=tmp_path / "mem")

        backend = asyncio.run(_run_once(project, mm, "mem_empty", [[_PYTEST]]))
        assert backend.prompts, "backend 必须被调用"
        for p in backend.prompts:
            assert "## Memory Context" not in p, \
                "空 memory 不得注入 Memory Context 噪音"

    def test_agent_without_memory_still_runs(self, tmp_path: Path):
        """context.memory 为 None 时不得报错（既有调用方路径）。"""
        project = tmp_path / "proj"
        project.mkdir()
        _write_project(project)
        backend = _PromptCapturingBackend([[_PYTEST], None])
        ctx = AgentContext(
            agent_id="mem_none", task="修复 app.py",
            backend=backend, tools=ToolRegistry(),
            config={"project_path": str(project), "timeout": 60},
            metadata={},
        )

        async def _go():
            agent = SWEAgent("mem_none")
            await agent.initialize(ctx)
            return await agent.step(ctx)

        action = asyncio.run(_go())
        assert action.type in ("continue", "complete", "fail")
        assert backend.prompts and "## Memory Context" not in backend.prompts[0]
