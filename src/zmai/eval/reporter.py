"""ScoreReporter — 生成评测报告（JSON / Markdown / CSV）。

支持输出格式:
  - json:     完整结构化数据
  - markdown: README 友好表格
  - csv:      电子表格兼容

使用方式:
    reporter = ScoreReporter()
    reporter.to_json(stats, path="report.json")
    reporter.to_markdown(stats, path="BENCHMARK.md")
    reporter.to_csv(stats, path="results.csv")
    print(reporter.format_console(stats))
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zmai.eval.collector import BenchmarkStats

logger = logging.getLogger("zmai.eval.reporter")


class ScoreReporter:
    """评测报告生成器。"""

    # ═══════════════════════════════════════════════════════════════
    # JSON
    # ═══════════════════════════════════════════════════════════════

    def to_json(self, stats: BenchmarkStats, path: str | Path | None = None) -> str:
        """输出 JSON 格式报告。"""
        data = self._build_report_data(stats)
        text = json.dumps(data, indent=2, ensure_ascii=False)
        if path:
            Path(path).write_text(text, encoding="utf-8")
            logger.info("JSON 报告已保存: %s", path)
        return text

    # ═══════════════════════════════════════════════════════════════
    # Markdown
    # ═══════════════════════════════════════════════════════════════

    def to_markdown(self, stats: BenchmarkStats, path: str | Path | None = None) -> str:
        """输出 Markdown 格式报告（README 表格）。"""
        lines: list[str] = [
            "# ZMAI Benchmark Report",
            "",
            f"> 生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "## Summary",
            "",
        ]

        # Summary table
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Tasks | {stats.total} |")
        lines.append(f"| ✅ Passed | {stats.passed} |")
        lines.append(f"| ❌ Failed | {stats.failed} |")
        lines.append(f"| ⏰ Timed Out | {stats.timedout} |")
        lines.append(f"| 💥 Errors | {stats.errors} |")
        lines.append(f"| **Success Rate** | **{stats.success_rate:.1f}%** |")
        lines.append(f"| Pass@1 | {stats.pass_at_1:.1f}% |")
        lines.append("")

        # Latency table
        lines.append("## Latency")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Min | {stats.latency_min:.1f}s |")
        lines.append(f"| Max | {stats.latency_max:.1f}s |")
        lines.append(f"| Avg | {stats.latency_avg:.1f}s |")
        lines.append(f"| Median | {stats.latency_median:.1f}s |")
        lines.append(f"| Total | {stats.total_duration:.1f}s |")
        lines.append("")

        # Token usage table
        lines.append("## Token Usage")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Input | {stats.total_input_tokens:,} |")
        lines.append(f"| Total Output | {stats.total_output_tokens:,} |")
        lines.append(f"| Avg Input/Step | {stats.avg_input_tokens:.1f} |")
        lines.append(f"| Avg Output/Step | {stats.avg_output_tokens:.1f} |")
        if stats.estimated_cost_usd > 0:
            lines.append(f"| Estimated Cost | ${stats.estimated_cost_usd:.4f} |")
        lines.append("")

        # Per-task table
        lines.append("## Per-Task Results")
        lines.append("")
        lines.append("| Task ID | Status | Duration | Steps | Error |")
        lines.append("|---------|--------|----------|-------|-------|")
        for task in stats.per_task:
            icon = {"PASS": "✅", "FAIL": "❌", "TIMEOUT": "⏰", "ERROR": "💥"}.get(
                task["status"], "?"
            )
            err = (task["error"] or "")[:40]
            lines.append(
                f"| {task['task_id']} | {icon} {task['status']} "
                f"| {task['duration_s']:.1f}s | {task['steps']} | {err} |"
            )
        lines.append("")

        text = "\n".join(lines)
        if path:
            Path(path).write_text(text, encoding="utf-8")
            logger.info("Markdown 报告已保存: %s", path)
        return text

    # ═══════════════════════════════════════════════════════════════
    # CSV
    # ═══════════════════════════════════════════════════════════════

    def to_csv(self, stats: BenchmarkStats, path: str | Path | None = None) -> str:
        """输出 CSV 格式报告。"""
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(["Task ID", "Status", "Duration (s)", "Steps", "Error", "Agent Status"])

        # Rows
        for task in stats.per_task:
            writer.writerow([
                task["task_id"],
                task["status"],
                f"{task['duration_s']:.2f}",
                task["steps"],
                task.get("error", "") or "",
                task.get("agent_status", ""),
            ])

        # Summary rows
        writer.writerow([])
        writer.writerow(["Metric", "Value"])
        writer.writerow(["Total Tasks", stats.total])
        writer.writerow(["Success Rate", f"{stats.success_rate:.1f}%"])
        writer.writerow(["Pass@1", f"{stats.pass_at_1:.1f}%"])
        writer.writerow(["Avg Latency", f"{stats.latency_avg:.2f}s"])
        writer.writerow(["Total Input Tokens", stats.total_input_tokens])
        writer.writerow(["Total Output Tokens", stats.total_output_tokens])
        writer.writerow(["Estimated Cost (USD)", f"{stats.estimated_cost_usd:.4f}"])

        text = output.getvalue()
        if path:
            Path(path).write_text(text, encoding="utf-8")
            logger.info("CSV 报告已保存: %s", path)
        return text

    # ═══════════════════════════════════════════════════════════════
    # Console
    # ═══════════════════════════════════════════════════════════════

    def format_console(self, stats: BenchmarkStats) -> str:
        """终端可读的 ASCII 报告。"""
        lines = [
            "=" * 64,
            "  ZMAI Benchmark Report",
            "=" * 64,
            "",
            f"  Success Rate:  {stats.success_rate:.1f}%",
            f"  Pass@1:        {stats.pass_at_1:.1f}%",
            f"  Total Tasks:   {stats.total}",
            f"  Passed:        {stats.passed}",
            f"  Failed:        {stats.failed}",
            f"  Timed Out:     {stats.timedout}",
            f"  Errors:        {stats.errors}",
            "",
            "  ── Latency ──────────────────────────────────────",
            f"  Min:           {stats.latency_min:.1f}s",
            f"  Max:           {stats.latency_max:.1f}s",
            f"  Avg:           {stats.latency_avg:.1f}s",
            f"  Median:        {stats.latency_median:.1f}s",
            f"  Total:         {stats.total_duration:.1f}s",
            "",
            "  ── Token Usage ──────────────────────────────────",
            f"  Total Input:   {stats.total_input_tokens:,}",
            f"  Total Output:  {stats.total_output_tokens:,}",
            f"  Avg Input:     {stats.avg_input_tokens:.1f}",
            f"  Avg Output:    {stats.avg_output_tokens:.1f}",
        ]
        if stats.estimated_cost_usd > 0:
            lines.extend([
                "",
                "  ── Cost ─────────────────────────────────────────",
                f"  Estimated:     ${stats.estimated_cost_usd:.4f} USD",
            ])

        lines.extend([
            "",
            "  ── Per-Task Results ────────────────────────────",
            f"  {'Task ID':<35} {'Result':<10} {'Time':<8} {'Steps':<6}",
            "  " + "-" * 59,
        ])
        for task in stats.per_task:
            icon = {"PASS": "✅", "FAIL": "❌", "TIMEOUT": "⏰", "ERROR": "💥"}.get(
                task["status"], "?"
            )
            lines.append(
                f"  {task['task_id']:<35} {icon}{task['status']:<7} "
                f"{task['duration_s']:<8.1f} {task['steps']:<6}"
            )

        lines.append("")
        lines.append("=" * 64)
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════
    # 内部
    # ═══════════════════════════════════════════════════════════════

    def _build_report_data(self, stats: BenchmarkStats) -> dict[str, Any]:
        """构建完整报告数据。"""
        return {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "summary": {
                "total": stats.total,
                "passed": stats.passed,
                "failed": stats.failed,
                "timedout": stats.timedout,
                "errors": stats.errors,
                "success_rate": round(stats.success_rate, 1),
                "pass_at_1": round(stats.pass_at_1, 1),
            },
            "latency": {
                "min_s": round(stats.latency_min, 2),
                "max_s": round(stats.latency_max, 2),
                "avg_s": round(stats.latency_avg, 2),
                "median_s": round(stats.latency_median, 2),
                "total_s": round(stats.total_duration, 2),
            },
            "token_usage": {
                "total_input": stats.total_input_tokens,
                "total_output": stats.total_output_tokens,
                "avg_input": round(stats.avg_input_tokens, 1),
                "avg_output": round(stats.avg_output_tokens, 1),
            },
            "estimated_cost_usd": round(stats.estimated_cost_usd, 4),
            "model_used": stats.model_used or "",
            "per_task": stats.per_task,
        }
