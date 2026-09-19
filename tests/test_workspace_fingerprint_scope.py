"""P1：无显式工作区根时，工作区指纹不得扫描整个 CWD。

`_workspace_root()` 在 `project_path`/`workspace` 都未配置时兜底为 `Path(".")`。
那只是一个 cwd，不代表项目范围：实测 ZMAI 仓库根 30,672 文件、单次遍历 13.7 秒，
而每次工具调用都要走一遍（test_loop_guard.py 因此从数秒涨到数分钟）。

修复：**无显式 root 时**改用 git 索引快路径（`git status --porcelain -uall`），
非 git 目录再退回目录树 —— 正确性优先，不给 `_ws_changed` 造假值。
**有显式 root 时指纹语义完全不变。**

全部用例使用小型 temp workspace，不扫描真实 ZMAI 仓库。
"""

from __future__ import annotations

import asyncio
import subprocess
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
from zmai.swe.agent import (
    SWEAgent,
    _explicit_workspace_root,
    _workspace_fingerprint,
)
from zmai.tool import ToolCall, ToolRegistry


def _git_cli(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=root, capture_output=True, check=False)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")
    _git_cli(tmp_path, "init", "-q")
    _git_cli(tmp_path, "add", "-A")
    _git_cli(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _fp(root: Path) -> dict:
    """快路径指纹（无显式 root 时 Agent 实际使用的那个）。"""
    return _workspace_fingerprint(root, prefer_git=True)


# ── 1：有显式 root → 目录树指纹语义不变 ─────────────────────────
def test_explicit_root_keeps_tree_semantics(tmp_path):
    """显式 root 走目录树：列出每个文件，不因 git 快路径改变语义。"""
    root = _repo(tmp_path)
    fp = _workspace_fingerprint(root)          # prefer_git 默认 False

    assert set(fp) == {"a.py", "b.py"}, fp
    # 每个值仍是 (mtime_ns, size)（Windows 上 git 可能已把 LF 换成 CRLF，
    # 故不断言精确字节数，只断言是真实 stat 值）
    mtime, size = fp["a.py"]
    assert mtime > 0 and size > 0


def test_explicit_root_is_used_by_agent(tmp_path):
    """配置了 project_path 时，Agent 解析出的 root 就是它（而非 CWD）。"""
    ctx = AgentContext(agent_id="x", task="t",
                       config={"project_path": str(tmp_path)}, metadata={})
    assert _explicit_workspace_root(ctx) == tmp_path


# ── 2：无显式 root → 不再递归扫描 ───────────────────────────────
def test_no_root_uses_git_fast_path(tmp_path, monkeypatch):
    """干净 git 仓库下快路径返回空集 —— 证明没有遍历目录树。"""
    root = _repo(tmp_path)
    monkeypatch.chdir(root)

    ctx = AgentContext(agent_id="x", task="t", config={}, metadata={})
    assert _explicit_workspace_root(ctx) is None, "本用例前提：无显式 root"

    fp = _fp(Path("."))
    assert fp == {}, f"干净仓库应为空集（遍历会列出 a.py/b.py）: {fp}"
    # 目录树指纹会列出文件 —— 两者形成对照
    assert set(_workspace_fingerprint(Path("."))) == {"a.py", "b.py"}


# ── 3：git 快路径覆盖 修改 / 新增 / 删除 / rename / 回退 ────────
def test_git_fingerprint_tracks_all_change_kinds(tmp_path):
    root = _repo(tmp_path)
    clean = _fp(root)
    assert clean == {}

    (root / "a.py").write_text("a = 2\n", encoding="utf-8")           # 修改
    modified = _fp(root)
    assert modified != clean
    assert any("a.py" in k for k in modified)

    (root / "new.py").write_text("n = 1\n", encoding="utf-8")         # 新增（未跟踪）
    added = _fp(root)
    assert added != modified
    assert any("new.py" in k for k in added)

    _git_cli(root, "add", "-A")
    staged = _fp(root)
    assert staged != added                                            # 状态行变化可见

    (root / "a.py").unlink()                                          # 删除（仍受跟踪）
    deleted = _fp(root)
    assert deleted != staged
    assert any("a.py" in k for k in deleted)

    # 回退：git checkout 恢复被删文件 → 指纹必须再次变化（不能"永远不变"）
    _git_cli(root, "checkout", "--", "a.py")
    restored = _fp(root)
    assert restored != deleted, "真实回退必须被识别"


def test_git_fingerprint_detects_revert_of_modification(tmp_path):
    """修改 → git checkout 回退：指纹从非空回到空集（回退也是真实变化）。"""
    root = _repo(tmp_path)
    assert _fp(root) == {}

    (root / "a.py").write_text("a = 999\n", encoding="utf-8")
    dirty = _fp(root)
    assert dirty != {}

    _git_cli(root, "checkout", "--", "a.py")
    reverted = _fp(root)
    assert reverted == {}, "回退到 HEAD 后应回到干净状态"
    assert reverted != dirty, "回退本身必须被识别为工作区变化"


def test_untracked_file_inside_new_dir_is_detected(tmp_path):
    """新建目录内的文件变化必须可见（`-uall`，否则目录会被折叠）。"""
    root = _repo(tmp_path)
    (root / "pkg").mkdir()
    (root / "pkg" / "m.py").write_text("m = 1\n", encoding="utf-8")
    first = _fp(root)
    assert any("m.py" in k for k in first)

    (root / "pkg" / "m.py").write_text("m = 2\n", encoding="utf-8")
    assert _fp(root) != first, "未跟踪目录内文件的变化不得被折叠掉"


# ── 4：非 git 工作区不崩溃 ──────────────────────────────────────
def test_non_git_workspace_falls_back_without_crash(tmp_path):
    """非 git 目录：快路径返回 None → 退回目录树，仍能给出指纹。"""
    (tmp_path / "c.py").write_text("c = 1\n", encoding="utf-8")
    fp = _fp(tmp_path)                        # prefer_git=True 但无 git
    assert set(fp) == {"c.py"}, f"应退回目录树指纹: {fp}"


def test_missing_directory_does_not_crash(tmp_path):
    assert _fp(tmp_path / "does-not-exist") == {}


# ── 5：Agent / LoopGuard 修改检测不回归 ─────────────────────────
class _OnceBackend(Backend):
    name = "fp_scope"

    def __init__(self, call: ToolCall):
        self._call = call
        self._done = False

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None if self._done else [self._call]
        self._done = True
        return BackendResponse(content="", tool_calls=calls, usage=TokenUsage(1, 1),
                               stop_reason="tool_use" if calls else "end_turn")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def test_agent_without_explicit_root_still_detects_modification(tmp_path, monkeypatch):
    """无显式 root 时，shell 真实改文件仍必须被识别为 modification。"""
    root = _repo(tmp_path)
    monkeypatch.chdir(root)

    call = ToolCall(id="sh", name="shell_exec",
                    params={"command": "python -c \"open('a.py','a').write('# x')\""})
    ctx = AgentContext(
        agent_id="fp_scope", task="改一下 a.py",
        backend=_OnceBackend(call), tools=ToolRegistry(),
        config={"timeout": 30, "_quiet": True, "loop_guard.threshold": 50},
        metadata={},
    )
    agent = SWEAgent("fp_scope")
    asyncio.run(agent.initialize(ctx))
    asyncio.run(agent.step(ctx))

    assert ctx.metadata.get("ever_modified") is True, (
        "无显式 root 下 shell 的真实改动必须仍被检测到"
    )
    guard = ctx.metadata["loop_guard"]
    assert guard.get_status()["last_modification_step"] >= 1, (
        "LoopGuard 的 modification detection 不得因快路径而回归"
    )


def test_agent_without_explicit_root_ignores_readonly(tmp_path, monkeypatch):
    """反向保证：只读命令不得因快路径被误判为 modification。"""
    root = _repo(tmp_path)
    monkeypatch.chdir(root)

    call = ToolCall(id="sh", name="shell_exec",
                    params={"command": "python -m pytest -q"})
    ctx = AgentContext(
        agent_id="fp_scope2", task="跑测试",
        backend=_OnceBackend(call), tools=ToolRegistry(),
        config={"timeout": 60, "_quiet": True, "loop_guard.threshold": 50},
        metadata={},
    )
    agent = SWEAgent("fp_scope2")
    asyncio.run(agent.initialize(ctx))
    asyncio.run(agent.step(ctx))

    assert ctx.metadata.get("ever_modified") is not True
