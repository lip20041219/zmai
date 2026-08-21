"""SWE-bench Lite Smoke Run — 单个任务驱动脚本。

独立于 benchmark.py 的判定逻辑：
  1. 从本地缓存加载实例
  2. 独立工作目录内 clone + checkout base_commit
  3. 记录 test_before（base_commit 上应用 test_patch 后 FTP 应失败 → 验证任务可复现）
  4. 启动 Runtime.run（真实 backend）
  5. 提取 agent 的 git diff → 在干净 base_commit 上重放 + 应用 test_patch
  6. 独立跑 FTP / PTP → 判定 resolved
  7. 采集指标并写 result.json

用法:
  python run_smoke.py --instance pallets__flask-4992 [--only-fetch] [--repo-dir DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 项目根：run_smoke.py 位于 benchmarks/results/swebench_lite/，向上 3 层到项目根
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("smoke")

RESULT_DIR = Path(__file__).resolve().parent

# 独立判定使用的 Python：隔离 venv（pytest 8.4，兼容 monkeypatch.notset 等旧 API）
EVAL_PYTHON = str(Path(__file__).resolve().parent / "_evalenv" / "Scripts" / "python.exe")


def repo_src_path(repo_path: Path) -> str:
    """返回仓库源码目录（绝对路径）：src-layout（如 flask 的 src/）用
    <repo>/src，扁平 layout（如 requests/pylint）用 repo 根目录。
    必须绝对化——PYTHONPATH 会在子进程 cwd（repo 本身）下解析。"""
    src = repo_path / "src"
    return str((src if src.is_dir() else repo_path).resolve())


def parse_pytest_counts(output: str) -> dict:
    """从 pytest 输出解析 passed/failed/errors 计数（G5：tests_* 出计数而非原文）。"""
    counts = {"passed": 0, "failed": 0, "errors": 0}
    if not output:
        return counts
    m = re.search(r"(\d+) passed", output)
    if m:
        counts["passed"] = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m:
        counts["failed"] = int(m.group(1))
    m = re.search(r"(\d+) errors", output)
    if m:
        counts["errors"] = int(m.group(1))
    return counts


# ── 辅助函数 ────────────────────────────────────────────────


def sh(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 300,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """运行命令，返回 CompletedProcess。"""
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=timeout,
        input=input_text,
    )


def run_tests_independent(
    repo_path: Path,
    test_ids: list[str],
    timeout: int = 300,
) -> tuple[bool, str]:
    """在 repo 上跑 pytest（node id 列表），返回 (passed, output)。

    关键：通过 PYTHONPATH 指向仓库源码目录（src-layout 或扁平布局），
    确保测试导入的是仓库自己的源码，而非全局安装的包。
    使用隔离 venv（pytest 8.4）以兼容 monkeypatch.notset 等旧 API。
    """
    if not test_ids:
        return True, "no tests"
    env = dict(os.environ)
    env["PYTHONPATH"] = repo_src_path(repo_path)
    r = subprocess.run(
        [EVAL_PYTHON, "-m", "pytest", *test_ids, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(repo_path), capture_output=True, text=True, timeout=timeout,
        env=env,
    )
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"(\d+)\s+failed", out)
    if m:
        passed = int(m.group(1)) == 0
    else:
        passed = r.returncode == 0
    return passed, out[-3000:]


def apply_patch(repo: Path, patch_text: str) -> tuple[bool, str]:
    """git apply 一个 patch。返回 (成功, 错误信息)。"""
    r = sh(["git", "-C", str(repo), "apply", "-"], timeout=60,
           input_text=patch_text)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout)[:500]
    return True, ""


def collect_agent_metrics(agent_id: str, ws_path: Path | None) -> dict:
    """从 ExecutionLog 采集 agent 指标。

    ExecutionLog 由 Runtime 落盘到 <workspace.root>/<agent_id>/.state/execution_log.json
    （workspace.root 默认 ./workspace，即运行 CWD 下的 workspace/ 目录）。
    注意：每条工具调用在日志中产生 tool_call + tool_result 两条记录，
    tool_calls 计数应只按 phase == "tool_call" 统计，否则会翻倍。
    """
    metrics: dict = {}
    if ws_path:
        log_file = ws_path / ".state" / "execution_log.json"
        if log_file.exists():
            try:
                data = json.loads(log_file.read_text(encoding="utf-8"))
                steps = data.get("steps", [])
                metrics["steps"] = len(steps)
                tool_calls = [s for s in steps if s.get("phase") == "tool_call"]
                metrics["tool_calls"] = len(tool_calls)
                pytest_calls = [
                    s for s in steps
                    if s.get("phase") == "tool_call"
                    and s.get("tool_name") == "shell_exec"
                    and "pytest" in str(s.get("tool_input", {}))
                ]
                metrics["test_runs"] = len(pytest_calls)
                metrics["execution_log_ok"] = True
            except Exception as e:
                metrics["execution_log_ok"] = False
                metrics["execution_log_error"] = str(e)[:200]
    return metrics


# ── 核心 ────────────────────────────────────────────────────


def fetch_and_prepare(
    instance, work_dir: Path, repo_dir: Path, force: bool = False,
) -> Path:
    """clone + checkout base_commit。返回 repo 路径。"""
    from zmai.eval.swebench import setup_instance_repo
    return setup_instance_repo(instance, work_dir)


def run_instance(instance, repo_dir: Path, args) -> dict:
    """对单个实例执行完整链路，返回 result dict。"""
    result: dict = {
        "instance_id": instance.instance_id,
        "repo": instance.repo,
        "base_commit": instance.base_commit,
        "model": "",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "success": None,
        "status": "running",
        "error": "",
        "duration_seconds": None,
        "steps": None,
        "tool_calls": None,
        "test_runs": None,
        "files_changed": None,
        "lines_added": None,
        "lines_deleted": None,
        "tests_before": None,
        "tests_after": None,
        "ftp_before": None,
        "ftp_after": None,
        "ptp_after": None,
        "failure_reason": "",
        "failure_category": "",
    }
    inst_dir = RESULT_DIR / instance.instance_id
    inst_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()

    try:
        # ── 1. 准备 repo ─────────────────────────────
        if not repo_dir.exists():
            logger.info("[%s] cloning %s", instance.instance_id, instance.repo)
            repo_dir = fetch_and_prepare(instance, repo_dir.parent, repo_dir)
        else:
            # 已存在 → 重置到 base_commit
            r = sh(["git", "-C", str(repo_dir), "checkout", "-f", instance.base_commit])
            if r.returncode != 0:
                # 可能分支不存在，先 fetch
                sh(["git", "-C", str(repo_dir), "fetch", "--all", "--tags"], timeout=600)
                r = sh(["git", "-C", str(repo_dir), "checkout", "-f", instance.base_commit])
            if r.returncode != 0:
                raise RuntimeError(f"checkout base_commit 失败: {(r.stderr or '')[:200]}")
            sh(["git", "-C", str(repo_dir), "reset", "--hard", "HEAD"])
            sh(["git", "-C", str(repo_dir), "clean", "-fd"])

        # ── 2. test_before：base_commit + test_patch 跑 FTP ──
        # 在独立副本上验证任务可复现（FTP 应失败）
        before_dir = inst_dir / "_test_before"
        before_repo = before_dir / instance.clone_dir
        if before_repo.exists():
            sh(["git", "-C", str(before_repo), "checkout", "-f", instance.base_commit])
            sh(["git", "-C", str(before_repo), "reset", "--hard", "HEAD"])
            sh(["git", "-C", str(before_repo), "clean", "-fd"])
        else:
            before_dir.mkdir(parents=True, exist_ok=True)
            from zmai.eval.swebench import setup_instance_repo
            before_repo = setup_instance_repo(instance, before_dir)
        ok, err = apply_patch(before_repo, instance.test_patch)
        if not ok:
            logger.warning("[%s] test_before: test_patch 应用失败: %s", instance.instance_id, err)
            result["ftp_before"] = "ERROR: test_patch apply failed"
        else:
            ftp_before, out_before = run_tests_independent(before_repo, instance.FAIL_TO_PASS)
            result["ftp_before"] = "PASS(异常)" if ftp_before else "FAIL(符合预期)"
            result["tests_before"] = out_before
            result["ftp_before_counts"] = parse_pytest_counts(out_before)
            (inst_dir / "test_before.log").write_text(out_before, encoding="utf-8")

        # ── 3. 应用 test_patch 到 agent 工作仓库 ──────────────
        # 让 agent 能看到 FAIL_TO_PASS 测试失败，否则 base_commit 上
        # 根本没有这些测试，agent 永远看不到失败信号。
        ok, err = apply_patch(repo_dir, instance.test_patch)
        if not ok:
            logger.warning("[%s] test_patch apply 失败（agent 工作区）: %s",
                          instance.instance_id, err)
            result["failure_category"] = "patch_failure"
            result["failure_reason"] = f"test_patch apply failed: {err}"
            return finalize(result, start)
        logger.info("[%s] test_patch applied to agent workspace", instance.instance_id)

        # ── 4. 启动 Agent ─────────────────────────────
        from zmai.config import Config
        from zmai.runtime import Runtime

        # agent 的 shell 测试环境：使用隔离 venv 的 python + PYTHONPATH 指向仓库源码。
        # 否则 agent 用全局 python（3.13）跑测试会因 requests 旧代码 import cgi 而
        # ImportError，看不到真实的 FTP 失败信号（与 flask harness 修复同理）。
        _eval_bin = str(Path(EVAL_PYTHON).parent)
        os.environ["PYTHONPATH"] = repo_src_path(repo_dir)
        os.environ["PATH"] = _eval_bin + os.pathsep + os.environ.get("PATH", "")

        agent_id = f"eval_{instance.instance_id}"
        config = Config(sources=[])
        config.set("runtime.max_iterations", args.max_steps)
        rt = Runtime(config=config)
        rdict = asyncio.run(rt.run(
            agent_id=agent_id,
            task=instance.format_task(),
            backend=args.backend,
            config={
                "project_path": str(repo_dir),
                # SWE-bench eval 守卫：未修改任何源码不得因现有测试全绿而完成
                "eval.require_code_change": "true",
            },
        ))

        result["status"] = rdict.get("status", "")
        result["steps"] = rdict.get("steps", 0)
        result["error"] = (rdict.get("error") or "")[:500]

        # ── Token 用量 + 模块触发计数（G1/G3/G5）──
        # Runtime.run() 现在透出 finalize 的 metadata（swe_stats + token_usage）。
        _meta = rdict.get("metadata") or {}
        _tok = _meta.get("token_usage") or {}
        result["input_tokens"] = _tok.get("input_tokens", 0) or 0
        result["output_tokens"] = _tok.get("output_tokens", 0) or 0
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
        _swe = _meta.get("swe_stats") or {}
        result["failure_parser_used"] = int(_swe.get("failure_parser_used", 0) or 0)
        result["loop_guard_triggered"] = bool(_swe.get("loopguard_blocks", 0))
        result["test_guard_triggered"] = bool(_swe.get("test_guard_triggered", 0))

        # Agent workspace 指标
        # ExecutionLog 由 Runtime 写入 workspace.root/<agent_id>/.state/，
        # workspace.root 默认 ./workspace（相对运行 CWD），即 RESULT_DIR/workspace。
        ws = RESULT_DIR / "workspace" / agent_id
        metrics = collect_agent_metrics(agent_id, ws)
        result["tool_calls"] = metrics.get("tool_calls")
        result["test_runs"] = metrics.get("test_runs")

        # ── 基础设施失败：backend 调用错误（如 HTTP 402/限流/网络）──
        # agent 根本没机会执行 → 标记 infrastructure_failure，不判 no_change/test_failure。
        # 这类失败反映的是环境/API 状态，不是 agent 修复能力。
        if result["error"] and any(
            k in result["error"].upper()
            for k in ("HTTP 4", "HTTP 5", "BACKEND_ERROR", "TIMEOUT", "RATE-LIMIT", "RATE_LIMIT")
        ):
            result["failure_category"] = "infrastructure_failure"
            result["failure_reason"] = f"Backend/API 错误，agent 未能执行: {result['error'][:200]}"
            result["status"] = "error"
            result["success"] = False
            return finalize(result, start)

        # ── 4. 保存 git.diff ──────────────────────────
        # 先剥离预应用的 test_patch：checkout 恢复 test_patch 涉及的文件到 base_commit。
        # 否则 agent 的 git diff 会包含预应用测试改动 → eval 重复应用冲突 + 误判 test_tampering。
        _tp_files = _test_patch_files(instance)
        if _tp_files:
            sh(["git", "-C", str(repo_dir), "checkout", "--"] + _tp_files)
            logger.info("[%s] test_patch files reverted before diff: %s",
                        instance.instance_id, _tp_files)
        r = sh(["git", "-C", str(repo_dir), "diff"], timeout=60)
        git_diff = r.stdout or ""
        (inst_dir / "git.diff").write_text(git_diff, encoding="utf-8")
        if git_diff.strip():
            added = sum(
                1 for line in git_diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            deleted = sum(
                1 for line in git_diff.splitlines()
                if line.startswith("-") and not line.startswith("---")
            )
            files_changed = len(re.findall(r"^\+\+\+ b/(.+)$", git_diff, re.M))
            result["lines_added"] = added
            result["lines_deleted"] = deleted
            result["files_changed"] = files_changed

        # ── 5. 独立判定（重放 diff 于干净 base + test_patch）──
        # 5a. 提取 agent 的 diff
        if not git_diff.strip():
            result["failure_category"] = "no_change"
            result["failure_reason"] = "Agent 未产生任何代码修改"
            result["status"] = "failed"
            result["success"] = False
            return finalize(result, start)

        # 检查 diff 是否修改了测试文件（伪造检测）
        test_files_touched = _find_touched_test_files(git_diff, instance)
        if test_files_touched:
            result["test_guard_violation"] = test_files_touched
            result["failure_category"] = "test_tampering"
            result["failure_reason"] = f"Agent 修改了测试文件: {test_files_touched}"
            result["success"] = False
            return finalize(result, start)

        # 5b. 干净副本：base_commit → apply agent diff → apply test_patch → 跑测试
        eval_dir = inst_dir / "_eval"
        eval_repo = eval_dir / instance.clone_dir
        if eval_repo.exists():
            sh(["git", "-C", str(eval_repo), "checkout", "-f", instance.base_commit])
            sh(["git", "-C", str(eval_repo), "reset", "--hard", "HEAD"])
            sh(["git", "-C", str(eval_repo), "clean", "-fd"])
        else:
            eval_dir.mkdir(parents=True, exist_ok=True)
            from zmai.eval.swebench import setup_instance_repo
            eval_repo = setup_instance_repo(instance, eval_dir)

        ok, err = apply_patch(eval_repo, git_diff)
        if not ok:
            result["failure_category"] = "patch_failure"
            result["failure_reason"] = f"agent diff 应用失败: {err}"
            result["success"] = False
            return finalize(result, start)
        ok, err = apply_patch(eval_repo, instance.test_patch)
        if not ok:
            result["failure_category"] = "patch_failure"
            result["failure_reason"] = f"test_patch 应用失败: {err}"
            result["success"] = False
            return finalize(result, start)

        # 5c. 跑 FTP + PTP
        ftp_after, out_ftp = run_tests_independent(eval_repo, instance.FAIL_TO_PASS)
        ptp_after, out_ptp = run_tests_independent(eval_repo, instance.PASS_TO_PASS)
        result["ftp_after"] = "PASS" if ftp_after else "FAIL"
        result["ptp_after"] = "PASS" if ptp_after else "FAIL"
        result["tests_after"] = out_ftp
        result["ftp_after_counts"] = parse_pytest_counts(out_ftp)
        result["ptp_after_counts"] = parse_pytest_counts(out_ptp)
        (inst_dir / "test_after_ftp.log").write_text(out_ftp, encoding="utf-8")
        (inst_dir / "test_after_ptp.log").write_text(out_ptp, encoding="utf-8")

        if ftp_after and ptp_after:
            result["success"] = True
            result["status"] = "resolved"
        else:
            result["success"] = False
            result["status"] = "failed"
            result["failure_category"] = "test_failure"
            result["failure_reason"] = (
                f"FTP={('PASS' if ftp_after else 'FAIL')} "
                f"PTP={('PASS' if ptp_after else 'FAIL')}"
            )
        return finalize(result, start)

    except Exception as e:
        logger.error("[%s] 异常: %s", instance.instance_id, traceback.format_exc())
        result["status"] = "error"
        result["error"] = str(e)[:500]
        result["failure_category"] = _classify_error(str(e))
        result["success"] = False
        return finalize(result, start)


def _find_touched_test_files(git_diff: str, instance) -> list[str]:
    """检查 agent diff 是否触碰 FTP/PTP 涉及的测试文件。"""
    touched = re.findall(r"^\+\+\+ b/(.+)$", git_diff, re.M)
    test_paths = set()
    for t in list(instance.FAIL_TO_PASS) + list(instance.PASS_TO_PASS):
        test_paths.add(t.split("::")[0])
    hit = [t for t in touched if t in test_paths]
    return hit


def _classify_error(msg: str) -> str:
    low = msg.lower()
    if "clone" in low or "connection" in low or "timeout" in low:
        return "environment"
    if "apply" in low or "patch" in low:
        return "patch_failure"
    if "api" in low or "key" in low or "backend" in low:
        return "model_api"
    return "unknown"


def _test_patch_files(instance) -> list[str]:
    """从 test_patch 提取修改的文件路径列表。"""
    files = []
    for line in instance.test_patch.splitlines():
        if line.startswith("--- a/"):
            files.append(line[6:])
    return files


def finalize(result: dict, start: float) -> dict:
    result["duration_seconds"] = round(time.time() - start, 2)
    inst_dir = RESULT_DIR / result["instance_id"]
    (inst_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return result


# ── main ────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="SWE-bench Lite 单任务 smoke run")
    ap.add_argument("--instance", required=True, help="instance_id")
    ap.add_argument("--backend", default="deepseek", help="backend 名称")
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--repo-dir", default=None, help="repo 缓存目录（跨任务复用）")
    args = ap.parse_args()

    from zmai.eval.swebench import load_instances
    instances = load_instances(split="lite")
    by_id = {i.instance_id: i for i in instances}
    if args.instance not in by_id:
        print(f"实例不存在: {args.instance}", file=sys.stderr)
        sys.exit(2)
    instance = by_id[args.instance]

    if args.repo_dir:
        repo_dir = Path(args.repo_dir) / instance.clone_dir
    else:
        repo_dir = RESULT_DIR / "repos" / instance.clone_dir
    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    result = run_instance(instance, repo_dir, args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
