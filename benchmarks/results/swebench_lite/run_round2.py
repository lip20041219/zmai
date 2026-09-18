"""SWE-bench Lite Round 2 批量运行脚本。

复用 run_smoke.run_instance 的完整判定链路（test_before → agent → diff →
干净副本重放 → FTP/PTP 独立判定），顺序跑多个实例：

  1. 实例选择：默认从已 clone 仓库中取 count 个（Round 2 = 10）
  2. 逐实例执行，单实例失败不中断批次
  3. 每实例写 <instance_id>/result.json（复用 run_smoke）
  4. 汇总写 round2_summary.json + 控制台表格

用法:
  python run_round2.py [--backend deepseek] [--max-steps 60]
                       [--instances id1,id2,...] [--count 10]
                       [--from-cloned-only] [--skip-existing]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.stdout = sys.stdout  # 继承终端（run_smoke 会重设，这里无需处理）

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from run_smoke import RESULT_DIR, run_instance  # noqa: E402

# Round 1 已用实例（跳过）
ROUND1_USED = {
    "pallets__flask-4992",
    "psf__requests-3362",
    "pydata__xarray-4248",
    "pylint-dev__pylint-5859",
    "mwaskom__seaborn-3010",
}

# 已 clone 的 5 个仓库（repos/ 下目录已就绪，环境已适配）
CLONED_REPOS = {
    "pallets/flask",
    "psf/requests",
    "pydata/xarray",
    "pylint-dev/pylint",
    "mwaskom/seaborn",
}


def select_instances(count: int, from_cloned_only: bool) -> list:
    """选择待跑实例。

    from_cloned_only=True → 仅在已 clone 的 5 仓库内选取（避免联网 clone +
    依赖环境失败），Round 1 已用实例排除。
    """
    from zmai.eval.swebench import load_instances

    instances = load_instances(split="lite")
    pool = [i for i in instances if i.instance_id not in ROUND1_USED]
    if from_cloned_only:
        pool = [i for i in pool if i.repo in CLONED_REPOS]
    if len(pool) < count:
        print(f"候选实例不足: 需要 {count}，候选 {len(pool)}", file=sys.stderr)
        sys.exit(2)
    # 按仓库轮询取实例，尽量覆盖多个仓库（每仓库优先取 2 个）
    by_repo: dict[str, list] = {}
    for i in pool:
        by_repo.setdefault(i.repo, []).append(i)
    picked: list = []
    repos = sorted(by_repo, key=lambda r: len(by_repo[r]), reverse=True)
    while len(picked) < count:
        progressed = False
        for r in repos:
            if len(picked) >= count:
                break
            if by_repo[r]:
                picked.append(by_repo[r].pop(0))
                progressed = True
        if not progressed:
            break
    return picked[:count]


def main() -> None:
    ap = argparse.ArgumentParser(description="SWE-bench Lite Round 2 批量运行")
    ap.add_argument("--backend", default="claude", help="backend 名称")
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--count", type=int, default=10, help="实例数量")
    ap.add_argument("--instances", default="", help="逗号分隔的 instance_id 列表（优先于 --count）")
    ap.add_argument("--from-cloned-only", action="store_true",
                    help="仅在已 clone 仓库中选实例")
    ap.add_argument("--skip-existing", action="store_true",
                    help="跳过已有 result.json 的实例")
    args = ap.parse_args()

    if args.instances:
        from zmai.eval.swebench import load_instances
        by_id = {i.instance_id: i for i in load_instances(split="lite")}
        picked = [by_id[i] for i in args.instances.split(",") if i in by_id]
        missing = [i for i in args.instances.split(",") if i not in by_id]
        if missing:
            print(f"实例不存在: {missing}", file=sys.stderr)
    else:
        picked = select_instances(args.count, args.from_cloned_only)

    if not picked:
        print("没有可运行的实例", file=sys.stderr)
        sys.exit(2)

    if args.skip_existing:
        before = len(picked)
        picked = [i for i in picked
                  if not (RESULT_DIR / i.instance_id / "result.json").exists()]
        print(f"跳过已有结果 {before - len(picked)} 个，剩余 {len(picked)} 个")

    print(f"\n{'='*70}")
    print(f"Round 2 批量运行 | backend={args.backend} max_steps={args.max_steps} "
          f"实例数={len(picked)}")
    print(f"{'='*70}")
    for idx, i in enumerate(picked, 1):
        print(f"  [{idx}/{len(picked)}] {i.instance_id} ({i.repo})")

    repo_dir = RESULT_DIR / "repos"
    repo_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "backend": args.backend,
        "max_steps": args.max_steps,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "instances": [],
    }
    results: list[dict] = []
    for idx, instance in enumerate(picked, 1):
        inst_repo = repo_dir / instance.clone_dir
        print(f"\n>>> [{idx}/{len(picked)}] {instance.instance_id} 开始 "
              f"({time.strftime('%H:%M:%S')})")
        try:
            result = run_instance(instance, inst_repo, args)
        except Exception as e:  # noqa: BLE001 — 批次必须继续
            print(f"    !!! {instance.instance_id} 异常: {e}")
            result = {
                "instance_id": instance.instance_id,
                "repo": instance.repo,
                "status": "error",
                "error": str(e)[:300],
                "failure_category": "batch_exception",
                "success": False,
            }
        results.append(result)
        outcome = ("✅ resolved" if result.get("success")
                   else f"❌ {result.get('failure_category', result.get('status'))}")
        dur = result.get("duration_seconds")
        dur_s = f"{dur}s" if dur is not None else "-"
        print(f"    >>> {instance.instance_id} → {outcome} ({dur_s})")

    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    summary["instances"] = results

    # ── 汇总统计 ─────────────────────────────────────
    total = len(results)
    resolved = sum(1 for r in results if r.get("success"))
    by_cat: dict[str, int] = {}
    for r in results:
        c = r.get("failure_category") or r.get("status") or "unknown"
        by_cat[c] = by_cat.get(c, 0) + 1
    summary["summary"] = {
        "total": total,
        "resolved": resolved,
        "resolve_rate": round(resolved / total, 3) if total else 0,
        "by_failure_category": by_cat,
    }

    out = RESULT_DIR / "round2_summary.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{'='*70}")
    print(f"Round 2 完成: {resolved}/{total} resolved "
          f"({summary['summary']['resolve_rate']*100:.1f}%)")
    print(f"失败分类: {by_cat}")
    print(f"汇总: {out}")
    print(f"{'='*70}")

    sys.exit(0 if resolved > 0 else 1)


if __name__ == "__main__":
    main()
