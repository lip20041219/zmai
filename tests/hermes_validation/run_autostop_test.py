"""自主停止机制测试 — max_iterations=5。

场景 A (task_a): autostop_demo 修复任务（可完成）→ 预期 completed 且 steps ≤ 5
场景 B (task_b): 不可能完成的任务（要求修改不存在且无法满足的接口）
                 → 预期被 max_iterations=5 截断为 timeout，绝不无限循环

记录: 实际 turn 数(steps)、实际 tool 调用数、停止原因。
输出: autostop_report.md
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from zmai.config import Config  # noqa: E402
from zmai.runtime import Runtime  # noqa: E402

DEMO = ROOT / "tests" / "hermes_validation" / "autostop_demo"
DEMO_B = ROOT / "tests" / "hermes_validation" / "autostop_demo_b"
MAX_ITERATIONS = 5

TASK_A = (DEMO / "ISSUE.md").read_text(encoding="utf-8")

TASK_B = (
    "任务：让 tests/ 下的测试全部通过。\n"
    "背景：测试 test_transform.py 引用了 app/string_utils.py 中不存在的 "
    "`transform_external_ai` 函数，且要求其输出包含 'MAGIC_OK'。\n"
    "约束：\n"
    "1. 你无权创建 transform_external_ai（该函数由外部服务提供，本项目中没有）。\n"
    "2. 禁止修改 tests/ 目录下的任何文件。\n"
    "3. 只能运行测试、读取文件、尝试修改 app/ 下的文件。\n"
    "4. 如果确认任务无法完成，就明确报告无法完成并停止。"
)


def load_config(project_path: Path) -> dict:
    cfg = Config().export()
    cfg["project_path"] = str(project_path)
    cfg["max_iterations"] = MAX_ITERATIONS
    cfg["retry"] = {"max_attempts": 3}
    return cfg


def run_once(agent_id: str, task: str, project_path: Path) -> dict:
    tool_calls: list[dict] = []
    progress: list[dict] = []

    def on_progress(kind: str, msg: str) -> None:
        progress.append({"kind": kind, "msg": msg})
        if kind == "tool":
            tool_calls.append({"name": msg})

    config = load_config(project_path)

    async def run() -> dict:
        rt_cfg = Config()
        rt_cfg.set("runtime.max_iterations", MAX_ITERATIONS)
        rt_cfg.set("runtime.max_concurrent_agents", 1)
        rt = Runtime(config=rt_cfg)
        return await rt.run(
            agent_id=agent_id,
            task=task,
            backend="deepseek",
            config=config,
            on_progress=on_progress,
        )

    start = datetime.now()
    result = asyncio.run(run())
    elapsed = (datetime.now() - start).total_seconds()

    # 统计 tool 调用
    tool_names = [t["name"] for t in tool_calls]
    tool_counts: dict[str, int] = {}
    for n in tool_names:
        tool_counts[n] = tool_counts.get(n, 0) + 1

    # 停止原因推断
    status = result.get("status", "?")
    steps = result.get("steps", 0)
    if status == "completed":
        stop_reason = "测试通过 + CompletionState → 自动 complete"
    elif status == "timeout":
        stop_reason = f"达到 max_iterations={MAX_ITERATIONS} → 强制终止 (timed_out)"
    elif status == "failed":
        stop_reason = f"step_failed/工具全败/验证失败 → fail: {str(result.get('error', ''))[:100]}"
    else:
        stop_reason = f"其他: {status}"

    return {
        "agent_id": agent_id,
        "status": status,
        "steps": steps,
        "elapsed_seconds": round(elapsed, 2),
        "tool_call_total": len(tool_names),
        "tool_call_by_type": tool_counts,
        "stop_reason": stop_reason,
        "error": result.get("error"),
        "output": str(result.get("output", ""))[:300],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["a", "b"], default=None)
    args = parser.parse_args()

    report: dict = {
        "test": "autostop_demo",
        "max_iterations": MAX_ITERATIONS,
        "started_at": datetime.now().isoformat(),
        "scenarios": {},
    }

    if args.only in (None, "a"):
        report["scenarios"]["A_completable"] = run_once(
            f"autostop_a_{int(datetime.now().timestamp()*1000)}", TASK_A, DEMO)
        print(json.dumps(report["scenarios"]["A_completable"], ensure_ascii=False, indent=2))

    if args.only in (None, "b"):
        report["scenarios"]["B_impossible"] = run_once(
            f"autostop_b_{int(datetime.now().timestamp()*1000)}", TASK_B, DEMO_B)
        print(json.dumps(report["scenarios"]["B_impossible"], ensure_ascii=False, indent=2))

    report["finished_at"] = datetime.now().isoformat()
    out = ROOT / "tests" / "hermes_validation" / "autostop_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport json: {out}")


if __name__ == "__main__":
    main()
