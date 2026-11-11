"""证据账本与统一终态判定（M1 / P0-1）。

每次真实测试运行形成一条 :class:`EvidenceRecord`，由 Runtime 写入
:class:`CompletionLedger`。终态只由纯函数 :func:`decide_terminal` 计算：

    completed  完整范围 + 覆盖可证明 + 有效全绿证据
    candidate  修改后有效的局部通过证据，且已执行可信范围内无新增失败
    failed     不可恢复错误（Runtime 显式标记）
    timeout    预算耗尽（沿用既有超时语义）

completed 与 candidate 的 scope 条件互斥；polluted / stale 证据一律不参与判定。
P0-1 只做影子对比，不改变 Runtime 现有终态。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

# ── 证据状态 ────────────────────────────────────────────────────
EVIDENCE_VALID = "valid"
EVIDENCE_POLLUTED = "polluted"
EVIDENCE_STALE = "stale"

# ── 范围 / 覆盖 ─────────────────────────────────────────────────
SCOPE_FULL = "full"
SCOPE_TARGETED = "targeted"
SCOPE_SUBSET = "subset"
SCOPE_UNKNOWN = "unknown"

COVERAGE_FULL = "full"
COVERAGE_PARTIAL = "partial"
COVERAGE_UNKNOWN = "unknown"

# ── 判定结果 ────────────────────────────────────────────────────
TERMINAL_COMPLETED = "completed"
TERMINAL_CANDIDATE = "candidate"
TERMINAL_FAILED = "failed"
TERMINAL_TIMEOUT = "timeout"
TERMINAL_CONTINUE = "continue"  # 非终态：证据不足，继续循环


@dataclass
class BaselineCompare:
    """本次运行与同范围编辑前基线的对比（基线不可用时 available=False）。"""

    available: bool = False
    new_failures: list[str] = field(default_factory=list)
    fixed_failures: list[str] = field(default_factory=list)
    preexisting_failures: list[str] = field(default_factory=list)


@dataclass
class Integrity:
    """运行完整性。None 表示该检查不适用（如非 pytest 运行器没有 nonce）。"""

    test_files_unchanged: bool = True
    config_unchanged: bool = True
    no_scope_narrowing: bool = True
    ran_after_last_edit: bool = True
    nonce_verified: bool | None = None
    replay_verified: bool | None = None

    def ok(self) -> bool:
        required = (self.test_files_unchanged, self.config_unchanged,
                    self.no_scope_narrowing, self.ran_after_last_edit)
        return all(required)

    def trusted(self) -> bool:
        """P0-2：可用于 completed / candidate 的证据强度。

        nonce 对不支持的运行器为 None（不阻断）；一旦有值就必须为 True。
        回放是通用的独立复核，必须实际通过。
        """
        return self.ok() and self.nonce_verified is not False \
            and self.replay_verified is True


@dataclass
class ReplayResult:
    """一次独立进程回放的结果与成本。"""

    attempted: bool = False
    verified: bool = False
    command: str = ""
    nonce: str = ""
    exit_code: int | None = None
    passed: int = 0
    failed: int = 0
    duration_ms: int = 0
    workspace_unchanged: bool = False
    environment_unchanged: bool = False
    reason: str = ""


@dataclass
class EvidenceRecord:
    """一条结构化测试运行证据。"""

    run_id: str
    step: int
    command: str
    scope: str = SCOPE_UNKNOWN
    coverage: str = COVERAGE_UNKNOWN
    evidence_status: str = EVIDENCE_VALID
    workspace_fingerprint: str = ""
    environment_fingerprint: str = ""
    exit_code: int = 0
    passed: int = 0
    failed: int = 0
    failed_ids: list[str] = field(default_factory=list)
    executed_tests: int = 0
    baseline_compare: BaselineCompare = field(default_factory=BaselineCompare)
    integrity: Integrity = field(default_factory=Integrity)
    unverified_scope: str = ""
    invalid_reason: str = ""
    regression_detected: bool = False
    nonce: str = ""
    replay: ReplayResult = field(default_factory=ReplayResult)
    sequence: int = -1

    @property
    def valid(self) -> bool:
        return self.evidence_status == EVIDENCE_VALID

    @property
    def green(self) -> bool:
        return self.exit_code == 0 and self.passed > 0 and self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TerminalDecision:
    """终态判定（kind 为 completed/candidate/failed/timeout/continue 之一）。"""

    kind: str
    reason: str
    evidence_id: str | None = None
    unverified_scope: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompletionLedger:
    """证据账本：Runtime 写入，判定函数只读。

    修改工作区会把此前所有 valid 证据标记为 stale（运行后代码已变，旧结果
    不再描述当前工作区）；测试文件 / 配置被篡改或守卫失效则标记为 polluted。
    两种状态都不得用于 completed 或 candidate。
    """

    records: list[EvidenceRecord] = field(default_factory=list)
    ever_modified: bool = False
    last_edit_step: int = -1
    budget_exhausted: bool = False
    fatal_error: str | None = None
    replay_attempts: int = 0
    replay_total_ms: int = 0

    def record(self, record: EvidenceRecord) -> EvidenceRecord:
        record.sequence = len(self.records)
        self.records.append(record)
        return record

    def record_modification(self, step: int) -> None:
        """记录一次真实代码修改：旧证据全部失效为 stale。"""
        self.ever_modified = True
        self.last_edit_step = max(self.last_edit_step, step)
        self.invalidate(EVIDENCE_STALE, f"workspace changed after the run (step {step})")

    def mark_polluted(self, reason: str) -> None:
        """测试文件 / 范围配置被改动或守卫失效：已有证据作废为 polluted。"""
        self.invalidate(EVIDENCE_POLLUTED, reason)

    def invalidate(self, status: str, reason: str) -> None:
        for r in self.records:
            if r.valid:
                r.evidence_status = status
                r.invalid_reason = reason
                r.integrity.ran_after_last_edit = False

    def mark_budget_exhausted(self) -> None:
        self.budget_exhausted = True

    def mark_fatal(self, error: str) -> None:
        self.fatal_error = error

    def usable(self) -> list[EvidenceRecord]:
        return [r for r in self.records if r.valid]

    def summary(self) -> dict[str, Any]:
        return {
            "ever_modified": self.ever_modified,
            "last_edit_step": self.last_edit_step,
            "budget_exhausted": self.budget_exhausted,
            "fatal_error": self.fatal_error,
            "replay_attempts": self.replay_attempts,
            "replay_total_ms": self.replay_total_ms,
            "usable_records": len(self.usable()),
            "records": [r.to_dict() for r in self.records],
        }


def fingerprint_digest(fingerprint: dict | None) -> str:
    """工作区指纹的稳定摘要；缺失时返回空串（不可计算 ≠ 通过）。"""
    if not fingerprint:
        return ""
    h = hashlib.sha256()
    for key in sorted(fingerprint, key=str):
        h.update(f"{key}\0{fingerprint[key]!r}\0".encode("utf-8", "replace"))
    return h.hexdigest()


def command_key(command: str) -> str:
    """测试范围身份：与 Runtime 的 unresolved_regressions 同一口径。

    Runtime 用"命令 token 排序集合"区分不同测试范围；账本按同一 key 取最新记录，
    否则一个范围的绿会覆盖另一个范围未解决的回归。
    """
    return " ".join(sorted((command or "").lower().split()))


def _latest_per_scope(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    latest: dict[tuple[str, str], EvidenceRecord] = {}
    for r in records:  # records 按写入顺序；同 scope+command 才互相覆盖
        latest[(r.scope, command_key(r.command))] = r
    return list(latest.values())


def decide_terminal(ledger: CompletionLedger) -> TerminalDecision:
    """按互斥规则计算终态。纯函数，不修改账本。"""
    unverified = next((r.unverified_scope for r in reversed(ledger.usable())
                       if r.unverified_scope), "full suite")

    # 预算耗尽沿用既有超时语义；不可恢复错误 fail-closed，优先于任何旧证据。
    if ledger.budget_exhausted:
        return TerminalDecision(TERMINAL_TIMEOUT, "budget exhausted",
                                unverified_scope=unverified)
    if ledger.fatal_error:
        return TerminalDecision(TERMINAL_FAILED,
                                f"unrecoverable: {ledger.fatal_error}",
                                unverified_scope=unverified)

    usable = ledger.usable()
    if not usable:
        return TerminalDecision(TERMINAL_CONTINUE, "no usable evidence",
                                unverified_scope=unverified)

    # 任一"范围"最新记录里的新增失败/回归都阻止成功终态 —— 不能被另一个范围的
    # 绿运行覆盖（Runtime 的 unresolved_regressions 同样按命令范围保留）。
    latest = _latest_per_scope(usable)
    new_failures = [fid for r in latest for fid in r.baseline_compare.new_failures]
    if new_failures:
        return TerminalDecision(
            TERMINAL_CONTINUE,
            f"new failures observed in verified scope: {', '.join(new_failures[:5])}",
            unverified_scope=unverified)
    if any(r.regression_detected for r in latest):
        return TerminalDecision(
            TERMINAL_CONTINUE, "regression observed in verified scope",
            unverified_scope=unverified)

    # completed：最新一次完整范围运行必须全绿、覆盖可证明、完整性通过。
    full_runs = [r for r in usable if r.scope == SCOPE_FULL]
    if full_runs:
        latest_full = full_runs[-1]
        if (latest_full.coverage == COVERAGE_FULL
                and latest_full.green
                and latest_full.integrity.trusted()
                and not latest_full.baseline_compare.new_failures):
            return TerminalDecision(TERMINAL_COMPLETED,
                                    f"full-scope green run {latest_full.run_id}",
                                    evidence_id=latest_full.run_id)

    if not ledger.ever_modified:
        return TerminalDecision(TERMINAL_CONTINUE,
                                "no code modification, no full-scope green",
                                unverified_scope=unverified)

    # candidate：修改后仍有**可验证**的局部通过证据，且已执行可信范围无新增失败。
    greens = [r for r in latest
              if r.green and r.integrity.trusted()
              and not (r.scope == SCOPE_FULL and r.coverage == COVERAGE_FULL)]
    if greens:
        r = greens[-1]
        return TerminalDecision(
            TERMINAL_CANDIDATE,
            f"post-edit local green run {r.run_id}; verification incomplete",
            evidence_id=r.run_id,
            unverified_scope=r.unverified_scope or unverified)
    return TerminalDecision(TERMINAL_CONTINUE, "no post-edit green evidence",
                            unverified_scope=unverified)


def shadow_diff(old_kind: str, decision: TerminalDecision, *, point: str,
                step: int) -> dict[str, Any]:
    """新旧判定的影子对比记录（只审计，不参与控制）。"""
    return {
        "point": point,
        "step": step,
        "old": old_kind,
        "new": decision.kind,
        "agree": old_kind == decision.kind,
        "reason": decision.reason,
        "evidence_id": decision.evidence_id,
        "unverified_scope": decision.unverified_scope,
    }
