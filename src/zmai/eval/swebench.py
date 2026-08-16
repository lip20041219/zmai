"""SWE-bench Lite evaluation harness.

Loads SWE-bench Lite instances, sets up repositories, runs agents, and
evaluates results using the standard SWE-bench methodology.

SWE-bench instance format:
  - instance_id: unique identifier
  - repo: "owner/repo"
  - base_commit: git commit to checkout
  - problem_statement: task description
  - hints_text: optional hints
  - patch: gold patch (for reference)
  - test_patch: test patch to apply for evaluation
  - FAIL_TO_PASS: tests expected to flip from fail→pass
  - PASS_TO_PASS: tests expected to remain passing
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("zmai.eval.swebench")

# ── Constants ─────────────────────────────────────────────────────

SWEBENCH_LITE_URL = (
    "https://raw.githubusercontent.com/princeton-nlp/SWE-bench/main/"
    "data/lite/instances.json"
)

SWEBENCH_VERIFIED_URL = (
    "https://raw.githubusercontent.com/princeton-nlp/SWE-bench/main/"
    "data/verified/instances.json"
)

DEFAULT_SPLIT = "lite"
SWEBENCH_DATA_DIR = Path.home() / ".zmai" / "swebench_data"


# ── Data Models ───────────────────────────────────────────────────


@dataclass
class SWEBenchInstance:
    """A single SWE-bench evaluation instance."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    patch: str = ""
    test_patch: str = ""
    FAIL_TO_PASS: list[str] = field(default_factory=list)
    PASS_TO_PASS: list[str] = field(default_factory=list)

    @property
    def owner(self) -> str:
        return self.repo.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.repo.split("/")[1]

    @property
    def clone_dir(self) -> str:
        return f"{self.owner}__{self.repo_name}"

    def format_task(self) -> str:
        """Format the problem statement as a task prompt for the agent."""
        parts = [self.problem_statement]
        if self.hints_text:
            parts.append(f"\n\nHints:\n{self.hints_text}")
        return "\n".join(parts)


@dataclass
class SWEBenchResult:
    """Result of evaluating a single SWE-bench instance."""

    instance_id: str
    resolved: bool
    status: str  # "resolved", "failed", "error", "timeout"
    duration_seconds: float
    agent_steps: int = 0
    error: str = ""
    agent_output: str = ""


@dataclass
class SWEBenchReport:
    """Aggregated SWE-bench evaluation report."""

    total: int
    resolved: int
    failed: int
    errors: int
    results: list[SWEBenchResult]

    @property
    def resolve_rate(self) -> float:
        return self.resolved / self.total * 100 if self.total > 0 else 0.0

    def summary(self) -> str:
        return (
            f"SWE-bench {DEFAULT_SPLIT}: "
            f"{self.resolved}/{self.total} resolved "
            f"({self.resolve_rate:.1f}%)"
        )


# ── Data Loading ─────────────────────────────────────────────────


def download_instances(split: str = DEFAULT_SPLIT) -> list[dict[str, Any]]:
    """Download SWE-bench instances from GitHub raw data.

    Args:
        split: "lite" or "verified"

    Returns:
        List of raw instance dicts.
    """
    url = SWEBENCH_LITE_URL if split == "lite" else SWEBENCH_VERIFIED_URL

    logger.info("Downloading SWE-bench %s instances from %s", split, url)

    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except Exception as e:
        logger.error("Failed to download SWE-bench data: %s", e)
        logger.info("Trying local cache...")
        data = _load_local(split)
        if not data:
            raise RuntimeError(
                f"Cannot download SWE-bench data from {url}. "
                f"Check your internet connection or use a local file."
            ) from e

    # Cache locally
    _save_local(split, data)

    if isinstance(data, dict):
        # Some versions use {instance_id: instance} format
        return list(data.values())
    return data


def _load_local(split: str) -> list[dict[str, Any]]:
    """Load SWE-bench instances from local cache."""
    local_file = SWEBENCH_DATA_DIR / f"{split}_instances.json"
    if local_file.exists():
        with open(local_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return list(data.values())
        return data
    return []


def _save_local(split: str, data: list[dict[str, Any]] | dict[str, Any]) -> None:
    """Cache SWE-bench instances locally."""
    SWEBENCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
    local_file = SWEBENCH_DATA_DIR / f"{split}_instances.json"
    with open(local_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_instances(
    split: str = DEFAULT_SPLIT,
    max_instances: int = 0,
    repos: list[str] | None = None,
) -> list[SWEBenchInstance]:
    """Load and parse SWE-bench instances.

    Args:
        split: "lite" or "verified"
        max_instances: Max instances to load (0 = all)
        repos: Optional filter by repo name (e.g. ["django/django"])

    Returns:
        List of parsed SWEBenchInstance objects.
    """
    raw_instances = download_instances(split)

    instances: list[SWEBenchInstance] = []
    for raw in raw_instances:
        inst = SWEBenchInstance(
            instance_id=raw.get("instance_id", ""),
            repo=raw.get("repo", ""),
            base_commit=raw.get("base_commit", ""),
            problem_statement=raw.get("problem_statement", raw.get("text", "")),
            hints_text=raw.get("hints_text", ""),
            patch=raw.get("patch", ""),
            test_patch=raw.get("test_patch", ""),
            FAIL_TO_PASS=raw.get("FAIL_TO_PASS", []),
            PASS_TO_PASS=raw.get("PASS_TO_PASS", []),
        )
        if repos and inst.repo not in repos:
            continue
        instances.append(inst)

    if max_instances > 0:
        instances = instances[:max_instances]

    logger.info("Loaded %d SWE-bench %s instances", len(instances), split)
    return instances


# ── Repository Setup ─────────────────────────────────────────────


def setup_instance_repo(
    instance: SWEBenchInstance,
    work_dir: str | Path,
) -> Path:
    """Clone and checkout the base commit for an instance.

    Args:
        instance: The SWE-bench instance.
        work_dir: Working directory for repos.

    Returns:
        Path to the cloned repository.
    """
    work_dir = Path(work_dir)
    repo_path = work_dir / instance.clone_dir

    if repo_path.exists():
        logger.info("Repo exists at %s", repo_path)
        # Clean and checkout base commit
        subprocess.run(
            ["git", "-C", str(repo_path), "checkout", "-f", instance.base_commit],
            capture_output=True, text=True, timeout=60,
        )
        return repo_path

    # Clone
    clone_url = f"https://github.com/{instance.repo}.git"
    logger.info("Cloning %s into %s", clone_url, repo_path)

    work_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", clone_url, str(repo_path)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Clone failed for {instance.repo}: {result.stderr[:500]}")

    # Checkout base commit
    result = subprocess.run(
        ["git", "-C", str(repo_path), "checkout", instance.base_commit],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Checkout failed for {instance.repo} @ {instance.base_commit}: "
            f"{result.stderr[:500]}"
        )

    return repo_path


# ── Evaluation ────────────────────────────────────────────────────


def apply_patch(repo_path: str | Path, patch_text: str) -> bool:
    """Apply a git patch to the repository.

    Args:
        repo_path: Path to the repository.
        patch_text: The patch content (git diff format).

    Returns:
        True if patch applied cleanly.
    """
    repo_path = Path(repo_path)
    result = subprocess.run(
        ["git", "-C", str(repo_path), "apply", "-"],
        input=patch_text,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        logger.warning("Patch apply failed: %s", result.stderr[:300])
        return False
    return True


def run_tests(
    repo_path: str | Path,
    test_files: list[str],
    timeout: int = 300,
) -> tuple[bool, str]:
    """Run specific test files and check if they pass.

    Args:
        repo_path: Path to the repository.
        test_files: List of test file paths relative to repo root.
        timeout: Test timeout in seconds.

    Returns:
        (passed, output)
    """
    if not test_files:
        return True, "No tests to run"

    repo_path = Path(repo_path)
    test_args = " ".join(test_files)

    result = subprocess.run(
        f"python -m pytest {test_args} -x -q --no-header 2>&1 || true",
        shell=True,
        cwd=str(repo_path),
        capture_output=True, text=True, timeout=timeout,
    )
    output = (result.stdout or "") + (result.stderr or "")

    # Check if tests passed
    passed = "passed" in output or "failed" not in output.split("\n")[-3:]
    # More precise: check for "X failed" in the last few lines
    failed_match = re.search(r"(\d+) failed", output)
    if failed_match:
        passed = int(failed_match.group(1)) == 0

    return passed, output[:2000]


def evaluate_instance(
    instance: SWEBenchInstance,
    repo_path: str | Path,
    agent_output: str = "",
) -> bool:
    """Evaluate whether an agent's fix resolves the instance.

    Uses the SWE-bench evaluation methodology:
    1. Apply the test patch
    2. Run FAIL_TO_PASS tests (should pass)
    3. Run PASS_TO_PASS tests (should still pass)

    Args:
        instance: The SWE-bench instance.
        repo_path: Path to the repository with agent's changes.
        agent_output: Agent's output (for logging).

    Returns:
        True if the instance is resolved.
    """
    repo_path = Path(repo_path)

    # Stash agent's changes first
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "-A"],
        capture_output=True, text=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "stash"],
        capture_output=True, text=True, timeout=30,
    )

    # Reset to base commit
    subprocess.run(
        ["git", "-C", str(repo_path), "checkout", "-f", instance.base_commit],
        capture_output=True, text=True, timeout=30,
    )

    # Apply test patch
    if instance.test_patch:
        if not apply_patch(repo_path, instance.test_patch):
            logger.error("Failed to apply test patch for %s", instance.instance_id)
            return False

    # Restore agent's changes on top
    subprocess.run(
        ["git", "-C", str(repo_path), "stash", "pop"],
        capture_output=True, text=True, timeout=30,
    )

    # Run FAIL_TO_PASS tests
    if instance.FAIL_TO_PASS:
        ftp_passed, ftp_output = run_tests(repo_path, instance.FAIL_TO_PASS)
        if not ftp_passed:
            logger.info(
                "FAIL_TO_PASS tests failed for %s: %s",
                instance.instance_id, ftp_output[:200],
            )
            return False

    # Run PASS_TO_PASS tests
    if instance.PASS_TO_PASS:
        ptp_passed, ptp_output = run_tests(repo_path, instance.PASS_TO_PASS)
        if not ptp_passed:
            logger.info(
                "PASS_TO_PASS tests failed for %s: %s",
                instance.instance_id, ptp_output[:200],
            )
            return False

    return True


# ── Reporting ─────────────────────────────────────────────────────


def format_report(report: SWEBenchReport) -> str:
    """Format evaluation report as human-readable text."""
    lines = [
        "=" * 60,
        "  SWE-bench Lite Results",
        "=" * 60,
        "",
        f"  Total:     {report.total}",
        f"  Resolved:  {report.resolved}  ({report.resolve_rate:.1f}%)",
        f"  Failed:    {report.failed}",
        f"  Errors:    {report.errors}",
        "",
        "-" * 60,
        "  Per-instance results:",
        "",
    ]
    for r in report.results:
        status_mark = "✅" if r.resolved else "❌"
        lines.append(f"  {status_mark} {r.instance_id:<30} {r.duration_seconds:.0f}s")
        if r.error:
            lines.append(f"      error: {r.error[:100]}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def save_report(report: SWEBenchReport, path: str | Path) -> None:
    """Save evaluation report to JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "total": report.total,
        "resolved": report.resolved,
        "failed": report.failed,
        "errors": report.errors,
        "resolve_rate": report.resolve_rate,
        "results": [
            {
                "instance_id": r.instance_id,
                "resolved": r.resolved,
                "status": r.status,
                "duration_seconds": r.duration_seconds,
                "agent_steps": r.agent_steps,
                "error": r.error[:500] if r.error else "",
            }
            for r in report.results
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Report saved to %s", path)
