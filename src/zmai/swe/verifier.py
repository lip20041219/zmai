"""Verifier — objective verification module.

Verifies whether the agent's changes are actually effective, rather than relying solely on tool call success.

Architecture:
  VerificationResult
    ├── passed: bool           ← all checks passed
    ├── summary: str           ← human-readable summary
    └── errors: list[str]      ← global errors

5 verification strategies:
  1. file_exists   — file existence
  2. file_content  — file content matching
  3. exit_code     — command exit code
  4. test_output   — test result parsing
  5. git_diff      — Git change inspection
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("zmai.swe.verifier")


@dataclass
class VerificationCheck:
    """A single verification check result.

    Attributes:
        name: Human-readable check name.
        strategy: Verification strategy name (file_exists / file_content / exit_code / test_output / git_diff).
        passed: Whether the check passed.
        target: The target being checked (file path, command, etc.).
        evidence: Observed facts (basis for pass/fail judgment).
        error: Error message on failure.
    """

    name: str
    strategy: str
    passed: bool
    target: str = ""
    evidence: str = ""
    error: str | None = None


@dataclass
class VerificationResult:
    """Aggregated verification results.

    Attributes:
        passed: All checks passed.
        checks: Individual check details.
        summary: Human-readable summary.
        errors: Global errors (not per-check errors).
    """

    passed: bool
    checks: list[VerificationCheck] = field(default_factory=list)
    summary: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def passed_checks(self) -> list[VerificationCheck]:
        return [c for c in self.checks if c.passed]

    @property
    def failed_checks(self) -> list[VerificationCheck]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {
                    "name": c.name,
                    "strategy": c.strategy,
                    "passed": c.passed,
                    "target": c.target,
                    "evidence": c.evidence[:200] if c.evidence else "",
                    "error": c.error,
                }
                for c in self.checks
            ],
            "summary": self.summary,
            "errors": self.errors,
        }

    @classmethod
    def merge(cls, results: list[VerificationResult]) -> VerificationResult:
        """Merge multiple verification results."""
        all_checks: list[VerificationCheck] = []
        all_errors: list[str] = []
        for r in results:
            all_checks.extend(r.checks)
            all_errors.extend(r.errors)
        passed = all(c.passed for c in all_checks) and not all_errors
        return cls(
            passed=passed,
            checks=all_checks,
            summary=f"{sum(1 for c in all_checks if c.passed)}/{len(all_checks)} checks passed"
                     if all_checks else "No checks available",
            errors=all_errors,
        )


# ═══════════════════════════════════════════════════════════
# 验证策略实现
# ═══════════════════════════════════════════════════════════


def verify_file_exists(path: str | Path, workspace: Path | None = None) -> VerificationCheck:
    """File existence verification.

    Checks if the specified path exists and is a regular file.
    """
    full = _resolve(path, workspace)
    exists = full.exists() and full.is_file()
    return VerificationCheck(
        name=f"文件存在: {path}",
        strategy="file_exists",
        passed=exists,
        target=str(path),
        evidence=f"文件存在 ({full.stat().st_size} bytes)" if exists else "文件不存在",
        error=None if exists else f"文件 {path} 不存在",
    )


def verify_file_content(
    path: str | Path,
    expected: str | None = None,
    workspace: Path | None = None,
) -> VerificationCheck:
    """File content verification.

    Reads the file and checks length or content.
    No third-party dependency; checks:
      - File is readable
      - Non-empty (when no expected text specified)
      - Contains expected text (when specified)
    """
    full = _resolve(path, workspace)
    if not full.exists() or not full.is_file():
        return VerificationCheck(
            name=f"文件内容: {path}",
            strategy="file_content",
            passed=False,
            target=str(path),
            evidence="文件不存在",
            error=f"文件 {path} 不存在",
        )

    try:
        content = _read_file_safe(full)
    except Exception as e:
        return VerificationCheck(
            name=f"文件内容: {path}",
            strategy="file_content",
            passed=False,
            target=str(path),
            evidence="读取失败",
            error=str(e),
        )

    checks: list[bool] = []

    # 非空检查
    non_empty = len(content.strip()) > 0
    checks.append(non_empty)

    # 可选预期内容检查
    pattern_ok = True
    if expected:
        pattern_ok = expected.lower() in content.lower()
        checks.append(pattern_ok)

    passed = all(checks)

    evidence_parts = []
    evidence_parts.append(f"{len(content)} chars")
    if non_empty:
        evidence_parts.append("非空")
    if expected:
        evidence_parts.append(f"包含预期文本: {pattern_ok}")
    if passed:
        evidence_parts.append("内容验证通过")

    return VerificationCheck(
        name=f"文件内容: {path}",
        strategy="file_content",
        passed=passed,
        target=str(path),
        evidence="; ".join(evidence_parts),
        error=None if passed else f"文件 {path} 内容验证失败",
    )


def verify_exit_code(
    command: str,
    expected_code: int = 0,
    workspace: Path | None = None,
    timeout: int = 30,
) -> VerificationCheck:
    """Command exit code verification.

    Executes the specified command and checks if the exit code matches expectations.
    """
    cwd = str(workspace) if workspace else None
    try:
        r = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        actual = r.returncode
        passed = actual == expected_code
        return VerificationCheck(
            name=f"命令退出码: {command[:80]}",
            strategy="exit_code",
            passed=passed,
            target=command[:120],
            evidence=f"exit code {actual}"
                     + (f" (期望 {expected_code})" if not passed else ""),
            error=None if passed else f"命令退出码 {actual} ≠ 期望 {expected_code}",
        )
    except subprocess.TimeoutExpired:
        return VerificationCheck(
            name=f"命令超时: {command[:80]}",
            strategy="exit_code",
            passed=False,
            target=command[:120],
            evidence=f"超时 ({timeout}s)",
            error=f"命令执行超时 ({timeout}s)",
        )
    except Exception as e:
        return VerificationCheck(
            name=f"命令执行失败: {command[:80]}",
            strategy="exit_code",
            passed=False,
            target=command[:120],
            evidence=str(e),
            error=str(e),
        )


def parse_test_totals(test_output: str) -> dict[str, int]:
    """解析 pytest 输出的各类测试数量（通过/失败/错误/跳过/反选/忽略）。

    用于"基线测试数回退防护"：若某次运行实际执行的测试总数低于首次记录的
    基线，说明有测试被反选、删除或忽略，即使 pytest exit 0 且显示 passed，
    也视为未真正验证业务代码（伪造成功），不得计入完成。

    注意：只从 pytest **末尾的汇总行**（形如 "4 passed, 1 deselected in 0.05s"）
    解析，不能在整个输出里找计数——traceback 里的 "200"、"passed" 等片段会
    造成误匹配（曾把 4 个测试误判成 200 个）。
    """
    import re
    text = test_output or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # 汇总行：包含某计数词且带耗时标记 " in "
    summary = text
    for ln in reversed(lines):
        if re.search(r"\d+\s+(passed|failed|error|skipped|deselected|ignored)", ln) \
                and " in " in ln:
            summary = ln
            break
    low = summary.lower()

    def _count(pat: str) -> int:
        m = re.search(pat, low)
        return int(m.group(1)) if m else 0

    return {
        "passed": _count(r"(\d+)\s+passed"),
        "failed": _count(r"(\d+)\s+failed"),
        "errors": _count(r"(\d+)\s+errors?"),
        "skipped": _count(r"(\d+)\s+skipped"),
        "deselected": _count(r"(\d+)\s+deselected"),
        "ignored": _count(r"(\d+)\s+ignored"),
        "collected": _count(r"(\d+)\s+collected\s+items"),
    }


def verify_test_output(test_output: str) -> VerificationCheck:
    """Test output verification.

    Parses the test output string and checks for failure markers.
    Supports common test framework output patterns.
    """
    import re
    lower = test_output.lower()
    failures: list[str] = []

    # ── Failure signals ─────────────────────────────────────────
    # Context-free substrings that always indicate a real failure.
    # "FAILED " / "FAILURES" / "FAIL:" are checked against the ORIGINAL
    # text (pytest emits them uppercase); the lowercased "failed" in a green
    # summary ("290 passed, 0 failed") must NOT trigger them.
    for signal, label in [
        ("FAILED ", "FAILED"),
        ("FAIL:", "FAIL:"),
        ("FAILURES", "FAILURES"),
        ("Traceback", "Traceback"),
    ]:
        if signal in test_output:
            failures.append(label)
    for signal, label in [
        ("tests failed", "tests failed"),
        ("AssertionError", "AssertionError"),
        ("exit code 1", "exit code 1"),
    ]:
        if signal in lower:
            failures.append(label)

    # Counted failures — "N failed" / "N errors" only fail when N > 0.
    # This correctly treats pytest's green summary "290 passed, 0 failed"
    # as passing instead of the old substring match on "failed".
    for pat, label in [
        (r"(\d+)\s+failed", "N failed"),
        (r"(\d+)\s+errors?\b", "N errors"),
        (r"(\d+)\s+failures?\b", "N failures"),
    ]:
        for m in re.finditer(pat, lower):
            if int(m.group(1)) > 0:
                failures.append(f"{label}={m.group(1)}")

    # ── Pass signals ────────────────────────────────────────────
    passed_signals = [
        "passed",
        "all tests passed",
        "ok",
        "100%",
        "test session starts",
        "no tests failed",
        "succeeded",
    ]
    has_passed_signal = any(s in lower for s in passed_signals)

    passed = not failures and has_passed_signal

    evidence_parts = []
    if has_passed_signal:
        evidence_parts.append("Test pass signal detected")
    if failures:
        evidence_parts.append(f"Failure markers found: {', '.join(failures[:3])}")
    if passed:
        evidence_parts.append("Test result verification passed")

    return VerificationCheck(
        name="Test result verification",
        strategy="test_output",
        passed=passed,
        target="",
        evidence="; ".join(evidence_parts) if evidence_parts else test_output[:100],
        error=None if passed else f"Test result contains failures: {', '.join(failures[:3])}",
    )


def verify_git_diff(workspace: Path | None = None) -> VerificationCheck:
    """Git diff verification.

    Checks if the workspace has uncommitted Git changes.
    Changes do not need to be committed; only verifies they were correctly recorded.
    """
    cwd = str(workspace) if workspace else None
    try:
        r = subprocess.run(
            "git diff --stat",
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        diff_output = (r.stdout or "").strip()
        has_diff = len(diff_output) > 0
        return VerificationCheck(
            name="Git diff check",
            strategy="git_diff",
            passed=True,  # Having or not having a diff both count as passing
            target="",
            evidence=f"{diff_output.count(chr(10)) + 1 if diff_output else 0} file(s) changed"
                     if diff_output else "No uncommitted changes",
            error=None,
        )
    except Exception as e:
        return VerificationCheck(
            name="Git diff check",
            strategy="git_diff",
            passed=False,
            target="",
            evidence="Git check failed",
            error=f"Git diff execution failed: {e}",
        )


# ═══════════════════════════════════════════════════════════
# 自动验证生成
# ═══════════════════════════════════════════════════════════


def auto_generate_checks(
    modified_files: list[str],
    tool_results: list[dict[str, Any]],
    workspace: Path | None = None,
) -> VerificationResult:
    """Automatically generate verification checks based on context.

    Analyzes modified files and tool execution results to automatically
    select appropriate verification strategies.

    Args:
        modified_files: List of modified file paths.
        tool_results: List of tool results (with name, success, output).
        workspace: Workspace path.

    Returns:
        Merged verification result.
    """
    checks: list[VerificationCheck] = []

    # 1. Verify all written/edited files exist
    for f in modified_files:
        checks.append(verify_file_exists(f, workspace))

    # 2. Check shell/test tool execution results
    test_outputs: list[str] = []
    for tr in tool_results:
        name = tr.get("name", "")
        output = tr.get("output", "")

        if name in ("shell_exec", "git") and output:
            # Check output for error signals
            error_signals = ["error", "fail", "traceback", "cannot"]
            lower = output.lower()
            has_error = any(s in lower for s in error_signals)
            if has_error:
                checks.append(VerificationCheck(
                    name=f"Command output check: {name}",
                    strategy="exit_code",
                    passed=False,
                    target=output[:100],
                    evidence="Output contains error signals",
                    error=f"{name} output appears to contain errors",
                ))

        if "test" in name.lower() or "pytest" in name.lower():
            test_outputs.append(output)

    # 3. Verify test results if test output exists
    for to in test_outputs:
        checks.append(verify_test_output(to))

    # 4. Attempt Git diff
    try:
        checks.append(verify_git_diff(workspace))
    except Exception:
        pass  # Silently skip if not a git repository

    return VerificationResult(
        passed=all(c.passed for c in checks) if checks else True,
        checks=checks,
        summary=f"{sum(1 for c in checks if c.passed)}/{len(checks)} checks passed"
                if checks else "No checks available",
    )


def _resolve(path: str | Path, workspace: Path | None = None) -> Path:
    """Resolve a file path."""
    p = Path(path)
    if p.is_absolute():
        return p
    if workspace:
        return (workspace / p).resolve()
    return p.resolve()


def _read_file_safe(path: Path) -> str:
    """Safely read file (UTF-8 → system encoding fallback)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        import locale
        enc = locale.getpreferredencoding()
        return path.read_text(encoding=enc, errors="replace")
    except Exception:
        raise
