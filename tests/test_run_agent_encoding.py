"""回归测试 — run_agent.py 的 subprocess UTF-8 编码。

原始 bug：Windows (GBK) 下 subprocess 输出含非 ASCII（中文/emoji）时，
Claude Code 的 stdout 解码抛 `UnicodeDecodeError: 'gbk' codec can't decode byte`。

修复后：subprocess.run 显式指定 `encoding="utf-8", errors="replace"`，
stdout/stderr 即使含中文/emoji 也不会崩溃。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# 从项目根按路径加载 run_agent.py（它是脚本，不在包内）
_spec = importlib.util.spec_from_file_location(
    "zmai_run_agent", str(_ROOT / "run_agent.py")
)
_run_agent = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_run_agent)


class _FakeResult:
    """模拟含中文与 emoji 的 subprocess 输出（UTF-8 解码后的 str）。"""

    def __init__(self, stdout: str, stderr: str, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_subprocess_unicode_stdout_does_not_crash(monkeypatch):
    """含中文+emoji 的 stdout 不抛 UnicodeDecodeError，可正常 json 解析。"""
    # 中文 + emoji 的典型 Agent 输出
    gbk_unsafe_stdout = '{"summary": "任务完成 ✅，所有测试通过 🎉"}'
    gbk_unsafe_stderr = "警告 ⚠️：请检查配置"

    def fake_run(*args, **kwargs):
        # 验证 subprocess.run 确实被要求以 UTF-8 解码（编码修复的核心断言）
        assert kwargs.get("text") is True
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "replace"
        return _FakeResult(
            stdout=gbk_unsafe_stdout,
            stderr=gbk_unsafe_stderr,
            returncode=0,
        )

    monkeypatch.setattr(_run_agent.subprocess, "run", fake_run)

    result = _run_agent.run_claude_agent("请修复 bugs")

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    # agent_response 是合法 JSON，含中文与 emoji
    assert result["agent_response"]["summary"] == "任务完成 ✅，所有测试通过 🎉"
    # stderr 原样保留（含 emoji），无解码崩溃
    assert result["stderr"] == "警告 ⚠️：请检查配置"


def test_process_result_json_is_generated(tmp_path, monkeypatch):
    """process_result.json 可正常生成，且内容含中文/emoji、无 UnicodeDecodeError。"""
    gbk_unsafe_stdout = '{"msg": "✅ 全部通过"}'

    def fake_run(*args, **kwargs):
        return _FakeResult(stdout=gbk_unsafe_stdout, stderr="", returncode=0)

    monkeypatch.setattr(_run_agent.subprocess, "run", fake_run)

    # 临时工作区，避免污染真实 agent_workspace / process_result.json
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(_run_agent, "WORKSPACE_DIR", str(ws))
    out_file = tmp_path / "process_result.json"
    monkeypatch.setattr(_run_agent, "LOG_FILE", str(out_file))

    prompt_file = tmp_path / "agent_prompt.txt"
    prompt_file.write_text("修复 bugs", encoding="utf-8")
    monkeypatch.setattr(_run_agent, "PROMPT_FILE", str(prompt_file))

    _run_agent.main()

    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["status"] == "success"
    assert data["agent_response"]["msg"] == "✅ 全部通过"
