"""CLI entry — zmai <task> or zmai (REPL)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from zmai import __version__ as zmai_version
from zmai.cli.context import build_context
from zmai.cli.detector import _find_root as find_project_root
from zmai.cli.formatters import Theme, print_error, print_json, print_info, print_success
from zmai.cli.auth_cmd import (
    first_run_wizard as _first_run_wizard,
    offer_auth_fix as _offer_auth_fix,
    print_auth_debug as _print_auth_debug,
    run_auth as _run_auth,
    should_show_wizard as _should_show_wizard,
)
from zmai.cli.config_cmd import run_config as _run_config
from zmai.cli.eval_cmd import run_benchmark as _run_benchmark
from zmai.cli.eval_cmd import run_eval as _run_eval
from zmai.cli.github_cmd import run_github as _run_github
from zmai.cli.plugin_cmd import run_plugin as _run_plugin
from zmai.config import Config
from zmai.config.sources import CLISource, EnvSource, FileSource
from zmai.runtime import Runtime
from zmai.swe.planner import generate_plan

SESSION_DIR = Path.home() / ".zmai" / "sessions"
HISTORY_FILE = Path.home() / ".zmai" / "history"
AUTHORS = "xijingliu"


# ── 辅助 ──────────────────────────────────────────────────

def _sanitize_error(msg: str) -> str:
    """Sanitize error messages, removing HTTP details and raw API errors."""
    import re as _re
    # Match "xxx API HTTP 401: {...}" or "[BACKEND_ERROR] xxx API HTTP..."
    if _re.search(r"API HTTP \d{3}", msg):
        return "Backend call failed. Please check your API Key configuration."
    # Match JSON containing request_id
    if '"request_id"' in msg or "'request_id'" in msg:
        return "Backend call failed. Please check your API Key configuration."
    # Match raw API error JSON
    if '"error"' in msg and ('"type"' in msg or '"message"' in msg):
        return "Backend call failed. Please try again later."
    # Match BACKEND_ERROR prefix
    if "BACKEND_ERROR" in msg:
        parts = msg.split("BACKEND_ERROR] ", 1)
        if len(parts) > 1:
            return parts[1].strip()
    return msg


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _save_session(task: str) -> None:
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        (SESSION_DIR / "latest.json").write_text(
            json.dumps({"task": task, "time": _now_iso()}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_latest_session() -> str | None:
    try:
        p = SESSION_DIR / "latest.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get("task")
    except Exception:
        return None


def _ensure_utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _cleanup_old_workspaces(workspace_root: Path, max_age_days: int = 7) -> int:
    """Clean up completed Agent workspaces older than max_age_days."""
    from zmai.workspace import Workspace
    try:
        ws = Workspace(root=str(workspace_root))
        cleaned = 0
        now = time.time()
        for aid in agents:
            state = ws.get_state(aid)
            if not state:
                continue
            if state.status not in ("completed", "failed"):
                continue
            try:
                updated = state.updated_at
                if updated:
                    from datetime import datetime, timezone
                    age = now - datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
                    if age > max_age_days * 86400:
                        ws.remove(aid)
                        cleaned += 1
            except Exception:
                continue
        return cleaned
    except Exception:
        return 0


# ── 参数解析 ──────────────────────────────────────────────

def _print_help() -> None:
    """Print help message."""
    print("ZMAI - Model-Agnostic Agent Runtime", end="")
    print("\n\nUsage:")
    print("  zmai                        Enter interactive REPL mode")
    print("  zmai <task description>     Run a single task")
    print("  zmai --json <task>          Run task, output JSON")
    print("  zmai --no-color <task>      Run task without color")
    print("  zmai --backend <name>       Run task with specific backend")
    print("  zmai --version              Show version")
    print("  zmai --help                 Show this help")
    print("\nSubcommands:")
    print("  zmai plan <task>            PLAN ONLY: analyze and generate execution plan")
    print("  zmai --plan <task>          AUTO PLAN: generate plan then execute")
    print("  zmai config                 Manage configuration")
    print("  zmai auth                   Show authentication status")
    print("  zmai auth setup             Set up authentication interactively")
    print("  zmai auth status            Show credentials status with source details")
    print("  zmai auth update <backend>  Update API key for a backend")
    print("  zmai auth test <backend>    Test API key validity via HTTP")
    print("  zmai auth list              List saved credentials")
    print("  zmai auth switch <backend>  Switch default backend")
    print("  zmai auth remove <backend>  Remove saved credentials")
    print("  zmai plugin <list|install|remove>  Manage plugin backends")
    print("  zmai issue <url>                 Fetch and fix a GitHub issue")
    print("  zmai pr <title>                  Create a pull request")
    print("\nIn REPL mode:")
    print("  exit / quit / Ctrl+D        Exit REPL")
    print("  Ctrl+C                      Pause current task")
    print("  /help                       Show REPL commands")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zmai",
        description="ZMAI - Model-Agnostic Agent Runtime",
        usage="zmai [options] <task...>",
        add_help=False,
    )
    p.add_argument("--version", action="version", version=f"ZMAI v{zmai_version}")
    p.add_argument("--json", action="store_true", help="json output")
    p.add_argument("--no-color", action="store_true", help="disable color")
    p.add_argument("--backend", help="backend name (see zmai auth list for available)")
    p.add_argument("--plan", action="store_true", help="auto plan mode: generate plan before execution")
    p.add_argument("task", nargs="*", help="task description")
    p.add_argument("--help", action="store_true", help="show help")
    return p


def _get_theme(args: argparse.Namespace, config: Config) -> Theme:
    if getattr(args, "no_color", False):
        return Theme.plain()
    tn = config.get("cli.theme", "dark")
    if tn == "light":
        return Theme.light()
    if tn == "plain":
        return Theme.plain()
    return Theme.dark()


# ── 任务执行 ──────────────────────────────────────────────

def _repl_run(task: str, runtime: Runtime, agent_id: str, theme: Theme,
               project_path: str = "") -> dict[str, Any]:
    """REPL 模式下的任务执行。返回结果，不 exit。"""
    _save_session(task)

    def on_progress(typ: str, msg: str) -> None:
        try:
            if typ == "tool":
                sys.stderr.write(f"\n  > {msg}")
            elif typ == "result":
                ok = msg.startswith("OK:")
                sys.stderr.write(f"\n    {'ok' if ok else 'fail'}")
            sys.stderr.flush()
        except Exception:
            pass

    try:
        result = asyncio.run(runtime.run(
            agent_id=agent_id,
            task=task,
            backend=None,
            on_progress=on_progress,
            config={"project_path": project_path},
        ))
    except KeyboardInterrupt:
        print("\n  ⏸ task paused")
        return {"status": "cancelled"}

    st = result.get("status", "?")
    output = result.get("output", "")
    if st == "completed":
        sys.stderr.write("\n")
        if output:
            print_success(f"✓ {output[:2000]}", theme)
        else:
            print_success("done", theme)
    else:
        err = _sanitize_error(result.get("error", "") or f"agent {st}")
        if err:
            print_error(err, theme)
        # Offer interactive fix for API Key related errors
        if err and ("API_KEY" in err or "auth update" in err or "not configured" in err):
            _offer_auth_fix(theme)
    return result


def _run_plan_only(task_args: list[str]) -> None:
    """`zmai plan <task>` — PLAN ONLY mode: generate plan and output, no execution."""
    if not task_args:
        print("用法: zmai plan <task description>", file=sys.stderr)
        sys.exit(1)

    task = " ".join(task_args)
    theme = Theme.dark()

    # 初始化 Runtime 以获取 Backend
    terminal_cwd = os.getcwd()
    root = find_project_root()
    from pathlib import Path as _Path
    global_cfg = str(_Path.home() / ".zmai" / "config.json")
    config = Config() if not root else Config(
        sources=[
            FileSource(str(root / "zmai.json")),
            FileSource(global_cfg),
            EnvSource(),
            CLISource(),
        ]
    )
    config.set("project_path", terminal_cwd)
    runtime = Runtime(config=config)

    # 获取默认 Backend
    try:
        backend_inst = runtime._gateway.get()
    except Exception as e:
        print_error(f"Cannot get Backend: {e}", theme)
        sys.exit(1)

    print()
    print(f"  {theme.dim('ZMAI Plan — analyzing task...')}")
    print()

    try:
        plan = generate_plan(task, backend_inst, config.export())
    except Exception as e:
        print_error(f"计划生成失败: {e}", theme)
        sys.exit(1)

    # 输出计划
    sep = "━" * 50
    print(f"  {sep}")
    print(f"  {theme.highlight('Plan')}")
    print(f"  {sep}")
    print(f"")
    print(f"  {theme.bold('目标:')} {plan.goal}")
    print(f"  {theme.dim(f'复杂度: {plan.estimated_complexity}  |  步骤: {len(plan.steps)} 步')}")
    print(f"")
    print(f"  {theme.bold('执行步骤:')}")
    for s in plan.steps:
        tool_str = f"  [{theme.dim(s.tool)}]" if s.tool else ""
        print(f"    {s.id}. {s.action}{tool_str}")
        if s.expected_outcome:
            print(f"       {theme.dim('→ ' + s.expected_outcome[:120])}")
    print(f"")
    if plan.risks:
        print(f"  {theme.bold('风险:')}")
        for r in plan.risks:
            print(f"    {theme.warning('⚠')} {r}")
        print(f"")
    print(f"  {sep}")
    plan_hint = 'Use zmai --plan "<task>" to auto-plan and execute'
    print(f"  {theme.dim(plan_hint)}")
    print(f"  {sep}")
    print()

    # 保存计划到 session 供后续使用
    try:
        session_dir = Path.home() / ".zmai" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        import json
        (session_dir / "latest_plan.json").write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass




def _oneshot_run(task: str, runtime: Runtime, config: Config, args: argparse.Namespace) -> None:
    """单次执行模式。执行完成后 exit。"""
    theme = _get_theme(args, config)
    _save_session(task)

    def on_progress(typ: str, msg: str) -> None:
        if getattr(args, "json", False):
            return
        try:
            if typ == "tool":
                sys.stderr.write(f"\n  > {msg}")
            elif typ == "result":
                ok = msg.startswith("OK:")
                sys.stderr.write(f"\n    {'ok' if ok else 'fail'}")
            sys.stderr.flush()
        except Exception:
            pass

    project_path = config.get("project_path", "")
    try:
        result = asyncio.run(runtime.run(
            agent_id=f"agent_{os.getpid()}",
            task=task,
            backend=args.backend if getattr(args, "backend", None) else None,
            on_progress=on_progress,
            config={
                "project_path": project_path,
                "auto_plan": getattr(args, "plan", False),
            },
        ))
    except KeyboardInterrupt:
        print("\n  cancelled")
        sys.exit(130)

    if getattr(args, "json", False):
        print_json(result)
    else:
        st = result.get("status", "?")
        output = result.get("output", "")
        if st == "completed":
            sys.stderr.write("\n")
            if output:
                print_success(f"✓ {output[:2000]}", theme)
            else:
                print_success("done", theme)
        else:
            err = _sanitize_error(result.get("error", "") or f"agent {st}")
            if err:
                print_error(err, theme)
                # Offer interactive fix for API Key related errors
                if "API_KEY" in err or "auth update" in err or "not configured" in err:
                    _offer_auth_fix(theme)
            if output and not err:
                sys.stderr.write(f"\n{output}\n")
    sys.exit(0 if result.get("status") == "completed" else 3)


# ── REPL 交互模式 ──────────────────────────────────────────

def _setup_readline() -> None:
    """Set up readline history and Tab completion."""
    try:
        import readline
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        if HISTORY_FILE.exists():
            readline.read_history_file(str(HISTORY_FILE))
        readline.set_history_length(2000)
        import atexit
        atexit.register(lambda: readline.write_history_file(str(HISTORY_FILE)))
    except ImportError:
        pass  # Windows: no readline, fallback to plain input()


def _cmd_interactive(runtime: Runtime, config: Config, args: argparse.Namespace) -> None:
    """REPL interactive mode."""
    theme = _get_theme(args, config)
    project_path = config.get("project_path", "")
    # Use unique agent_id per task to avoid lifecycle state machine rejecting completed agents
    _task_counter = 0

    # Set up readline
    _setup_readline()

    # Last session prompt
    session_task = _load_latest_session()
    if session_task:
        print_info(f"last task: {session_task[:60]}", theme)

    aid = ""  # Recent task ID for /status

    # Built-in command handling
    def _handle_builtin(cmd: str) -> bool:
        c = cmd.lower()
        if c == "/help":
            print()
            print("  ─── commands ─────────────────────")
            print("  /help            show this help")
            print("  /status          show session status")
            print("  exit / quit / ^D exit REPL")
            print("  ─────────────────────────────────\n")
            return True
        if c == "/status":
            print()
            print(f"  agent:   {aid}")
            try:
                info = runtime.get_info()
                print(f"  uptime:  {info.uptime_seconds:.0f}s")
                print(f"  agents:  {info.running_agents} running / {info.total_agents} total")
            except Exception:
                pass
            print()
            return True
        return False

    theme_symbol = theme.highlight("zmai> ")
    try:
        while True:
            try:
                task = input(theme_symbol).strip()
            except EOFError:
                print()
                break
            if not task:
                continue
            if task.lower() in ("exit", "quit"):
                break

            # Built-in commands
            if task.startswith("/"):
                if not _handle_builtin(task):
                    print(f"  unknown command: {task}   try /help")
                continue

            # Execute task — unique agent_id each time to avoid lifecycle reuse conflicts
            _task_counter += 1
            aid = f"repl_{os.getpid()}_{_task_counter}"
            _repl_run(task, runtime, aid, theme, project_path)

    except KeyboardInterrupt:
        print("\n  bye")
    finally:
        _save_session("(interrupted)")




def _run_doctor(json_output: bool = False) -> None:
    """Check installation integrity + subsystem status."""
    from zmai.cli.doctor import Doctor

    doctor = Doctor(theme=Theme.dark(), json_output=json_output)
    results = doctor.run()
    if not all(r.status for r in results):
        sys.exit(1)


# ── 主入口 ──────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    _ensure_utf8()
    argv = argv or sys.argv[1:]

    # 子命令
    if argv:
        cmd = argv[0]
        rest = argv[1:]
        if cmd == "config":
            _run_config(rest)
            return
        if cmd == "auth":
            _run_auth(rest)
            return
        if cmd == "doctor":
            use_json = "--json" in rest
            _run_doctor(json_output=use_json)
            return
        if cmd == "plugin":
            _run_plugin(rest)
            return
        if cmd in ("issue", "pr"):
            _run_github(argv)
            return
        if cmd == "plan":
            _run_plan_only(rest)
            return
        if cmd == "benchmark":
            _run_benchmark(rest)
            return
        if cmd == "eval":
            _run_eval(rest)
            return

    parser = _build_parser()

    # 处理 --help
    if "--help" in argv or "-h" in argv:
        _print_help()
        return

    args = parser.parse_args(argv)

    try:
        # 首次配置向导（无配置时触发）
        if _should_show_wizard():
            configured = _first_run_wizard()
            if not configured:
                sys.exit(0)

        # 保存终端启动目录（用户执行 zmai 时的 CWD）
        terminal_cwd = os.getcwd()

        # 项目检测
        root = find_project_root()
        if root:
            project_ctx = build_context(root)
        else:
            project_ctx = None

        # Runtime — 传入项目根目录让 Config 正确加载 zmai.json
        from pathlib import Path as _Path
        global_cfg = str(_Path.home() / ".zmai" / "config.json")
        config = Config() if not root else Config(
            sources=[
                FileSource(str(root / "zmai.json")),
                FileSource(global_cfg),
                EnvSource(),
                CLISource(),
            ]
        )
        config.set("project_path", terminal_cwd)
        runtime = Runtime(config=config)
        theme = _get_theme(args, config)

        # Workspace 清理
        ws_root = config.get("workspace.root", "./workspace")
        cleaned = _cleanup_old_workspaces(Path(ws_root))
        if cleaned > 0:
            sys.stderr.write(f"  (cleaned {cleaned} old workspaces)\n")

        # 启动信息
        if not getattr(args, "json", False) and sys.stderr.isatty():
            if project_ctx:
                sys.stderr.write(f"\033[2mzmai  {project_ctx.summary()}  [{runtime._gateway.default_name or ''}]\033[0m\n")
            else:
                sys.stderr.write("\033[2mzmai  (chat mode)\033[0m\n")
            _print_auth_debug(runtime._gateway)

        # 确定任务
        task = args.prompt if hasattr(args, "prompt") and args.prompt else None
        if not task and hasattr(args, "task") and args.task:
            task = " ".join(args.task)

        if task:
            _oneshot_run(task, runtime, config, args)
        elif sys.stdin.isatty():
            _cmd_interactive(runtime, config, args)
        else:
            pipe = sys.stdin.read().strip()
            if pipe:
                _oneshot_run(pipe, runtime, config, args)
            else:
                _print_help()
    except Exception as e:
        print_error(_sanitize_error(str(e)))
        sys.exit(1)


if __name__ == "__main__":
    main()
