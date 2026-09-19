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
"""  # noqa: E501

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("zmai.swe.verifier")


# ── ANSI 转义序列 ────────────────────────────────────────────────
# pytest 在 FORCE_COLOR / PY_COLORS / `--color=yes` / CI 强制颜色时会输出彩色文本，
# 转义序列直接插进被解析的语义单元中间：
#   \x1b[1m\x1b[31mapp.py\x1b[0m:2: KeyError      ← 路径被包住
#   \x1b[31mFAILED\x1b[0m test_app.py::test_a    ← "FAILED " 后紧跟转义而非空格
# 实测后果：failure parser 的 error_type 退化成 Error、file:line 变成 '' :0，
# verify_test_output 丢掉 FAILED 标记。所有测试输出解析入口因此先剥一层。
# CSI（\x1b[…m 这类 SGR）与非 CSI 的 ESC 序列（如 \x1b(B 字符集选择）都要覆盖。
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[ -/]*[0-~])")


def strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列；无转义时原样返回（不做无谓拷贝）。"""
    if not text or "\x1b" not in text:
        return text or ""
    return _ANSI_RE.sub("", text)


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
    """  # noqa: E501

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
    text = strip_ansi(test_output)
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


def classify_test_progress(
    previous: dict[str, int] | None,
    current: dict[str, int],
) -> str:
    """对比两轮测试的结构化计数，判定测试状态走向（Progress / Regression）。

    返回值：
        "first"       — 没有上一轮结果（首次运行），不判定
        "progress"    — 通过数增加，或失败/错误清零
        "no_progress" — 通过数与失败/错误数都没变（不得据此判定代码错误）
        "regression"  — 通过数减少 **且** 失败/错误数增加；
                        或上一轮有真实计数、本轮却一个测试都没跑起来（collection /
                        import error，见下）

    只比较 ``passed`` 与 ``failed + errors`` 两项结构性计数；skipped /
    deselected 的变化不参与判定（跳过数变化本身不是退步）。

    collection / import error 单独判定，不并入 failed 数字参与比较：它会让
    passed/failed 同时归零，若照数字比较会被读成"failed 从 3 降到 0 → progress"。
    因此当**上一轮有真实测试计数**而本轮 ``passed == failed == collected == 0
    且 errors > 0`` 时，直接判 regression —— 语义是"测试整个消失（模块已无法
    import）"，属最严重的退化。首次运行就是 collection error 仍返回 "first"
    （无基线，无从比较）。
    """
    if not previous:
        return "first"
    cur_pass = current["passed"]
    cur_fail = current["failed"] + current["errors"]
    prev_pass = previous["passed"]
    prev_fail = previous["failed"] + previous["errors"]
    # ── 灾难性退化：上一轮有真实测试计数，本轮一个测试都没跑起来 ──
    # collection/import error 会让 passed/failed 同时归零。若照数字比较，就会得出
    # "failed 从 3 降到 0 → progress" 的荒谬结论——那不是"失败变少"，而是
    # "测试整个消失"（模块已无法 import），必须判为 regression。
    if (prev_pass + previous["failed"] > 0
            and cur_pass == 0 and current["failed"] == 0
            and current.get("collected", 0) == 0
            and current["errors"] > 0):
        return "regression"
    if cur_pass < prev_pass and cur_fail > prev_fail:
        return "regression"
    if cur_pass > prev_pass or cur_fail < prev_fail:
        return "progress"
    return "no_progress"


def verify_test_output(test_output: str, exit_code: int | None = None,
                       ) -> VerificationCheck:
    """Test output verification.

    判定优先级（有 exit_code 时）：**真实退出码 > 结构化 pytest 计数 > 文本启发式**。
    文本关键词只有在拿不到任何结构化证据时才作为判定依据 —— 否则一次 exit 0 的
    通过运行只要输出里出现 `FAILED ` / `Traceback` / `AssertionError`（测试名、
    被测代码自己打印的 traceback、-rA 报告等）就会被判失败。

    Args:
        test_output: 命令输出（stdout+stderr 合并）。
        exit_code: 测试命令的真实退出码。为 None 时退回纯文本启发式
            （旧调用点 / 仅需文案场景），保持向后兼容。
    """
    import re
    text = strip_ansi(test_output)
    lower = text.lower()
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
        if signal in text:
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

    # ── 结构化证据（优先于文本）──
    totals = parse_test_totals(text)
    counted = totals["passed"] + totals["failed"] + totals["errors"]
    counted_failed = totals["failed"] + totals["errors"]

    if exit_code is None:
        # 拿不到真实执行结果 → 退回文本启发式（旧调用点，兼容）
        passed = not failures and has_passed_signal
        reason = f"failure markers: {', '.join(failures[:3])}"
    elif exit_code != 0:
        # 真实退出码是权威
        passed = False
        reason = f"exit code {exit_code}"
    elif counted_failed:
        # exit 0 但结构化汇总报失败（如退出码被管道掩盖）→ 以计数为准
        passed = False
        reason = f"summary reports {totals['failed']} failed, {totals['errors']} errors"
    elif counted:
        # exit 0 + 汇总无失败 → 通过；文本关键词不得推翻真实执行结果
        passed = True
        reason = ""
    else:
        # 无结构化计数（如 --collect-only）→ 仍要求出现通过信号
        passed = has_passed_signal
        reason = "no structured test summary and no pass signal"

    evidence_parts = []
    if exit_code is not None:
        evidence_parts.append(f"exit_code={exit_code}")
        evidence_parts.append(f"summary: {totals['passed']} passed, "
                              f"{totals['failed']} failed, {totals['errors']} errors")
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
        error=None if passed else f"Test result contains failures: {reason}",
    )


def validate_python_syntax(file_path: str | Path) -> tuple[bool, dict[str, Any]]:
    """Validate a Python file's syntax using compile().

    Equivalent to ``python -m py_compile <file>`` but in-process (no subprocess).
    Returns (valid, info). On failure, info contains error_type / line / message
    so the caller can build a structured EDIT_VALIDATION_FAILED signal.

    Non-Python files and unreadable files are treated as valid (skip).
    """
    p = Path(file_path)
    if p.suffix.lower() != ".py":
        return True, {}
    try:
        src = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True, {}
    try:
        compile(src, str(p), "exec")
        return True, {}
    except SyntaxError as e:  # IndentationError is a subclass
        return False, {
            "error_type": type(e).__name__,
            "line": e.lineno or 0,
            "message": str(e).splitlines()[0][:300] if str(e) else type(e).__name__,
        }


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
        len(diff_output) > 0
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
    # ── 结构化证据优先，不做裸关键词匹配 ──
    # 旧实现对成功输出做 error/fail/traceback/cannot 子串匹配，两个方向都不成立：
    #   * 假失败：成功命令输出里的普通英文（"error handling initialized"、测试名
    #     test_failure_handling，以及 ZMAI 自己注入的 "[test summary] … 0 failed,
    #     0 errors" 前缀）都会命中 → 每次成功 pytest 都必然生成失败 check；
    #   * 漏判：真正失败时 ToolResult.err() 把文本放进 error 字段、output 为空串，
    #     旧实现只读 output → 真实失败被静默跳过。
    for tr in tool_results:
        name = tr.get("name", "")
        if name not in ("shell_exec", "git"):
            continue
        output = tr.get("output") or ""
        error = tr.get("error") or ""

        # ① 工具自身报告了失败（shell_exec 非零退出码 / 执行异常）——权威信号。
        #    失败详情在 error 字段，必须读它。
        if tr.get("success") is False or error:
            detail = error or output
            checks.append(VerificationCheck(
                name=f"Command failed: {name}",
                strategy="exit_code",
                passed=False,
                target=detail[:100],
                evidence="tool reported failure (non-zero exit / execution error)",
                error=detail[:500],
            ))
            continue

        # ② 结构化测试结果：仅当输出里**能解析出 pytest 计数**时才做测试判定，
        #    依据是解析出的数字，而不是"文本里出现了 fail/error 字样"。
        totals = parse_test_totals(output)
        if totals["failed"] or totals["errors"]:
            checks.append(VerificationCheck(
                name="Test result check",
                strategy="test_output",
                passed=False,
                target=output[:100],
                evidence=(f"{totals['passed']} passed, {totals['failed']} failed, "
                          f"{totals['errors']} errors"),
                error="test summary reports failures",
            ))

        # ③ 成功且无测试计数 → 不做失败判定（普通输出不构成失败证据）。

    # 3. Attempt Git diff
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
