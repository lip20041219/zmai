"""Eval and Benchmark CLI — zmai eval & zmai benchmark."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from zmai.cli.formatters import print_error

logger = logging.getLogger("zmai.cli.eval")


# ═══════════════════════════════════════════════════════════════
# zmai benchmark (保留兼容)
# ═══════════════════════════════════════════════════════════════


def run_benchmark(argv: list[str]) -> None:
    """`zmai benchmark <list|run|report>`"""
    from zmai.benchmark import BenchmarkRunner, format_report

    if not argv or argv[0] == "list":
        tasks = BenchmarkRunner.list_tasks()
        if not tasks:
            print("  (no tasks found)")
            return
        print(f"\n  {'Task ID':<30} {'Description':<50} {'Files'}")
        print(f"  {'-'*90}")
        for t in tasks:
            files = ", ".join(t["files"])
            print(f"  {t['id']:<30} {t['description']:<50} {files}")
        print(f"\n  {len(tasks)} task(s) available\n")
        return

    if argv[0] == "run":
        task_ids = None
        if len(argv) > 1 and argv[1] in ("-t", "--task"):
            task_ids = [argv[2]] if len(argv) > 2 else None
        elif len(argv) > 1:
            task_ids = argv[1:]

        backend = os.environ.get("ZMAI_BENCHMARK_BACKEND", "mock")
        max_steps = int(os.environ.get("ZMAI_BENCHMARK_MAX_STEPS", "20"))

        print(f"  Benchmark 开始 (backend={backend}, max_steps={max_steps})")
        print()

        runner = BenchmarkRunner(backend_name=backend, max_steps=max_steps)
        report = runner.run_all(task_ids=task_ids)
        runner.save_report(report)

        print(format_report(report))
        return

    if argv[0] == "report":
        report = BenchmarkRunner.load_report()
        if report is None:
            print("  (no benchmark report found. Run `zmai benchmark run` first.)")
            return
        print(format_report(report))
        return

    print("  Usage: zmai benchmark <list|run|report>")
    print("    zmai benchmark list              List available tasks")
    print("    zmai benchmark run               Run all tasks")
    print("    zmai benchmark run -t TASK_ID    Run single task")
    print("    zmai benchmark report            Show latest report")


# ═══════════════════════════════════════════════════════════════
# zmai eval
# ═══════════════════════════════════════════════════════════════


def run_eval(argv: list[str]) -> None:
    """`zmai eval <subcommand> [options]`

    Subcommands:
      list              List available tasks
      run               Run evaluation tasks
      report            Show latest report
      swebench          Run SWE-bench evaluation
      humaneval         Run HumanEval evaluation
      custom            Run custom evaluation
    """
    if not argv:
        _print_help()
        return

    cmd = argv[0]

    if cmd == "list":
        _cmd_list(argv[1:])
    elif cmd == "run":
        _cmd_run(argv[1:])
    elif cmd == "report":
        _cmd_report(argv[1:])
    elif cmd == "swebench":
        _cmd_swebench(argv[1:])
    elif cmd == "humaneval":
        _cmd_humaneval(argv[1:])
    elif cmd == "custom":
        _cmd_custom(argv[1:])
    elif cmd in ("--help", "-h"):
        _print_help()
    else:
        # 兼容旧版：直接跟 task_id
        _cmd_run(argv)


def _print_help() -> None:
    print("  Usage: zmai eval <subcommand> [options]")
    print()
    print("  Subcommands:")
    print("    list                                        List available tasks")
    print("    run [task_id]                               Run evaluation")
    print("    report                                      Show latest report")
    print("    swebench [--max N] [--repo R] [--output fmt] [--output-file P]")
    print("                                                Run SWE-bench Lite")
    print("    humaneval --path FILE [--max N] [--output fmt] [--output-file P]")
    print("                                                Run HumanEval")
    print("    custom [--path DIR] [--output fmt] [--output-file P]")
    print("                                                Run custom evaluation")
    print()
    print("  Output formats: json, markdown, csv (default: console)")
    print()
    print("  Examples:")
    print("    zmai eval swebench --max 5 --output markdown")
    print("    zmai eval humaneval --path data/humaneval.json --output json")
    print("    zmai eval custom --output csv --output-file results.csv")


def _parse_common_args(argv: list[str]) -> dict[str, Any]:
    """解析通用参数：--output, --output-file, --backend, --max-steps. """
    args: dict[str, Any] = {
        "output": "console",
        "output_file": None,
        "backend": os.environ.get("ZMAI_EVAL_BACKEND", "mock"),
        "max_steps": int(os.environ.get("ZMAI_EVAL_MAX_STEPS", "20")),
    }

    i = 0
    while i < len(argv):
        if argv[i] == "--output" and i + 1 < len(argv):
            args["output"] = argv[i + 1]
            i += 2
        elif argv[i] == "--output-file" and i + 1 < len(argv):
            args["output_file"] = argv[i + 1]
            i += 2
        elif argv[i] == "--backend" and i + 1 < len(argv):
            args["backend"] = argv[i + 1]
            i += 2
        elif argv[i] == "--max-steps" and i + 1 < len(argv):
            args["max_steps"] = int(argv[i + 1])
            i += 2
        else:
            i += 1
    return args


def _emit_output(
    stats: Any,
    output_format: str,
    output_file: str | None,
    *,
    is_markdown: bool = False,
    is_json: bool = False,
    is_csv: bool = False,
) -> None:
    """根据输出格式打印/保存结果。"""
    from zmai.eval.reporter import ScoreReporter

    reporter = ScoreReporter()

    if output_format == "json":
        text = reporter.to_json(stats, path=output_file)
        if not output_file:
            print(text)
    elif output_format == "markdown":
        text = reporter.to_markdown(stats, path=output_file)
        if not output_file:
            print(text)
    elif output_format == "csv":
        text = reporter.to_csv(stats, path=output_file)
        if not output_file:
            print(text)
    else:
        # console
        text = reporter.format_console(stats)
        print(text)

    if output_file:
        print(f"\n  报告已保存: {output_file}")


# ── list ─────────────────────────────────────────────────────


def _cmd_list(argv: list[str]) -> None:
    from zmai.eval import EvalHarness
    tasks = EvalHarness.list_tasks(EvalHarness)
    print(EvalHarness.format_list(tasks))


# ── run (旧版兼容) ──────────────────────────────────────────


def _cmd_run(argv: list[str]) -> None:
    from zmai.eval import EvalHarness

    task_id = None
    if argv and argv[0] not in ("-t", "--task"):
        task_id = argv[0]
    elif len(argv) > 1 and argv[0] in ("-t", "--task"):
        task_id = argv[1]

    backend = os.environ.get("ZMAI_EVAL_BACKEND", "mock")
    max_steps = int(os.environ.get("ZMAI_EVAL_MAX_STEPS", "20"))

    print(f"  Eval 开始 (backend={backend}, max_steps={max_steps})")
    print()

    harness = EvalHarness(backend_name=backend, max_steps=max_steps)

    if task_id:
        result = harness.run_task(task_id)
        tag = {
            "PASS": "✅ PASS",
            "FAIL": "❌ FAIL",
            "TIMEOUT": "⏰ TIMEOUT",
            "ERROR": "💥 ERROR",
        }.get(result.status, result.status)
        print(f"  {result.task_id:<30} {tag:<10} {result.steps:<8} {result.duration:.1f}s")
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        report = harness.run_all()
        harness.save_report(report)
        print(EvalHarness.format_report(report))


# ── report ──────────────────────────────────────────────────


def _cmd_report(argv: list[str]) -> None:
    from zmai.eval import EvalHarness
    report = EvalHarness.load_report()
    if report is None:
        print("  (no eval report found. Run `zmai eval run` first.)")
        return
    print(EvalHarness.format_report(report))


# ── swebench ────────────────────────────────────────────────


def _cmd_swebench(argv: list[str]) -> None:
    """`zmai eval swebench [--max N] [--repo R] [--output fmt] [--output-file P]`"""
    common = _parse_common_args(argv)

    # Parse swebench-specific args
    max_instances = 0
    repos = None
    split = "lite"

    i = 0
    while i < len(argv):
        if argv[i] == "--max" and i + 1 < len(argv):
            max_instances = int(argv[i + 1])
            i += 2
        elif argv[i] == "--repo" and i + 1 < len(argv):
            repos = [argv[i + 1]]
            i += 2
        elif argv[i] == "--split" and i + 1 < len(argv):
            split = argv[i + 1]
            i += 2
        elif argv[i].startswith("--"):
            i += 2 if i + 1 < len(argv) and not argv[i + 1].startswith("--") else 1
        else:
            i += 1

    from zmai.eval.benchmark import BenchmarkRunner

    runner = BenchmarkRunner(
        backend=common["backend"],
        max_steps=common["max_steps"],
    )

    print(f"  SWE-bench {split} 开始 (backend={common['backend']})")
    if max_instances:
        print(f"  最大实例数: {max_instances}")
    print()

    stats = runner.run_swebench(
        split=split,
        max_instances=max_instances,
        repos=repos,
    )

    _emit_output(stats, common["output"], common["output_file"])


# ── humaneval ───────────────────────────────────────────────


def _cmd_humaneval(argv: list[str]) -> None:
    """`zmai eval humaneval --path FILE [--max N] [--output fmt] [--output-file P]`"""
    common = _parse_common_args(argv)

    path = None
    max_instances = 0

    i = 0
    while i < len(argv):
        if argv[i] == "--path" and i + 1 < len(argv):
            path = argv[i + 1]
            i += 2
        elif argv[i] == "--max" and i + 1 < len(argv):
            max_instances = int(argv[i + 1])
            i += 2
        elif argv[i].startswith("--"):
            i += 2 if i + 1 < len(argv) and not argv[i + 1].startswith("--") else 1
        else:
            i += 1

    if not path:
        print_error("HumanEval 需要 --path 参数指定 JSON 文件路径")
        print('  示例: zmai eval humaneval --path data/humaneval.json')
        sys.exit(1)

    if not Path(path).exists():
        print_error(f"文件不存在: {path}")
        sys.exit(1)

    from zmai.eval.benchmark import BenchmarkRunner

    runner = BenchmarkRunner(
        backend=common["backend"],
        max_steps=common["max_steps"],
    )

    print(f"  HumanEval 开始 (backend={common['backend']})")
    print(f"  来源: {path}")
    if max_instances:
        print(f"  最大实例数: {max_instances}")
    print()

    stats = runner.run_humaneval(path=path, max_instances=max_instances)

    _emit_output(stats, common["output"], common["output_file"])


# ── custom ──────────────────────────────────────────────────


def _cmd_custom(argv: list[str]) -> None:
    """`zmai eval custom [--path DIR] [--output fmt] [--output-file P]`"""
    common = _parse_common_args(argv)

    path = None
    task_ids = None

    i = 0
    while i < len(argv):
        if argv[i] == "--path" and i + 1 < len(argv):
            path = argv[i + 1]
            i += 2
        elif argv[i] == "--task" and i + 1 < len(argv):
            if task_ids is None:
                task_ids = []
            task_ids.append(argv[i + 1])
            i += 2
        elif argv[i].startswith("--"):
            i += 2 if i + 1 < len(argv) and not argv[i + 1].startswith("--") else 1
        else:
            i += 1

    from zmai.eval.benchmark import BenchmarkRunner

    runner = BenchmarkRunner(
        backend=common["backend"],
        max_steps=common["max_steps"],
    )

    if path:
        print(f"  自定义评测开始 (backend={common['backend']})")
        print(f"  来源: {path}")
    else:
        print(f"  内置评测开始 (backend={common['backend']})")
    print()

    stats = runner.run_custom(path=path, task_ids=task_ids)

    _emit_output(stats, common["output"], common["output_file"])
