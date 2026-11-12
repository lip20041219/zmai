"""Paired SWE-bench Runtime A/B driver. No model calls without `run`."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "benchmarks" / "results" / "swebench_lite" / "run_smoke.py"
DATA = Path.home() / ".zmai" / "swebench_data" / "lite_instances.json"
EVAL_PYTHON = SMOKE.parent / "_evalenv" / "Scripts" / "python.exe"


def _source_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def load_protocol(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    instances = config.get("instances")
    if not isinstance(instances, list) or not 6 <= len(instances) <= 8 \
            or len(set(instances)) != len(instances):
        raise ValueError("protocol requires 6–8 unique instance IDs")
    for arm in ("old", "new"):
        commit = config.get("commits", {}).get(arm)
        if not isinstance(commit, str) or len(commit) != 40 \
                or any(c not in "0123456789abcdef" for c in commit.lower()):
            raise ValueError(f"{arm} commit must be a full 40-character Git SHA")
    if config["commits"]["old"] == config["commits"]["new"]:
        raise ValueError("old and new commits must differ")
    if config.get("backend") != "deepseek" or not config.get("model"):
        raise ValueError("this protocol pins a DeepSeek backend and model")
    if not isinstance(config.get("max_steps"), int) or config["max_steps"] <= 0:
        raise ValueError("max_steps must be positive")
    if not isinstance(config.get("wall_seconds"), int) or config["wall_seconds"] <= 0:
        raise ValueError("wall_seconds must be positive")
    return config


def _cached_instances() -> dict[str, dict]:
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    return {item.get("instance_id", ""): item for item in raw}


def validate(path: Path, config: dict, output: Path | None = None) -> dict:
    cached = _cached_instances()
    missing = sorted(set(config["instances"]) - cached.keys())
    if missing:
        raise ValueError(f"instances missing from local SWE-bench cache: {missing}")
    for instance in config["instances"]:
        for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
            tests = cached[instance].get(key)
            if isinstance(tests, str):
                tests = json.loads(tests)
            if not isinstance(tests, list) or not tests:
                raise ValueError(f"{instance} has no {key} tests")
    for arm, commit in config["commits"].items():
        result = subprocess.run(["git", "cat-file", "-e", commit + "^{commit}"],
                                cwd=ROOT, capture_output=True, check=False)
        if result.returncode:
            raise ValueError(f"{arm} commit not available locally: {commit}")
    for path_to_pin in (SMOKE, Path(__file__)):
        relative = path_to_pin.relative_to(ROOT).as_posix()
        frozen = subprocess.run(
            ["git", "show", config["commits"]["new"] + ":" + relative],
            cwd=ROOT, capture_output=True, check=False,
        )
        if frozen.returncode or frozen.stdout != _source_bytes(path_to_pin):
            raise ValueError(f"probe code differs from frozen new commit: {relative}")
    if not EVAL_PYTHON.is_file():
        raise ValueError(f"independent acceptance interpreter missing: {EVAL_PYTHON}")
    packages = subprocess.run([str(EVAL_PYTHON), "-m", "pip", "freeze"],
                              capture_output=True, timeout=30, check=True).stdout
    manifest = {"protocol": path.name, "commits": config["commits"],
            "dataset_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
            "probe_sha256": hashlib.sha256(_source_bytes(SMOKE)).hexdigest(),
            "driver_sha256": hashlib.sha256(_source_bytes(Path(__file__))).hexdigest(),
            "python": sys.version,
            "acceptance_python": str(EVAL_PYTHON),
            "acceptance_packages_sha256": hashlib.sha256(packages).hexdigest(),
            "instances": config["instances"], "backend": config["backend"],
            "model": config["model"], "max_steps": config["max_steps"],
            "wall_seconds": config["wall_seconds"]}
    if output is not None:
        output = output.resolve()
        if output.is_relative_to(ROOT):
            raise ValueError("A/B output must be outside the ZMAI source checkout")
        identities = {}
        for arm in ("old", "new"):
            source = _runtime_worktree(output, arm, config["commits"][arm]) / "src"
            env = dict(os.environ)
            env["ZMAI_RUNTIME_SRC"] = str(source)
            env["ZMAI_EXPECTED_RUNTIME_COMMIT"] = config["commits"][arm]
            env["DEEPSEEK_MODEL"] = config["model"]
            check = subprocess.run([sys.executable, str(SMOKE), "--instance", "unused",
                                    "--identity-only"], cwd=ROOT, env=env,
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=30, check=True)
            identity = json.loads(check.stdout)
            if Path(identity["source"]).resolve() != source.resolve() \
                    or identity["commit"] != config["commits"][arm] \
                    or identity.get("model") != config["model"]:
                raise ValueError(f"{arm} Runtime import identity mismatch")
            identities[arm] = identity
        manifest["runtime_identities"] = identities
    return manifest


def _runtime_worktree(root: Path, arm: str, commit: str) -> Path:
    target = root / "_runtime" / arm
    if target.exists():
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target,
                                       text=True).strip()
        if head != commit:
            raise ValueError(f"{arm} worktree commit mismatch: {head}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(target), commit],
                   cwd=ROOT, check=True)
    return target


def run(path: Path, config: dict, output: Path, *, resume: bool = False,
        allow_unisolated: bool = False) -> None:
    if not allow_unisolated:
        raise ValueError(
            "formal run requires an OS-enforced Agent filesystem boundary; "
            "use --allow-unisolated only for exploratory runs"
        )
    manifest = validate(path, config, output)
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("A/B output must be outside the ZMAI source checkout")
    if output.exists() and not resume and any(
            (output / name).exists() for name in ("old", "new", "protocol.json")):
        raise ValueError("output already exists; use a new directory or --resume")
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "protocol.json"
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != manifest_text:
        raise ValueError("resume protocol differs from existing run")
    manifest_path.write_text(manifest_text, encoding="utf-8")
    for instance in config["instances"]:
        for arm in ("old", "new"):
            arm_dir = output / arm
            result_file = arm_dir / instance / "result.json"
            if resume and result_file.is_file():
                continue
            if resume and any((arm_dir / instance / name).exists()
                              for name in ("_test_before", "_eval")):
                raise ValueError(
                    f"partial acceptance artifacts exist for {arm}/{instance}; "
                    "start a new output directory so the Agent cannot see them"
                )
            runtime_src = _runtime_worktree(output, arm, config["commits"][arm]) / "src"
            env = dict(os.environ)
            env["ZMAI_RUNTIME_SRC"] = str(runtime_src)
            env["ZMAI_EXPECTED_RUNTIME_COMMIT"] = config["commits"][arm]
            env["DEEPSEEK_MODEL"] = config["model"]
            env["ZMAI_AB_DATASET"] = str(DATA)
            env["ZMAI_AB_DATASET_SHA256"] = manifest["dataset_sha256"]
            # The probe keeps gold/test_patch in its process and creates acceptance
            # workspaces only after Runtime exits in sealed mode.
            cmd = [sys.executable, str(SMOKE), "--instance", instance,
                   "--backend", config["backend"], "--model", config["model"],
                   "--max-steps", str(config["max_steps"]),
                   "--wall-seconds", str(config["wall_seconds"]),
                   "--result-dir", str(arm_dir),
                   "--repo-dir", str(arm_dir / "repos"), "--no-hints", "--sealed-ab"]
            subprocess.run(cmd, cwd=ROOT, env=env, check=False)
            if not result_file.is_file():
                raise RuntimeError(f"probe produced no result: {arm}/{instance}")


def summarize(config: dict, output: Path) -> dict:
    pairs = []
    for instance in config["instances"]:
        arms = {}
        for arm in ("old", "new"):
            path = output / arm / instance / "result.json"
            raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            before_counts = raw.get("ftp_before_counts") or {}
            baseline_valid = (raw.get("ftp_before") == "FAIL(符合预期)"
                              and (before_counts.get("failed", 0)
                                   + before_counts.get("errors", 0)) > 0)
            external = (raw.get("ftp_after") == "PASS" and raw.get("ptp_after") == "PASS"
                        and baseline_valid)
            external_known = (raw.get("ftp_after") in ("PASS", "FAIL")
                              and raw.get("ptp_after") in ("PASS", "FAIL")
                              and baseline_valid)
            arms[arm] = {
                "runtime_status": raw.get("agent_status"),
                "patch_files_changed": raw.get("files_changed"),
                "external_resolved": external if external_known else None,
                "ftp": raw.get("ftp_after"), "ptp": raw.get("ptp_after"),
                "false_success": (raw.get("agent_status") == "completed" and not external)
                if external_known else None,
                "duration_seconds": raw.get("runtime_duration_seconds"),
                "probe_total_seconds": raw.get("duration_seconds"),
                "input_tokens": raw.get("input_tokens"),
                "output_tokens": raw.get("output_tokens"),
                "replay_total_ms": raw.get("replay_total_ms"),
                "evidence_decision": raw.get("evidence_decision"),
                "delivery_report": raw.get("delivery_report"),
            }
        pairs.append({"instance_id": instance, "old": arms["old"], "new": arms["new"]})

    summary = {"planned_pairs": len(pairs), "completed_pairs": sum(
        p["old"]["external_resolved"] is not None and p["new"]["external_resolved"] is not None
        for p in pairs), "arms": {}}
    for arm in ("old", "new"):
        rows = [p[arm] for p in pairs]
        summary["arms"][arm] = {
            "resolved": sum(r["external_resolved"] is True for r in rows),
            "resolved_known": sum(r["external_resolved"] is not None for r in rows),
            "false_success": sum(r["false_success"] is True for r in rows),
            "false_success_known": sum(r["false_success"] is not None for r in rows),
            "candidate": sum(r["runtime_status"] == "candidate" for r in rows),
            "timeout": sum(r["runtime_status"] == "timeout" for r in rows),
            "runtime_status_known": sum(r["runtime_status"] is not None for r in rows),
            **{key + "_total": sum(r[key] for r in rows) if all(
                isinstance(r[key], (int, float)) for r in rows) else None
               for key in ("duration_seconds", "input_tokens", "output_tokens",
                           "replay_total_ms")},
        }
    summary["timeout_to_candidate"] = sum(
        p["old"]["runtime_status"] == "timeout" and p["new"]["runtime_status"] == "candidate"
        for p in pairs)
    summary["timeout_to_candidate_known"] = sum(
        p["old"]["runtime_status"] is not None
        and p["new"]["runtime_status"] is not None for p in pairs)
    return {"summary": summary, "pairs": pairs}


def render_markdown(result: dict, config: dict) -> str:
    def show(value):
        return "未验证" if value is None else str(value)

    lines = ["# ZMAI M2 / P1-3 配对 A/B 结果", "",
             f"- 旧版 Commit：`{config['commits']['old']}`",
             f"- 新版 Commit：`{config['commits']['new']}`",
             f"- 模型：`{config['backend']}/{config['model']}`",
             f"- 可比较实例：{result['summary']['completed_pairs']}/{result['summary']['planned_pairs']}",
             "- resolved 仅由独立 FTP/PTP 验收确定；candidate 本身不计为 resolved。",
             "", "| 实例 | 旧 Runtime | 旧独立验收 | 新 Runtime | 新独立验收 |",
             "|---|---|---|---|---|"]
    for pair in result["pairs"]:
        old, new = pair["old"], pair["new"]
        lines.append(f"| {pair['instance_id']} | {show(old['runtime_status'])} | "
                     f"{show(old['external_resolved'])} | {show(new['runtime_status'])} | "
                     f"{show(new['external_resolved'])} |")
    lines.extend(["", "## 汇总", ""])
    for arm in ("old", "new"):
        stats = result["summary"]["arms"][arm]
        lines.append(
            f"- {arm}: resolved {stats['resolved']}/{stats['resolved_known']} 已验收，"
            f"错误完成 {stats['false_success']}/{stats['false_success_known']} 已验收，"
            f"candidate {stats['candidate']}，timeout {stats['timeout']}；"
            f"耗时合计 {show(stats['duration_seconds_total'])} 秒，"
            f"输入/输出 Token {show(stats['input_tokens_total'])}/"
            f"{show(stats['output_tokens_total'])}，回放成本 "
            f"{show(stats['replay_total_ms_total'])} ms。"
        )
    lines.append(f"- 旧 timeout → 新 candidate：{result['summary']['timeout_to_candidate']}"
                 f"/{result['summary']['timeout_to_candidate_known']} 状态已知的配对。")
    lines.append("- 缺失的运行、Token、回放或外部验收数据保持未验证；本报告不推断缺失值。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Paired sealed SWE-bench Runtime A/B")
    parser.add_argument("mode", choices=("validate", "run", "summarize"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-unisolated", action="store_true",
                        help="Exploratory only: Agent shell can read outside its project")
    args = parser.parse_args(argv)
    config = load_protocol(args.config)
    if args.mode == "validate":
        print(json.dumps(validate(args.config, config, args.output), ensure_ascii=False, indent=2))
    elif args.mode == "run":
        run(args.config, config, args.output, resume=args.resume,
            allow_unisolated=args.allow_unisolated)
    else:
        result = summarize(config, args.output)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (args.output / "summary.md").write_text(
            render_markdown(result, config), encoding="utf-8")
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
