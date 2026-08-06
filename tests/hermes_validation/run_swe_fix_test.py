"""SWE Agent 自动修复真实测试驱动。

调用 ZMAI Runtime + DeepSeek backend（credential store 配置），
对 swe_fix_demo 目录执行 ISSUE.md 描述的修复任务。

验证 SWE Agent 5 项能力:
  1. 是否读取任务 (ISSUE.md)
  2. 是否分析原因
  3. 是否修改文件 (app/calculator.py)
  4. 是否运行测试 (pytest)
  5. 是否自动停止 (complete 而非超时/循环)

用法: python run_swe_fix_test.py [--max-iterations N] [--output FILE]
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # ZMAI 根
sys.path.insert(0, str(ROOT))

from zmai.runtime import Runtime  # noqa: E402
from zmai.config import Config  # noqa: E402

DEMO = ROOT / "tests" / "hermes_validation" / "swe_fix_demo"


def load_config(max_iterations: int, project_path: Path) -> dict:
    """从 zmai.json 加载配置并覆盖测试参数。"""
    cfg = Config()
    merged = cfg.export()
    merged["max_iterations"] = max_iterations
    merged["project_path"] = str(project_path)
    merged["timeout"] = 60
    merged["retry"] = {"max_attempts": 3}
    merged["runtime"] = {
        "max_iterations": max_iterations,
        "timeout": 300,
        "max_concurrent_agents": 1,
    }
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    task = (DEMO / "ISSUE.md").read_text(encoding="utf-8")

    progress: list[dict] = []
    tool_trace: list[dict] = []

    def on_progress(kind: str, msg: str) -> None:
        progress.append({"kind": kind, "msg": msg, "ts": datetime.now().isoformat()})
        if kind == "tool":
            tool_trace.append({"action": msg, "ts": datetime.now().isoformat()})
        print(f"[progress:{kind}] {msg[:120]}", flush=True)

    config = load_config(args.max_iterations, DEMO)

    async def run() -> dict:
        rt_cfg = Config()  # 加载默认配置源 (zmai.json 等)
        rt_cfg.set("runtime.max_iterations", args.max_iterations)
        rt_cfg.set("runtime.max_concurrent_agents", 1)
        rt_cfg.set("runtime.timeout", 300)
        rt = Runtime(config=rt_cfg)
        result = await rt.run(
            agent_id=f"swe_fix_{int(datetime.now().timestamp()*1000)}",
            task=task,
            backend="deepseek",
            config=config,
            on_progress=on_progress,
        )
        return result

    start = datetime.now()
    result = asyncio.run(run())
    elapsed = (datetime.now() - start).total_seconds()

    report = {
        "task": "swe_fix_demo",
        "started_at": start.isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "result": result,
        "progress_count": len(progress),
        "tool_actions": [p["msg"] for p in progress if p["kind"] == "tool"],
        "tool_action_count": len([p for p in progress if p["kind"] == "tool"]),
        "result_count": len([p for p in progress if p["kind"] == "result"]),
    }

    out = args.output or str(ROOT / "tests" / "hermes_validation" / "swe_fix_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n=== RESULT: status={result.get('status')} steps={result.get('steps')} ===")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
