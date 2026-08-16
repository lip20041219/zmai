"""P3 — Demo: ZMAI 自主修复真实 Flask 网站。

对应真实场景 `zmai "修复这个网站所有bug，使测试全部通过"`。

Demo 站点（examples/real_project_demos/zmai_demo_site）:
  1. 首页 404（缺路由）
  2. /api/users 字段名错误（name vs user）
  3. /button 方法错误（POST vs GET）

本测试把 demo 站点复制到临时目录，驱动 SWEAgent 用真实工具端到端修复，
并断言"修复后网站工作正常"（首页 200、API 字段正确、按钮可访问）。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

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

DEMO_DIR = Path(__file__).resolve().parents[1] / "examples" / "real_project_demos" / "zmai_demo_site" / "project"  # noqa: E501


# ═══════════════════════════════════════════════════════════════════
# 修复序列（与 demo 站点一一对应，均为无行号依赖的 regex 替换）
# ═══════════════════════════════════════════════════════════════════

PYTEST = ToolCall(id="pytest", name="shell_exec",
                  params={"command": "python -m pytest -q"})


def _edit(tag: str, params: dict[str, str]) -> ToolCall:
    p: dict[str, Any] = {"path": "app.py", "mode": "regex_replace"}
    p.update(params)
    return ToolCall(id=f"edit_{tag}", name="edit", params=p)


def _script() -> list[list[ToolCall] | None]:
    fixes = [
        ("home", {"old_text": r"def home\(\):", "new_text": "@app.route('/')\ndef home():"}),
        ("users", {"old_text": '"name": "alice"', "new_text": '"user": "alice"'}),
        ("button", {"old_text": r'methods=\["POST"\]', "new_text": 'methods=["GET"]'}),
    ]
    script = []
    for i, (tag, fix) in enumerate(fixes):
        script.append([PYTEST, _edit(tag, fix)])
    script.append([PYTEST])  # 全绿验证轮
    script.append(None)
    return script


class _DemoBackend(Backend):
    name = "demo_fix"

    def __init__(self, script: list[list[ToolCall] | None]):
        self._script = script
        self._i = 0
        self.calls_seen: list[str] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None
        if self._i < len(self._script):
            calls = self._script[self._i]
        self._i += 1
        if calls:
            self.calls_seen.extend(c.name for c in calls)
        return BackendResponse(content="", tool_calls=calls,
                               usage=TokenUsage(1, 1),
                               stop_reason="tool_use" if calls else "end_turn")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


async def _run(project: Path, backend: Backend, max_steps: int = 8) -> tuple[AgentContext, Any]:
    agent = SWEAgent("demo_site")
    ctx = AgentContext(
        agent_id="demo_site",
        task=(f"项目在 {project} 目录下。\n修复这个网站所有bug，使测试全部通过。"
              f"先运行 pytest 看失败，逐个修复 app.py，直到全部通过。"),
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(project), "timeout": 30},
        metadata={},
    )
    await agent.initialize(ctx)
    action = None
    for _ in range(max_steps):
        action = await agent.step(ctx)
        if action.type in ("complete", "fail"):
            break
    return ctx, action


@pytest.mark.skipif(not DEMO_DIR.exists(), reason="demo 站点未随仓库存在")
class TestZmAiDemoSite:
    def test_fix_website_makes_it_work(self, tmp_path: Path):
        """修复后网站工作正常：首页 200、API 字段正确、按钮可访问。"""
        project = tmp_path / "demo"
        shutil.copytree(DEMO_DIR, project)

        # 初始红
        r0 = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                            cwd=str(project), capture_output=True, text=True,
                            timeout=60, encoding="utf-8", errors="replace")
        assert r0.returncode != 0 and "3 failed" in r0.stdout, "demo 初始应 3 failed"

        backend = _DemoBackend(_script())
        ctx, action = asyncio.run(_run(project, backend))

        # Agent 完成
        assert action.type == "complete", f"应 complete: {action.output}"

        # 3 处修复真实落盘
        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert "@app.route('/')" in app_text, "首页路由未修复"
        assert '"user": "alice"' in app_text, "API 字段未修复"
        assert 'methods=["GET"]' in app_text, "Button 方法未修复"

        # 真实 pytest 全绿
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=str(project), capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        assert r.returncode == 0 and "3 passed" in r.stdout, f"修复后应 3 passed: {r.stdout}"

        # 未提前停止：3 次 edit + ≥4 次 pytest
        assert backend.calls_seen.count("edit") == 3
        assert backend.calls_seen.count("shell_exec") >= 4

    @staticmethod
    def _route_statuses(project: Path) -> dict[str, str]:
        """在子进程里读取 app.py 路由状态（避免 import 缓存污染）。"""
        code = (
            "from app import app\n"
            "c = app.test_client()\n"
            "print(c.get('/').status_code, c.get('/button').status_code, "
            "c.get('/api/users').get_json(), sep='|')\n"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(project),
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return {"rc": str(r.returncode), "out": r.stdout.strip()}

    def test_demo_starts_broken_then_works(self, tmp_path: Path):
        """对比前后：修复前 404/405/字段错误，修复后全部正常（演示效果断言）。"""
        project = tmp_path / "demo2"
        shutil.copytree(DEMO_DIR, project)

        # 修复前：首页 404、按钮 405、API 缺 user 字段
        before = self._route_statuses(project)
        assert before["out"].startswith("404"), f"修复前首页应 404: {before['out']}"
        assert "405" in before["out"], f"修复前按钮应 405: {before['out']}"
        assert "user" not in before["out"], f"修复前 API 应缺 user 字段: {before['out']}"

        # 修复后：全部正常
        backend = _DemoBackend(_script())
        asyncio.run(_run(project, backend))

        after = self._route_statuses(project)
        assert after["out"].startswith("200"), f"修复后首页应 200: {after['out']}"
        assert "'user': 'alice'" in after["out"], f"修复后 API 字段应正确: {after['out']}"
