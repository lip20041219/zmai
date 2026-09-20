"""P1-A1/A2 — auto-verify 的**证据根目录**与"测试调用错误"判定。

A1（file check 根目录）
    文件检查曾以 ``context.workspace``（ZMAI 临时工作区，只有 input/output/temp）为根
    解析 ``modified_files``，而源码在 ``project_path``。相对路径全部解析到不存在的
    路径 → ``文件存在: <file>`` **必然 FAIL**，把"已修改、只是还没重测"判成验证失败。
    SWE-bench smoke 实测：psf__requests-3362 里 ``replaced 1 matches in requests/utils.py``
    编辑成功，同一文件仍被判 ``文件存在: requests/utils.py FAIL``。

A2（测试调用错误 ≠ 测试失败）
    pytest 的**调用错误**（exit 4 / ``ERROR: file or directory not found`` /
    ``no tests ran`` / ``collected 0 items``）根本没跑到被测代码，既不构成失败证据
    也不构成通过证据。旧实现把它记成 ``Command failed`` 进入 completion blocking。
    真正的 assertion failure（有 ``N failed`` 计数 / FAILED 段）必须继续阻断。

用例走真实文件系统 + 真实 pytest（用 sys.executable），pytest 输出取自真实运行，
避免用手写字符串把正则喂成"自己想要的答案"。``workspace`` 与 ``project_path``
刻意分离，复现 eval harness 的真实布局。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import zmai.swe.agent as agent_mod
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
from zmai.swe.tools import ShellTool
from zmai.swe.verifier import auto_generate_checks
from zmai.tool import ToolCall, ToolContext, ToolRegistry

APP_BUGGY = '''\
from flask import Flask

app = Flask(__name__)


def index():
    return "Hello"
'''

TEST_APP = '''\
from app import app


def test_home_returns_200():
    app.config["TESTING"] = True
    with app.test_client() as c:
        assert c.get("/").status_code == 200
'''


def _write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(APP_BUGGY, encoding="utf-8")
    (root / "test_app.py").write_text(TEST_APP, encoding="utf-8")


class _ScriptedBackend(Backend):
    """按预写脚本返回工具调用；无脚本时 end_turn（触发 auto-verify）。"""

    name = "scripted_evidence"

    def __init__(self, script: list[list[ToolCall] | None]):
        self._script = script
        self._idx = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = self._script[self._idx] if self._idx < len(self._script) else None
        self._idx += 1
        return BackendResponse(
            content="",
            tool_calls=calls,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use" if calls else "end_turn",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _run(tmp_path: Path, script, max_steps: int = 10, setup=None):
    """project_path 与 workspace 分离 —— 与 eval harness（Runtime.run）一致。

    setup(ctx) 在 initialize 之后、第一步之前调用，用于摆放夹具状态。
    """
    project = tmp_path / "proj"
    workspace = tmp_path / "ws"
    _write_project(project)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "output").mkdir(exist_ok=True)

    ctx = AgentContext(
        agent_id="av_evidence",
        task="修复 app.py 使 test_app.py 通过",
        backend=_ScriptedBackend(script),
        tools=ToolRegistry(),
        config={"project_path": str(project), "timeout": 60},
        workspace=workspace,
        metadata={},
    )
    agent = SWEAgent("av_evidence")
    asyncio.run(agent.initialize(ctx))
    if setup:
        setup(ctx)
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action)
        if action.type in ("complete", "fail"):
            break
    return ctx, actions


# ═══════════════════════════════════════════════════════════════════
# A1 — 文件检查必须以 project_path 为根
# ═══════════════════════════════════════════════════════════════════


def test_a1_file_check_resolves_against_project_path(tmp_path, monkeypatch):
    """真实修改 app.py 后，auto-verify 的 `文件存在: app.py` 必须 PASS。

    修复前：_auto_verify 传 context.workspace（tmp_path/ws，不含源码）当根，
    (ws / "app.py") 不存在 → check FAIL → 已修好的改动被判验证失败。

    modified_files 由 SummaryMemory 在**上下文压缩**时填充（P1-B 的提取点），
    短 run 不会压缩 —— 这里用真实 edit 结果消息驱动同一条提取路径摆放夹具。
    """
    captured: dict = {}
    orig = agent_mod.auto_generate_checks

    def _spy(modified_files, tool_results, workspace=None):
        r = orig(modified_files, tool_results, workspace)
        captured["workspace"] = workspace
        captured["modified_files"] = list(modified_files)
        captured["checks"] = [(c.name, c.passed) for c in r.checks]
        return r

    monkeypatch.setattr(agent_mod, "auto_generate_checks", _spy)

    def _seed_modified(ctx_):
        ctx_.metadata["cm"]._memory.compress(
            [{"role": "user", "content": "[工具 edit 结果]\nOK: appended app.py",
              "metadata": {"tool": "edit"}}], [])

    script: list[list[ToolCall] | None] = [
        [ToolCall(id="e", name="edit",
                  params={"path": "app.py", "mode": "append",
                          "new_text": "\n@app.route('/')\ndef _route():\n    return 'x'\n"})],
        None,  # end_turn → auto-verify
    ]
    _run(tmp_path, script, setup=_seed_modified)

    assert "workspace" in captured, "auto-verify 未被触发，夹具失效"
    assert Path(captured["workspace"]) == tmp_path / "proj", \
        f"文件检查的根目录应为 project_path, 实际 {captured['workspace']}"
    assert "app.py" in captured["modified_files"], \
        f"app.py 应被记为已修改: {captured['modified_files']}"
    assert ("文件存在: app.py", True) in captured["checks"], \
        f"`文件存在: app.py` 必须在 project_path 下 PASS, 实际 checks={captured['checks']}"


# ═══════════════════════════════════════════════════════════════════
# A2 — 测试调用错误不构成失败证据；真实失败仍必须阻断
# ═══════════════════════════════════════════════════════════════════


def _real_pytest(cmd: list[str], cwd: Path) -> dict:
    """真跑一次 pytest，按 ShellTool 的形态构造 tool_result entry。

    形态取 tools.py ShellTool.execute：失败时 error=f"exit {code}: {output}"，
    成功时 output=output、success=True。
    """
    r = subprocess.run([sys.executable, "-m", "pytest", *cmd], cwd=str(cwd),
                       capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        return {"name": "shell_exec", "success": False, "output": "",
                "error": f"exit {r.returncode}: {out}"}
    return {"name": "shell_exec", "success": True, "output": out}


def test_a2_pytest_invocation_error_is_not_failure_evidence(tmp_path):
    """exit 4 / file or directory not found（真实 pytest 输出）不得产生失败 check。"""
    project = tmp_path / "proj"
    _write_project(project)

    entry = _real_pytest(["tests/does_not_exist.py", "-q"], project)
    assert entry["success"] is False, "夹具失效：该命令本应非零退出"
    assert "file or directory not found" in entry["error"].lower(), \
        f"夹具失效：未复现 pytest 用法错误输出: {entry['error'][:200]}"

    result = auto_generate_checks([], [entry], project)
    failed = [c for c in result.checks if not c.passed]
    assert not failed, \
        f"pytest 调用错误不得成为失败证据（它没跑到被测代码）: {[c.name for c in failed]}"


def test_a2_zero_collection_is_not_failure_evidence(tmp_path):
    """`--collect-only` 之外，路径存在但一个测试都收集不到（no tests ran）同样不算失败。"""
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    (project / "test_empty.py").write_text("import pytest\n", encoding="utf-8")

    entry = _real_pytest(["test_empty.py", "-q"], project)
    assert entry["success"] is False, f"夹具失效：本应 exit 5, 实际 {entry}"

    result = auto_generate_checks([], [entry], project)
    failed = [c for c in result.checks if not c.passed]
    assert not failed, \
        f"零收集运行不得成为失败证据: {[c.name for c in failed]}"


def test_a2_real_assertion_failure_still_blocks(tmp_path):
    """真正的 assertion failure 必须继续产生失败证据（不能把所有非零退出都放过）。"""
    project = tmp_path / "proj"
    _write_project(project)

    entry = _real_pytest(["test_app.py", "-q"], project)
    assert entry["success"] is False

    result = auto_generate_checks([], [entry], project)
    failed = [c for c in result.checks if not c.passed]
    assert failed, "真实测试失败必须继续阻断"


def test_a2_non_test_command_failure_still_blocks(tmp_path):
    """非 pytest 命令的失败不受 A2 影响（不做 exit 4/5 的裸退出码放行）。"""
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)

    entry = {"name": "shell_exec", "success": False, "output": "",
             "error": "exit 4: make: *** [Makefile:12: build] Error 4"}
    result = auto_generate_checks([], [entry], project)
    failed = [c for c in result.checks if not c.passed]
    assert failed, "非测试命令的失败必须继续阻断"


# ═══════════════════════════════════════════════════════════════════
# A3 — shell_exec 描述与实际 cwd 语义一致
# ═══════════════════════════════════════════════════════════════════


def test_a3_shell_description_matches_actual_cwd(tmp_path):
    """描述不得声称在 workspace 下执行；实际 cwd 必须是 project_path。"""
    project = tmp_path / "proj"
    workspace = tmp_path / "ws"
    _write_project(project)
    workspace.mkdir(parents=True, exist_ok=True)

    desc = ShellTool.description.lower()
    assert "workspace" not in desc, f"描述不应声称 workspace: {ShellTool.description}"
    assert "project" in desc, f"描述应指明 project 目录: {ShellTool.description}"

    res = ShellTool().execute(
        ToolContext(agent_id="t", workspace_path=workspace, project_path=project),
        {"command": f'"{sys.executable}" -c "import os;print(os.getcwd())"'},
    )
    assert res.success, res.error
    assert Path(res.output.strip().splitlines()[-1]) == project, \
        f"shell_exec 实际 cwd 应为 project_path: {res.output!r}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
