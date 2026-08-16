"""SWE Integration Demo — Agent 自主修复一个含 4 个 bug 的 Flask 网站。

这是一个端到端集成测试，覆盖用户真实 demo 场景：

  初始状态（4 个 bug）：
    1. 首页 404        —— index 缺少 @app.route('/')
    2. CSS 缺失        —— /static/style.css 无路由
    3. API 字段错误    —— /api/user 返回 "name" 而非 "username"
    4. JS 编码错误     —— /js 响应含控制字符 0x01（UTF-8 严格解码失败）

  用户输入："修复这个网站所有bug，使测试全部通过"

要求 Agent 自动（验证核心能力）：
  1. 阅读测试失败            —— 先跑 pytest，看 4 个失败
  2. 定位源码                —— read_file 读 app.py
  3. 修改代码                —— edit 逐个修复
  4. 运行 pytest              —— 每修一个重跑
  5. 根据失败继续修复         —— 循环直至 4 个测试全绿
  6. 全部通过后停止           —— 只在全绿时 complete，不提前停

使用真实文件系统 + 真实工具（真实读/写/跑 pytest/edit）。mock backend
仅决定"下一步做什么"，因此验证的是 **Agent 执行闭环**而非某个 LLM。

关键断言：
  - 必须完成 4 个独立 edit（证明"根据失败继续修复"，而非一次写完）
  - 必须运行 >=5 次 pytest（证明"每步验证"，且未在部分失败时提前停止）
  - 仅当 4 个 bug 全部修复且 pytest 全绿时 action 才为 complete
  - [Repair Plan] 在首次失败注入
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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

# ═══════════════════════════════════════════════════════════════════
# 4-bug Flask 项目 fixture
# ═══════════════════════════════════════════════════════════════════

APP_BUGGY = (
    "from flask import Flask, jsonify\n"
    "\n"
    "app = Flask(__name__)\n"
    "\n"
    "\n"
    "def index():\n"
    "    return \"<link rel='stylesheet' href='/static/style.css'><h1>Home</h1>\"\n"
    "\n"
    "\n"
    "@app.route(\"/api/user\")\n"
    "def api_user():\n"
    "    return jsonify({\"name\": \"alice\"})\n"
    "\n"
    "\n"
    "@app.route(\"/js\")\n"
    "def js():\n"
    "    return \"var greeting = 'caf\\x01e';\"\n"
)

TEST_APP = (
    "import pytest\n"
    "from app import app\n"
    "\n"
    "@pytest.fixture\n"
    "def client():\n"
    "    app.config[\"TESTING\"] = True\n"
    "    with app.test_client() as c:\n"
    "        yield c\n"
    "\n"
    "def test_home_returns_200(client):\n"
    "    assert client.get(\"/\").status_code == 200\n"
    "\n"
    "def test_css_is_served(client):\n"
    "    rv = client.get(\"/static/style.css\")\n"
    "    assert rv.status_code == 200\n"
    "    assert b\"color\" in rv.data\n"
    "\n"
    "def test_api_returns_username(client):\n"
    "    data = client.get(\"/api/user\").get_json()\n"
    "    assert data[\"username\"] == \"alice\"\n"
    "\n"
    "def test_js_has_valid_encoding(client):\n"
    "    rv = client.get(\"/js\")\n"
    "    body = rv.data.decode(\"utf-8\", errors=\"strict\")\n"
    "    assert \"\\x01\" not in body\n"
)


def _write_flask_site(project_dir: Path) -> None:
    """写入含 4 个 bug 的 Flask 网站（测试初始全部失败）。"""
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "app.py").write_text(APP_BUGGY, encoding="utf-8")
    (project_dir / "test_app.py").write_text(TEST_APP, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# Scripted backend —— 模拟一个"能力足够"的 Agent，逐轮迭代修复
# ═══════════════════════════════════════════════════════════════════

PYTEST = ToolCall(id="pytest", name="shell_exec",
                  params={"command": "python -m pytest -q"})


def _read_app(tag: str) -> ToolCall:
    return ToolCall(id=f"read_{tag}", name="read_file", params={"path": "app.py"})


# 4 个修复：每个都是独立、无行号依赖的 regex 替换
_FIXES: list[tuple[str, dict[str, str]]] = [
    # Bug 1: 首页 404 —— index 缺路由（注意转义括号，否则 () 被当作空分组）
    ("bug1", {"old_text": r"def index\(\):", "new_text": "@app.route('/')\ndef index():"}),
    # Bug 3: API 字段错误 —— name -> username
    ("bug3", {"old_text": '"name": "alice"', "new_text": '"username": "alice"'}),
    # Bug 4: JS 编码错误 —— 整行替换（[^']* 吸收控制字符 0x01，无需转义）
    ("bug4", {"old_text": r'''return "var greeting = '[^']*';"''',
              "new_text": r'''return "var greeting = 'cafe';"'''}),
    # Bug 2: CSS 缺失 —— append 一个路由
    ("bug2", {"mode": "append",
              "new_text": '\n\n@app.route("/static/style.css")\ndef style():\n'
                          '    return "body { color: #333; }", 200, {"Content-Type": "text/css"}\n'}),  # noqa: E501
]


def _edit(tag: str, fix: dict[str, str]) -> ToolCall:
    params: dict[str, Any] = {"path": "app.py", "mode": fix.get("mode", "regex_replace")}
    params.update({k: v for k, v in fix.items() if k != "mode"})
    return ToolCall(id=f"edit_{tag}", name="edit", params=params)


def _build_script() -> list[list[ToolCall] | None]:
    """逐轮迭代：每轮 跑测试→读源码→修一个 bug；最后一轮只跑测试验证全绿。"""
    script: list[list[ToolCall] | None] = []
    for i, (tag, fix) in enumerate(_FIXES):
        script.append([PYTEST, _read_app(f"{tag}_{i}"), _edit(tag, fix)])
    script.append([PYTEST])  # 全绿验证轮
    script.append(None)
    return script


class _ScriptedBackend(Backend):
    """按预写脚本依次返回工具调用；无脚本时返回 end_turn。工具真实执行。"""

    name = "scripted_flask_demo"

    def __init__(self, script: list[list[ToolCall] | None]):
        self._script = script
        self._idx = 0
        self.calls_seen: list[str] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None
        if self._idx < len(self._script):
            calls = self._script[self._idx]
        self._idx += 1
        if calls:
            for c in calls:
                self.calls_seen.append(c.name)
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


async def _run_agent(project_dir: Path, backend: Backend, max_steps: int = 9) -> tuple[AgentContext, Any]:  # noqa: E501
    agent = SWEAgent("flask_demo")
    ctx = AgentContext(
        agent_id="flask_demo",
        task=(
            f"项目在 {project_dir} 目录下。\n"
            f"任务: 修复这个网站所有bug，使测试全部通过。\n"
            f"先运行 pytest 看哪些测试失败，定位源码，逐个修复 app.py，"
            f"每修一个就重跑 pytest，直到全部通过。"
        ),
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(project_dir), "timeout": 30},
        metadata={},
    )
    await agent.initialize(ctx)

    action = None
    for _ in range(max_steps):
        action = await agent.step(ctx)
        if action.type in ("complete", "fail"):
            break
    return ctx, action


def _messages_text(ctx: AgentContext) -> str:
    parts = []
    for m in ctx.metadata.get("messages", []):
        if isinstance(m, dict):
            parts.append(m.get("content", "") or "")
        else:
            parts.append(getattr(m, "content", "") or "")
    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════════════


class TestFlaskMultiBugAutonomousFix:
    def test_agent_fixes_all_bugs_and_stops_when_green(self, tmp_path: Path):
        """Agent 应迭代修复全部 4 个 bug，且只在测试全绿时 complete。"""
        project = tmp_path / "flask_site"
        _write_flask_site(project)

        # 先确认 fixture 初始确实红（4 failed）
        r0 = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                            cwd=str(project), capture_output=True, text=True,
                            timeout=60, encoding="utf-8", errors="replace")
        assert r0.returncode != 0, "fixture 初始测试应失败"
        assert "4 failed" in r0.stdout, f"初始应有 4 个失败: {r0.stdout}"

        backend = _ScriptedBackend(_build_script())
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 1) Agent 成功完成
        assert action.type == "complete", \
            f"应 complete, 实际 {action.type}: {action.output}"

        # 2) 4 个 bug 全部被真实修复（磁盘上 app.py 已含全部修复标记）
        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert "@app.route('/')" in app_text, "bug1 首页路由未修复"
        assert '"username": "alice"' in app_text, "bug3 API 字段未修复"
        assert "cafe" in app_text, "bug4 JS 编码未修复"
        assert "/static/style.css" in app_text, "bug2 CSS 路由未修复"

        # 3) 真实 pytest 最终全绿
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=str(project), capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"修复后 pytest 应全绿: {r.stdout}{r.stderr}"
        assert "passed" in r.stdout, f"应显示 passed: {r.stdout}"

        # 4) Agent 逐轮迭代而非一次写完：4 次独立 edit + 5 次 pytest
        calls = backend.calls_seen
        assert calls.count("edit") == 4, f"应执行 4 次独立 edit（逐 bug 修复）: {calls}"
        assert calls.count("shell_exec") >= 5, \
            f"应运行 >=5 次 pytest（每修一个验证一次）: {calls}"

        # 5) 未在部分失败时提前停止：编辑与测试轮交替出现
        #    edit 必须都发生在最后一次全绿 pytest 之前
        last_pytest = len(calls) - 1 - calls[::-1].index("shell_exec")
        for idx, c in enumerate(calls):
            if c == "edit":
                assert idx < last_pytest, "编辑必须发生在最终验证 pytest 之前"

        # 6) 首次失败注入 Repair Plan（进入修复闭环）
        joined = _messages_text(ctx)
        assert "[Repair Plan]" in joined, "首次测试失败应注入 Repair Plan"

    def test_missing_route_only_is_not_enough(self, tmp_path: Path):
        """负向：只修 1 个 bug 不应触发 complete —— Agent 必须以全绿为停止条件。"""
        project = tmp_path / "flask_site2"
        _write_flask_site(project)

        # 脚本只修 bug1，然后结束（无全绿 pytest）
        script = [
            [PYTEST, _read_app("only"), _edit("bug1", _FIXES[0][1])],
            [PYTEST],
            None,
        ]
        backend = _ScriptedBackend(script)
        ctx, action = asyncio.run(_run_agent(project, backend))

        # 由于 3 个 bug 仍未修复、测试未全绿 —— Agent 必须 NOT claim 完成，
        # 而是被 completion_guard 强制进入重测（continue）。
        assert action.type != "complete", \
            f"只修 1 个 bug 且测试未全绿，不应误判 completed: {action.output}"
        assert action.type == "continue", f"应强制重测: {action.type}"
        # 但 app.py 必须只修了 bug1，其它 3 个 bug 仍在
        app_text = (project / "app.py").read_text(encoding="utf-8")
        assert '"username": "alice"' not in app_text, "bug3 不应被修复"
