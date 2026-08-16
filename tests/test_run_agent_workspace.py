"""回归测试 — run_agent.py 工作目录解析优先级。

原始 bug：WORKSPACE_DIR 硬编码为 ./agent_workspace，
外部 SWE 测试项目（如 D:\\desk\\zmai_autostop_test_demo）无法作为 Agent 操作目录。

修复后：工作目录可按优先级 CLI --workspace > env ZMAI_WORKSPACE > 默认(./workspace) 解析。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "zmai_run_agent", str(_ROOT / "run_agent.py")
)
_run_agent = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_run_agent)

DEFAULT = "./workspace"


def test_default_uses_agent_workspace(monkeypatch):
    """无 --workspace、无 ZMAI_WORKSPACE 时使用默认 ./workspace。"""
    monkeypatch.delenv("ZMAI_WORKSPACE", raising=False)
    assert _run_agent.resolve_workspace([]) == DEFAULT


def test_env_var_used_when_no_cli(monkeypatch):
    """设置 ZMAI_WORKSPACE 后，无 --workspace 时使用环境变量目录。"""
    monkeypatch.setenv("ZMAI_WORKSPACE", r"D:\desk\zmai_autostop_test_demo")
    assert _run_agent.resolve_workspace([]) == r"D:\desk\zmai_autostop_test_demo"


def test_cli_overrides_env(monkeypatch):
    """--workspace 参数覆盖环境变量。"""
    monkeypatch.setenv("ZMAI_WORKSPACE", r"D:\desk\from_env")
    got = _run_agent.resolve_workspace(["--workspace", r"D:\desk\zmai_autostop_test_demo"])
    assert got == r"D:\desk\zmai_autostop_test_demo"


def test_cli_equals_form_overrides_env(monkeypatch):
    """--workspace=path 形式同样覆盖环境变量。"""
    monkeypatch.setenv("ZMAI_WORKSPACE", r"D:\desk\from_env")
    got = _run_agent.resolve_workspace(["--workspace=D:\\desk\\zmai_demo"])
    assert got == r"D:\desk\zmai_demo"


def test_main_prints_workspace_and_prompt(tmp_path, monkeypatch, capsys):
    """main() 启动时打印 Workspace 与 Prompt（示例格式）。"""
    ws = tmp_path / "demo_ws"
    ws.mkdir()
    prompt_file = tmp_path / "agent_prompt.txt"
    prompt_file.write_text("fix", encoding="utf-8")

    monkeypatch.setattr(_run_agent, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(_run_agent, "PROMPT_FILE", str(prompt_file))
    monkeypatch.setattr(_run_agent, "LOG_FILE", str(tmp_path / "process_result.json"))

    # 不真正调用 claude
    def fake_run(*a, **k):
        return type("R", (), {"stdout": "{}", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(_run_agent.subprocess, "run", fake_run)

    _run_agent.main()
    out = capsys.readouterr().out
    assert "Workspace:" in out
    assert str(ws.resolve()) in out
    assert "Prompt:" in out
