"""Auditable, redacted delivery reports for completed SWE Runtime runs."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_MAX_PATCH_BYTES = 5 * 1024 * 1024
_MAX_UNTRACKED_BYTES = 1024 * 1024
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
        r"github_pat_[A-Za-z0-9_]{12,})\b"
    ),
    re.compile(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|token|secret|password|authorization)"
        r"\b\s*[:=]\s*)([^\s,;]+)"
    ),
)
_WINDOWS_PATH = re.compile(
    r"(?<![\w])(?:\"[A-Za-z]:[\\/][^\"\r\n]*\"|"
    r"[A-Za-z]:[\\/][^\s\"'<>|]+)"
)
_POSIX_HOME_PATH = re.compile(r"(?<![\w])/(?:home|Users|mnt|tmp|private|workspace)/[^\s\"'<>|]+")


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: Path | None
    git_root: Path | None
    commit: str | None
    status: tuple[tuple[str, str], ...]
    error: str | None = None
    excluded_paths: tuple[str, ...] = ()


def sanitize_text(value: Any, *, project_root: Path | None = None, limit: int = 2000) -> str:
    """Redact credentials and local paths from user-controlled report text."""
    text = str(value or "")
    if project_root is not None:
        try:
            text = re.sub(re.escape(str(project_root.resolve())), "[PROJECT_ROOT]", text,
                          flags=re.IGNORECASE)
        except OSError:
            pass
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    text = _WINDOWS_PATH.sub("[LOCAL_PATH]", text)
    text = _POSIX_HOME_PATH.sub("[LOCAL_PATH]", text)
    return text[:limit]


def capture_workspace(
    root: str | Path | None, *, exclude_paths: tuple[str | Path, ...] = (),
) -> WorkspaceSnapshot:
    """Capture Git identity and dirty paths without modifying the worktree."""
    if root is None:
        return WorkspaceSnapshot(None, None, None, (), "project path unavailable")
    try:
        resolved_root = Path(root).resolve()
    except (OSError, RuntimeError, TypeError) as exc:
        return WorkspaceSnapshot(None, None, None, (), type(exc).__name__)
    if not resolved_root.is_dir():
        return WorkspaceSnapshot(resolved_root, None, None, (), "project directory unavailable")

    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=resolved_root,
            capture_output=True, timeout=5, check=False,
        )
        if top.returncode:
            return WorkspaceSnapshot(resolved_root, None, None, (), "not a Git worktree")
        git_root = Path(os.fsdecode(top.stdout).strip()).resolve()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=git_root,
            capture_output=True, timeout=5, check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=git_root, capture_output=True, timeout=10, check=False,
        )
        if head.returncode or status.returncode:
            return WorkspaceSnapshot(git_root, git_root, None, (), "Git snapshot unavailable")
        excluded: list[str] = []
        for path in exclude_paths:
            try:
                relative = Path(path).resolve().relative_to(git_root).as_posix()
            except (OSError, RuntimeError, ValueError):
                continue
            if relative == ".":
                return WorkspaceSnapshot(
                    git_root, git_root, os.fsdecode(head.stdout).strip() or None,
                    (), "workspace overlaps project root",
                )
            excluded.append(relative)
        status_entries = _parse_status(status.stdout)
        if excluded:
            status_entries = [
                (code, path) for code, path in status_entries
                if not any(path == base or path.startswith(base + "/") for base in excluded)
            ]
        return WorkspaceSnapshot(
            git_root, git_root, os.fsdecode(head.stdout).strip() or None,
            tuple(status_entries), None, tuple(excluded),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return WorkspaceSnapshot(resolved_root, None, None, (), type(exc).__name__)


def create_delivery_report(
    *,
    result: dict[str, Any],
    task: str,
    run_id: str,
    started_at: str,
    elapsed_seconds: float,
    before: WorkspaceSnapshot | None,
    project_root: str | Path | None,
    workspace_path: str | Path,
) -> dict[str, Any]:
    """Build and safely persist a whitelisted JSON/Markdown report and patch."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("invalid run id")
    workspace = Path(workspace_path).resolve()
    output_root = workspace / "output"
    report_dir = output_root / "delivery" / run_id
    if not output_root.resolve().is_relative_to(workspace):
        raise ValueError("agent output directory escaped workspace")
    if not report_dir.resolve().is_relative_to(output_root.resolve()):
        raise ValueError("report path escaped agent output directory")
    report_dir.mkdir(parents=True, exist_ok=True)
    if not report_dir.resolve().is_relative_to(output_root.resolve()):
        raise ValueError("report path escaped agent output directory")

    project = Path(project_root).resolve() if project_root is not None else None
    after = capture_workspace(project, exclude_paths=(workspace,))
    changes = _collect_changes(before, after, project)
    changes["scope"] = "Git-visible project changes; Runtime workspace excluded"
    if isinstance(result.get("metadata"), dict):
        metadata = result["metadata"]
    else:
        metadata = {
            key: result[key]
            for key in ("evidence_decision", "evidence_ledger", "evidence_shadow")
            if key in result
        }
    decision = metadata.get("evidence_decision") if isinstance(metadata, dict) else None
    decision = decision if isinstance(decision, dict) else {}
    ledger = metadata.get("evidence_ledger") if isinstance(metadata, dict) else None
    ledger = ledger if isinstance(ledger, dict) else {}
    evidence_records = ledger.get("records") if isinstance(ledger.get("records"), list) else []
    records = [_safe_record(item, project) for item in evidence_records if isinstance(item, dict)]
    objective = ledger.get("objective_checks") if isinstance(ledger.get("objective_checks"), list) else []
    objective_checks = [
        {
            "name": sanitize_text(check.get("name", "unknown"), project_root=project, limit=120),
            "passed": check.get("passed") if isinstance(check.get("passed"), bool) else None,
            "reason": sanitize_text(check.get("reason", ""), project_root=project, limit=300),
        }
        for check in objective if isinstance(check, dict)
    ]
    terminal_facts = ledger.get("terminal_facts") if isinstance(ledger.get("terminal_facts"), list) else []
    status = sanitize_text(result.get("status", "unknown"), limit=40)
    terminal_reason = decision.get("reason") or result.get("error") or "unknown"
    token_usage = metadata.get("token_usage") if isinstance(metadata.get("token_usage"), dict) else {}
    input_tokens = _number(token_usage.get("input_tokens"))
    output_tokens = _number(token_usage.get("output_tokens"))
    total_tokens = _number(token_usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    swe_stats = metadata.get("swe_stats") if isinstance(metadata.get("swe_stats"), dict) else {}
    execution_log = _execution_log_summary(
        workspace, project, result.get("agent_id"), started_at
    )
    tests = _verification(records, objective_checks, status, ledger)
    risks = _risks(status, decision, tests, changes)
    report = {
        "schema_version": "1.0",
        "run": {
            "id": run_id,
            "started_at": started_at,
            "elapsed_seconds": round(max(0.0, float(elapsed_seconds)), 3),
            "agent_id": sanitize_text(result.get("agent_id", "unknown"), project_root=project, limit=120),
            "task": sanitize_text(task, project_root=project, limit=1200) or "unknown",
            "steps": result.get("steps") if isinstance(result.get("steps"), int) else None,
            "execution_log": execution_log,
            "swe_stats": {
                key: _number(swe_stats.get(key))
                for key in (
                    "total_calls", "read_calls", "unique_read_files", "duplicate_reads",
                    "edit_calls", "pytest_calls", "unchanged_pytest_calls", "edit_failures",
                    "edit_validation_failures", "edit_failure_recoveries", "regression_detected",
                    "module_broken_regressions", "test_guard_triggered", "loopguard_blocks",
                    "failure_parser_used", "fixdriving_activations", "degenerate_responses",
                )
            },
        },
        "terminal": {
            "status": status,
            "reason": sanitize_text(terminal_reason, project_root=project, limit=1000),
            "decision_kind": sanitize_text(decision.get("kind", "unknown"), limit=40),
            "legacy_status": sanitize_text(metadata.get("terminal_legacy_status", "unknown"), limit=40),
            "facts": [
                {
                    "status": sanitize_text(fact.get("status", "unknown"), limit=40),
                    "source": sanitize_text(fact.get("source", "unknown"), limit=100),
                    "step": _number(fact.get("step")),
                    "reason": sanitize_text(fact.get("reason", ""), project_root=project, limit=500),
                }
                for fact in terminal_facts[-30:] if isinstance(fact, dict)
            ],
        },
        "project": {
            "name": sanitize_text(project.name, project_root=project, limit=120) if project else "unknown",
            "base_commit": before.commit if before and before.commit else "unknown",
            "baseline_worktree": (
                "unknown" if before is None or before.error else
                "dirty" if before.status else "clean"
            ),
        },
        "changes": changes,
        "verification": {
            "evidence_status": tests["evidence_status"],
            "tests": tests,
            "objective_checks": objective_checks or "unknown",
            "external_acceptance": "not_run",
        },
        "usage": {
            "elapsed_seconds": round(max(0.0, float(elapsed_seconds)), 3),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
        "risks": risks,
        "next_action": _next_action(status, decision),
    }
    markdown = _render_markdown(report)
    json_path = report_dir / "report.json"
    markdown_path = report_dir / "report.md"
    created: list[Path] = []
    try:
        if changes["patch"]["status"] in ("available", "redacted"):
            patch_path = report_dir / "patch.diff"
            # Patch is deliberately a review artifact if any content was redacted.
            patch_content = changes["patch"].pop("_patch_bytes")
            _atomic_write(patch_path, patch_content)
            created.append(patch_path)
        json_bytes = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        _atomic_write(json_path, json_bytes)
        created.append(json_path)
        _atomic_write(markdown_path, markdown.encode("utf-8"))
        created.append(markdown_path)
    except Exception:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return {
        "status": "generated",
        "run_id": run_id,
        "directory": f"output/delivery/{run_id}",
        "json": "report.json",
        "markdown": "report.md",
        "patch": "patch.diff" if report["changes"]["patch"]["status"] in (
            "available", "redacted"
        ) else None,
    }


def _parse_status(raw: bytes) -> list[tuple[str, str]]:
    tokens = raw.decode("utf-8", "replace").split("\0")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        item = tokens[index]
        index += 1
        if len(item) < 4:
            continue
        code, path = item[:2], item[3:]
        entries.append((code, path.replace("\\", "/")))
        if "R" in code or "C" in code:
            index += 1  # Git -z emits the old rename/copy path as a second token.
    return entries


def _collect_changes(
    before: WorkspaceSnapshot | None,
    after: WorkspaceSnapshot,
    project: Path | None,
) -> dict[str, Any]:
    empty_patch = {"status": "unknown", "path": None, "sha256": None,
                   "redacted": False, "complete": False}
    if before is None or before.git_root is None or after.git_root is None:
        return {"files": "unknown", "patch": {**empty_patch, "status": "unavailable"},
                "attribution": "unknown"}
    if before.error or after.error:
        return {"files": "unknown", "patch": {**empty_patch, "status": "unavailable"},
                "attribution": sanitize_text(before.error or after.error)}
    if before.excluded_paths != after.excluded_paths:
        return {"files": "unknown", "patch": {**empty_patch, "status": "snapshot_mismatch"},
                "attribution": "unknown because excluded paths changed"}
    after_paths = {path: code for code, path in after.status}
    before_paths = {path: code for code, path in before.status}
    dirty_start = bool(before.status)
    ambiguous_paths = sorted(before_paths)
    attributable_paths = sorted(path for path in after_paths if path not in before_paths)
    files = [
        {"path": sanitize_text(path, project_root=project, limit=500),
         "status": after_paths[path], "attribution": "run"}
        for path in (attributable_paths if dirty_start else sorted(after_paths))
    ]
    if dirty_start:
        return {
            "files": files,
            "ambiguous_preexisting_paths": [
                sanitize_text(path, project_root=project, limit=500)
                for path in ambiguous_paths[:100]
            ] or "none observed",
            "ambiguous_preexisting_path_count": len(ambiguous_paths),
            "patch": {**empty_patch, "status": "withheld_dirty_baseline"},
            "attribution": "unknown because the baseline worktree was dirty",
        }
    if not after.status:
        return {"files": [], "patch": {**empty_patch, "status": "no_changes",
                                           "complete": True}, "attribution": "run"}
    if before.git_root != after.git_root or before.commit != after.commit:
        return {"files": files, "patch": {**empty_patch, "status": "snapshot_mismatch"},
                "attribution": "unknown because the Git snapshot changed"}
    if any(Path(path).name.lower() == ".gitignore" for _, path in after.status):
        return {"files": files, "patch": {**empty_patch, "status": "ignore_rules_changed"},
                "attribution": "run; ignored-file scope may have changed"}

    diff_args = ["git", "diff", "--binary", "--no-ext-diff", before.commit or "HEAD", "--"]
    diff_args.extend(f":(exclude,literal){path}/" for path in before.excluded_paths)
    diff = subprocess.run(
        diff_args,
        cwd=before.git_root, capture_output=True, timeout=20, check=False,
    )
    if diff.returncode:
        return {"files": files, "patch": {**empty_patch, "status": "diff_unavailable"},
                "attribution": "run; patch generation failed"}
    patch = bytearray(diff.stdout)
    incomplete = False
    for code, relative in after.status:
        if code != "??":
            continue
        raw_file_path = before.git_root / relative
        if raw_file_path.is_symlink():
            incomplete = True
            break
        file_path = raw_file_path.resolve()
        if not file_path.is_relative_to(before.git_root):
            incomplete = True
            break
        try:
            content = file_path.read_bytes()
            if len(content) > _MAX_UNTRACKED_BYTES or b"\0" in content:
                incomplete = True
                break
            lines = content.decode("utf-8").splitlines(keepends=True)
        except (OSError, UnicodeError):
            incomplete = True
            break
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        patch.extend("".join(difflib.unified_diff(
            [], lines, fromfile="/dev/null", tofile=f"b/{relative}", lineterm="\n"
        )).encode("utf-8"))
    if incomplete:
        return {"files": files, "patch": {**empty_patch, "status": "incomplete_patch"},
                "attribution": "run; patch omitted because an untracked file was not safely representable"}
    if not patch:
        return {"files": files, "patch": {**empty_patch, "status": "no_text_patch",
                                             "complete": True}, "attribution": "run"}
    if len(patch) > _MAX_PATCH_BYTES:
        return {"files": files, "patch": {**empty_patch, "status": "patch_too_large"},
                "attribution": "run; patch exceeds the report size limit"}
    safe_patch = sanitize_text(patch.decode("utf-8", "replace"), project_root=project,
                               limit=_MAX_PATCH_BYTES + 1).encode("utf-8")
    redacted = safe_patch != bytes(patch)
    patch_hash = hashlib.sha256(safe_patch).hexdigest()
    return {
        "files": files,
        "patch": {"status": "redacted" if redacted else "available",
                  "path": "patch.diff", "sha256": patch_hash, "redacted": redacted,
                  "complete": not redacted, "directly_applicable": not redacted,
                  "_patch_bytes": safe_patch},
        "attribution": "run",
    }


def _safe_record(record: dict[str, Any], project: Path | None) -> dict[str, Any]:
    integrity = record.get("integrity") if isinstance(record.get("integrity"), dict) else {}
    replay = record.get("replay") if isinstance(record.get("replay"), dict) else {}
    baseline = record.get("baseline_compare") if isinstance(record.get("baseline_compare"), dict) else {}
    return {
        "command": sanitize_text(record.get("command", "unknown"), project_root=project, limit=500),
        "scope": sanitize_text(record.get("scope", "unknown"), limit=80),
        "coverage": sanitize_text(record.get("coverage", "unknown"), limit=80),
        "evidence_status": sanitize_text(record.get("evidence_status", "unknown"), limit=80),
        "exit_code": _number(record.get("exit_code")),
        "duration_ms": _number(record.get("duration_ms")),
        "executed_tests": _number(record.get("executed_tests")),
        "passed": _number(record.get("passed")),
        "failed": _number(record.get("failed")),
        "failed_ids": [sanitize_text(item, project_root=project, limit=200)
                       for item in record.get("failed_ids", [])[:30]]
        if isinstance(record.get("failed_ids"), list) else [],
        "unverified_scope": sanitize_text(record.get("unverified_scope", "unknown"),
                                           project_root=project, limit=500),
        "invalid_reason": sanitize_text(record.get("invalid_reason", ""), project_root=project,
                                         limit=500),
        "integrity": {
            key: integrity.get(key) if isinstance(integrity.get(key), bool) else None
            for key in ("test_files_unchanged", "config_unchanged", "no_scope_narrowing",
                        "ran_after_last_edit", "nonce_verified", "replay_verified")
        },
        "replay": {
            "attempted": replay.get("attempted") if isinstance(replay.get("attempted"), bool) else None,
            "verified": replay.get("verified") if isinstance(replay.get("verified"), bool) else None,
            "exit_code": _number(replay.get("exit_code")),
            "duration_ms": _number(replay.get("duration_ms")),
        },
        "new_failures": [sanitize_text(item, project_root=project, limit=200)
                         for item in baseline.get("new_failures", [])[:30]]
        if isinstance(baseline.get("new_failures"), list) else [],
        "regression_detected": (record.get("regression_detected")
                                 if isinstance(record.get("regression_detected"), bool) else None),
    }


def _execution_log_summary(
    workspace: Path, project: Path | None, expected_agent_id: Any, started_at: str,
) -> dict[str, Any]:
    """Read only bounded, allowlisted fields from Runtime's persisted execution log."""
    log_path = workspace / ".state" / "execution_log.json"
    try:
        if not log_path.resolve().is_relative_to(workspace.resolve()):
            return {"status": "unavailable", "reason": "path_outside_workspace"}
        if not log_path.is_file():
            return {"status": "missing", "step_count": None, "events": []}
        if log_path.stat().st_size > 2 * 1024 * 1024:
            return {"status": "unavailable", "reason": "size_limit_exceeded"}
        raw = json.loads(log_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"status": "invalid", "step_count": None, "events": []}
        if (expected_agent_id and raw.get("agent_id")
                and raw.get("agent_id") != expected_agent_id):
            return {"status": "stale", "step_count": None, "events": []}
        created_at = raw.get("created_at") if isinstance(raw, dict) else None
        if isinstance(created_at, str):
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            if created < started:
                return {"status": "stale", "step_count": None, "events": []}
        steps = raw.get("steps") if isinstance(raw, dict) else None
        if not isinstance(steps, list):
            return {"status": "invalid", "step_count": None, "events": []}
        events = []
        for item in steps[-150:]:
            if not isinstance(item, dict):
                continue
            events.append({
                "step_id": _number(item.get("step_id")),
                "phase": sanitize_text(item.get("phase", "unknown"), limit=80),
                "action": sanitize_text(item.get("action", "unknown"), limit=120),
                "tool_name": sanitize_text(item.get("tool_name", ""), limit=80) or None,
                "success": item.get("success") if isinstance(item.get("success"), bool) else None,
                "duration_ms": _number(item.get("duration_ms")),
                "error": sanitize_text(item.get("error", ""), project_root=project, limit=300) or None,
            })
        return {"status": "available", "step_count": len(steps),
                "truncated": len(steps) > len(events), "events": events}
    except (OSError, RuntimeError, UnicodeError, ValueError, TypeError):
        return {"status": "unavailable", "reason": "read_or_parse_failed"}


def _verification(records: list[dict[str, Any]], objective: list[dict[str, Any]],
                  status: str, ledger: dict[str, Any]) -> dict[str, Any]:
    valid = [record for record in records if record["evidence_status"] == "valid"]
    latest = valid[-1] if valid else None
    if any(record["evidence_status"] in ("polluted", "stale") for record in records):
        evidence_status = "polluted_or_stale_evidence_present"
    elif latest:
        evidence_status = (
            "valid_complete"
            if latest["scope"] == "full" and latest["coverage"] == "full"
            else "valid_incomplete"
        )
    elif objective and all(check["passed"] is True for check in objective):
        evidence_status = "objective_checks_passed"
    else:
        evidence_status = "no_usable_evidence"
    known = [record for record in records if record["executed_tests"] is not None]
    return {
        "evidence_status": evidence_status,
        "record_count": len(records),
        "valid_record_count": len(valid),
        "executed_tests": sum(record["executed_tests"] or 0 for record in known) if known else None,
        "passed": sum(record["passed"] or 0 for record in known) if known else None,
        "failed": sum(record["failed"] or 0 for record in known) if known else None,
        "latest_valid_scope": latest["scope"] if latest else "unknown",
        "latest_valid_coverage": latest["coverage"] if latest else "unknown",
        "ran_after_last_edit": latest["integrity"]["ran_after_last_edit"] if latest else None,
        "records": records,
        "objective_verification_required": ledger.get("objective_verification_required")
        if isinstance(ledger.get("objective_verification_required"), bool) else None,
        "terminal_status": status,
    }


def _risks(status: str, decision: dict[str, Any], tests: dict[str, Any],
           changes: dict[str, Any]) -> list[str]:
    risks = ["外部验收未运行；报告不代表隐藏测试或用户验收通过。"]
    if status in ("candidate", "timeout", "cancelled", "failed"):
        risks.append(f"Runtime 终态为 {status}；当前结果不能表述为完整成功。")
    if tests["evidence_status"] in ("no_usable_evidence", "polluted_or_stale_evidence_present"):
        risks.append("没有可用的最新测试证据，或证据已失效；测试结论标记为 unknown/unverified。")
    unverified = decision.get("unverified_scope")
    if unverified:
        risks.append("未验证范围：" + sanitize_text(unverified, limit=500))
    if changes.get("patch", {}).get("status") == "withheld_dirty_baseline":
        risks.append("运行前工作树已包含修改，无法可靠区分本次来源，未导出 patch。")
    elif changes.get("patch", {}).get("status") == "redacted":
        risks.append("patch 含脱敏内容；文件仅供审阅，不可直接应用。")
    elif changes.get("patch", {}).get("status") in (
        "unavailable", "snapshot_mismatch", "incomplete_patch", "ignore_rules_changed",
        "patch_too_large", "diff_unavailable", "no_changes", "no_text_patch",
    ):
        risks.append("patch 未导出，状态为 " + changes["patch"]["status"] + "。")
    return risks


def _next_action(status: str, decision: dict[str, Any]) -> str:
    if status == "completed":
        return "审阅报告中的变更与已验证范围；按项目要求执行外部验收。"
    if status == "candidate":
        return "补充未验证范围的测试，再由 Runtime 基于新证据重新判定。"
    if status == "timeout":
        return "检查部分变更和预算耗尽原因；补足验证后重新运行。"
    if status == "cancelled":
        return "检查取消前产生的变更与证据，再决定恢复任务或补充验证。"
    if status == "failed":
        return "根据失败原因和可归因变更定位问题；修复后重新验证。"
    return "核对 Runtime 终态、报告字段和未验证范围后再采取下一步。"


def _render_markdown(report: dict[str, Any]) -> str:
    run, terminal = report["run"], report["terminal"]
    changes, verification = report["changes"], report["verification"]
    lines = [
        "# 可信修复报告", "", f"- 运行 ID：`{_md_inline(run['id'])}`",
        f"- 最终状态：**{_md_inline(terminal['status'])}**",
        f"- 判定依据：{_md_inline(terminal['reason'])}",
        f"- 终态事实：{len(terminal['facts'])} 条",
        f"- 任务：{_md_inline(run['task'])}", f"- 耗时：{run['elapsed_seconds']} 秒",
        f"- 变更范围：{_md_inline(changes.get('scope', 'unknown'))}",
        f"- 修改文件：{_markdown_files(changes.get('files'))}",
        f"- Patch：{_md_inline(changes['patch']['status'])}；路径："
        f"{_md_inline(changes['patch']['path'] or 'unknown')}；SHA-256："
        f"{_md_inline(changes['patch']['sha256'] or 'unknown')}",
        f"- 测试证据：{verification['evidence_status']}",
        f"- 测试执行/通过/失败：{_show(verification['tests']['executed_tests'])} / "
        f"{_show(verification['tests']['passed'])} / {_show(verification['tests']['failed'])}",
        f"- 外部验收：{verification['external_acceptance']}",
        f"- Token：输入 {_show(report['usage']['input_tokens'])}，输出 "
        f"{_show(report['usage']['output_tokens'])}，总计 {_show(report['usage']['total_tokens'])}",
        f"- 执行日志：{report['run']['execution_log']['status']}，"
        f"{_show(report['run']['execution_log'].get('step_count'))} 条日志事件",
        "", "## 测试明细", "",
    ]
    records = verification["tests"]["records"]
    if records:
        for item in records:
            lines.append(
                f"- `{_md_inline(item['command'])}`：{_md_inline(item['evidence_status'])}，"
                f"范围 {_md_inline(item['scope'])}，覆盖 {_md_inline(item['coverage'])}，"
                f"退出码 {_show(item['exit_code'])}，"
                f"执行/通过/失败 {_show(item['executed_tests'])}/"
                f"{_show(item['passed'])}/{_show(item['failed'])}"
            )
    else:
        lines.append("- unknown：没有采集到测试记录。")
    objective = report["verification"]["objective_checks"]
    if isinstance(objective, list):
        lines.extend(["", "## 无 pytest 客观验证", ""])
        lines.extend(
            f"- {_md_inline(check['name'])}："
            f"{'passed' if check['passed'] is True else 'failed' if check['passed'] is False else 'unknown'}"
            + (f"；{_md_inline(check['reason'])}" if check["reason"] else "")
            for check in objective
        )
    ambiguous = changes.get("ambiguous_preexisting_paths")
    if isinstance(ambiguous, list):
        count = changes.get("ambiguous_preexisting_path_count", len(ambiguous))
        lines.extend(["", "## 来源不明的运行前修改", "",
                      f"- {count} 个运行前已存在的脏路径未归入本次修改清单。"])
    lines.extend(["", "## 风险与下一步", ""])
    lines.extend(f"- {_md_inline(risk)}" for risk in report["risks"])
    lines.extend(["", "**下一步：** " + _md_inline(report["next_action"]), ""])
    return "\n".join(lines)


def _markdown_files(value: Any) -> str:
    if value == "unknown":
        return "unknown"
    if not value:
        return "无"
    return ", ".join(f"`{_md_inline(item['path'])}` "
                      f"({_md_inline(item['status'])}, {_md_inline(item['attribution'])})"
                      for item in value)


def _md_inline(value: Any) -> str:
    return (str(value).replace("\\", "\\\\").replace("`", "\\`")
            .replace("<", "&lt;").replace(">", "&gt;")
            .replace("\r", " ").replace("\n", " "))


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _show(value: Any) -> str:
    return "unknown" if value is None else str(value)


def _atomic_write(path: Path, content: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
