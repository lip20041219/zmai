import os
import pathlib
import subprocess
import json
import sys
import argparse
from datetime import datetime

# 保证提示信息在 Windows (GBK) 控制台不因非 ASCII 字符崩溃
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 默认配置项
WORKSPACE_DIR = "./workspace"
PROMPT_FILE = "agent_prompt.txt"
LOG_FILE = "process_result.json"

# 无头模式权限策略
# Claude Code 当前版本已弃用 `--permission-mode bypassPermissions`（实测对
# 无头子进程不生效，导致 Edit/Write/Bash 被拒）。改用：
#   --allowedTools <白名单>   自动批准白名单内工具、无需人工确认，
#                             且把 Edit/Write 限定在 workspace 内（禁止逃逸）。
# 需要完全无边界时可用 --skip-permissions 触发 --dangerously-skip-permissions。
DANGEROUS_SKIP_PERMISSIONS_FLAG = "--dangerously-skip-permissions"


def build_claude_command(prompt: str, workspace: str, skip_permissions: bool) -> list[str]:
    """构造 Claude Code 无头调用命令。

    默认策略：allowedTools 白名单（自动批准 + workspace 边界）。
      - Read                    允许读取
      - Edit(<ws>/**)           允许编辑 workspace 内文件
      - Write(<ws>/**)          允许写入 workspace 内文件
      - Bash(*)                 允许执行命令（含 pytest 测试）
    Edit/Write 路径限定在 workspace 内 → 自动拒绝外部路径 / ../ 逃逸，
    满足"禁止修改 workspace 外文件"与无人值守双重要求。

    skip_permissions=True 时改用 --dangerously-skip-permissions（跳过全部权限，
    无边界，仅当调用方显式要求时使用）。
    """
    abs_ws = str(pathlib.Path(workspace).resolve()).replace("\\", "/")
    allowed = f"Read,Edit({abs_ws}/**),Write({abs_ws}/**),Bash(*)"
    if skip_permissions:
        return [
            "claude", "-p", prompt,
            "--output-format", "json",
            DANGEROUS_SKIP_PERMISSIONS_FLAG,
        ]
    return [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--allowedTools", allowed,
    ]


def parse_args(argv=None) -> argparse.Namespace:
    """解析命令行参数：--workspace、--prompt、--skip-permissions。"""
    parser = argparse.ArgumentParser(description="ZMAI SWE Agent 无头调用入口")
    parser.add_argument(
        "--workspace",
        default=WORKSPACE_DIR,
        help="Agent 工作目录（实际 cwd），默认 %(default)s",
    )
    parser.add_argument(
        "--prompt",
        default=PROMPT_FILE,
        help="提示词文件路径，默认 %(default)s",
    )
    parser.add_argument(
        "--skip-permissions",
        action="store_true",
        help="触发 --dangerously-skip-permissions（跳过全部权限，无边界）",
    )
    return parser.parse_args(argv)


def _entry_argv(argv: list[str] | None) -> list[str]:
    """解析 argv 来源。

    真实入口（python run_agent.py ...）时 __name__ == "__main__"，用 sys.argv；
    被测试/库调用（import run_agent）时用空列表，避免被调用方（如 pytest）
    的 sys.argv 污染 argparse 解析。
    """
    if argv is not None:
        return argv
    return sys.argv[1:] if __name__ == "__main__" else []


def resolve_workspace(argv: list[str] | None = None) -> str:
    """按优先级解析工作目录：CLI --workspace > env ZMAI_WORKSPACE > 默认。

    回归场景：WORKSPACE_DIR 硬编码导致外部测试项目无法作为 Agent 操作目录。
    """
    argv = _entry_argv(argv)
    has_cli = any(a == "--workspace" or a.startswith("--workspace=") for a in argv)
    if has_cli:
        return parse_args(argv).workspace
    return os.environ.get("ZMAI_WORKSPACE") or WORKSPACE_DIR


def run_claude_agent(prompt, workspace=None, skip_permissions=False):
    print("🚀 正在无头模式下启动 Claude Code Agent...")

    workspace = workspace or WORKSPACE_DIR
    command = build_claude_command(prompt, workspace, skip_permissions)

    try:
        # 在 workspace 下执行，限制其活动范围（cwd 为用户传入的工作目录）
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",   # Windows 下显式 UTF-8，避免 GBK 解码崩溃
            errors="replace",   # 非 ASCII 字节用替换符兜底，永不抛异常
            timeout=3600 # 设定 1 小时超时，防止 Agent 死循环
        )

        # Claude Code 输出的 stdout 已经是 JSON 格式（由 --output-format json 保证）
        try:
            agent_output = json.loads(result.stdout)
        except json.JSONDecodeError:
            agent_output = {"raw_output": result.stdout}

        status = "success" if result.returncode == 0 else "failed"

        return {
            "status": status,
            "exit_code": result.returncode,
            "timestamp": datetime.now().isoformat(),
            "agent_response": agent_output,
            "stderr": result.stderr
        }

    except subprocess.TimeoutExpired as e:
        return {"status": "timeout", "timestamp": datetime.now().isoformat(), "error": str(e)}
    except Exception as e:
        return {"status": "error", "timestamp": datetime.now().isoformat(), "error": str(e)}

def main(argv: list[str] | None = None):
    args = parse_args(_entry_argv(argv))

    workspace = resolve_workspace(argv)
    prompt_file = args.prompt
    skip_permissions = args.skip_permissions

    # 启动时打印，方便确认 Agent 当前操作目录与提示词文件（绝对路径）
    print("Workspace:")
    print(os.path.abspath(workspace))
    print("Prompt:")
    print(os.path.abspath(prompt_file))

    # 错误检查：工作区必须已存在，否则报错退出
    if not os.path.exists(workspace):
        print("Workspace not found")
        sys.exit(1)

    # 错误检查：提示词文件必须已存在，否则报错退出
    if not os.path.exists(prompt_file):
        print("Prompt file not found")
        sys.exit(1)

    # 读取提示词
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()

    # 执行（cwd 使用用户传入的 workspace）
    result_data = run_claude_agent(prompt, workspace, skip_permissions)

    # 将包含运行状态和 Claude 返回结果的完整日志写入 JSON
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 任务结束，完整报告已写入 {LOG_FILE}")

    # 下一步处理：解析 Claude 的 JSON 输出并执行后续逻辑
    if result_data["status"] == "success":
        print("➡️ 正在解析 Agent 输出进行下一步...")
        # do_next_step(result_data)

if __name__ == "__main__":
    main()
