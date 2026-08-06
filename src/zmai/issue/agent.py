"""IssueAgent — Issue 驱动的修复工作流。

工作流:
  1. Parse     — 解析 Issue 输入 (URL / 文件)
  2. Analyze   — 读取仓库/项目，分析问题根因
  3. Plan      — 生成修复计划 (--plan 在此结束)
  4. Modify    — 执行修改 (--dry-run 跳过)
  5. Test      — 运行测试
  6. Verify    — 检查测试结果 + diff
  7. Diff      — 生成 git diff
  8. Complete  — 输出最终摘要

使用方式:
    agent = IssueAgent()
    result = agent.run("https://github.com/owner/repo/issues/123")
    result = agent.run("./issue.md", plan_only=True)
    result = agent.run("https://github.com/owner/repo/issues/123", dry_run=True)
"""

from __future__ import annotations

import difflib
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zmai.config import Config
from zmai.issue import IssueDescription, IssueParser

logger = logging.getLogger("zmai.issue.agent")


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

_WORKFLOW_STEPS = [
    "parse",
    "analyze",
    "plan",
    "modify",
    "test",
    "verify",
    "diff",
    "complete",
]


# ═══════════════════════════════════════════════════════════════
# IssueResult
# ═══════════════════════════════════════════════════════════════


@dataclass
class IssueResult:
    """Issue 工作流的完整结果。"""

    issue: IssueDescription
    workflow_status: str  # "parsed" | "analyzed" | "planned" | "modified" | "tested" | "verified" | "completed"
    plan: str = ""
    diff: str = ""
    test_output: str = ""
    verification: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue.to_dict(),
            "workflow_status": self.workflow_status,
            "plan": self.plan[:1000] if self.plan else "",
            "diff": self.diff,
            "test_output": self.test_output[:1000] if self.test_output else "",
            "verification": self.verification[:500] if self.verification else "",
            "error": self.error[:500] if self.error else "",
            "duration_seconds": round(self.duration_seconds, 2),
            "summary": self.summary[:1000] if self.summary else "",
        }


# ═══════════════════════════════════════════════════════════════
# IssueAgent
# ═══════════════════════════════════════════════════════════════


class IssueAgent:
    """Issue 驱动的自动修复 Agent。

    Args:
        work_dir: 工作目录（默认临时目录）。
        max_steps: Agent 最大步数。
        backend: Backend 名称。
    """

    def __init__(
        self,
        work_dir: str | Path | None = None,
        max_steps: int = 50,
        backend: str | None = None,
    ) -> None:
        self._work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="zmai_issue_"))
        self._max_steps = max_steps
        self._backend = backend or os.environ.get("ZMAI_ISSUE_BACKEND", "")
        self._parser = IssueParser()
        self._work_dir.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════════

    def run(
        self,
        input_str: str,
        *,
        plan_only: bool = False,
        dry_run: bool = False,
    ) -> IssueResult:
        """运行完整 Issue 工作流。

        Args:
            input_str: GitHub Issue URL 或本地文件路径。
            plan_only: 仅生成计划，不执行修改。
            dry_run: 仅显示计划 + 预期修改，不应用。

        Returns:
            IssueResult 包含工作流各阶段输出。
        """
        start = time.time()
        result = IssueResult(
            issue=IssueDescription(title="", body="", source=""),
            workflow_status="parse",
        )

        try:
            # ── 1. Parse ────────────────────────────────────────
            result.issue = self._parser.parse(input_str)
            result.workflow_status = "parsed"
            logger.info("Issue 已解析: %s", result.issue.title)

            # ── 2. Analyze ──────────────────────────────────────
            analysis = self._analyze(result.issue)
            result.workflow_status = "analyzed"
            logger.info("分析完成")

            # ── 3. Plan ─────────────────────────────────────────
            plan = self._generate_plan(result.issue, analysis)
            result.plan = plan
            result.workflow_status = "planned"

            if plan_only:
                result.summary = plan
                result.duration_seconds = time.time() - start
                return result

            if dry_run:
                result.diff = self._simulate_diff(result.issue, plan)
                result.summary = f"[DRY RUN] 计划已生成，未应用修改。\n\n{plan}"
                result.workflow_status = "completed"
                result.duration_seconds = time.time() - start
                return result

            # ── 4. Modify ───────────────────────────────────────
            modify_result = self._modify(result.issue, plan)
            result.workflow_status = "modified"
            logger.info("修改完成")

            # ── 5. Test ─────────────────────────────────────────
            test_output = self._run_tests(result.issue)
            result.test_output = test_output
            result.workflow_status = "tested"
            logger.info("测试完成")

            # ── 6. Verify ───────────────────────────────────────
            verification = self._verify(result.issue, modify_result, test_output)
            result.verification = verification
            result.workflow_status = "verified"
            logger.info("验证完成")

            # ── 7. Diff ─────────────────────────────────────────
            result.diff = self._generate_diff(result.issue)
            result.workflow_status = "diff_generated"

            # ── 8. Complete ─────────────────────────────────────
            result.summary = self._build_summary(result)
            result.workflow_status = "completed"

        except Exception as e:
            logger.exception("Issue 工作流失败")
            result.error = str(e)[:500]
            result.summary = f"工作流在执行到 [{result.workflow_status}] 阶段失败: {e}"

        result.duration_seconds = time.time() - start
        return result

    # ═══════════════════════════════════════════════════════════════
    # 工作流阶段
    # ═══════════════════════════════════════════════════════════════

    def _analyze(self, issue: IssueDescription) -> str:
        """分析 Issue：读取相关文件，理解问题。"""
        task = issue.format_for_agent()
        lines: list[str] = [
            "## 问题分析",
            "",
            f"Issue: {issue.title}",
            "",
        ]

        if issue.source == "github" and issue.full_name:
            lines.append(f"仓库: {issue.full_name}")
            lines.append(f"Issue #{issue.number}")
            lines.append("")

        lines.append("### 问题描述")
        lines.append("")
        lines.append(issue.body[:1000] if issue.body else "(无描述)")
        lines.append("")

        # For local files, scan the workspace if available
        workspace = Path.cwd()
        py_files = list(workspace.rglob("*.py"))[:20]
        if py_files:
            lines.append("### 项目文件")
            lines.append("")
            for f in py_files:
                rel = f.relative_to(workspace)
                lines.append(f"- {rel}")
            lines.append("")

        return "\n".join(lines)

    def _generate_plan(self, issue: IssueDescription, analysis: str) -> str:
        """生成修复计划。"""
        plan = [
            "## 修复计划",
            "",
            f"### 目标: {issue.title}",
            "",
            "### 步骤",
            "",
        ]

        # Extract relevant keywords from issue body for smarter plan
        body_lower = (issue.body or "").lower()
        keywords = set()
        for word in ["fix", "bug", "error", "add", "remove", "update", "change",
                      "broken", "issue", "crash", "fail", "test", "feature"]:
            if word in body_lower:
                keywords.add(word)

        if keywords:
            plan.append(f"**关键词提取**: {', '.join(sorted(keywords))}")
            plan.append("")

        plan.extend([
            "1. **理解** — 阅读相关源码，定位问题根因",
            "2. **修改** — 根据分析进行代码修改",
            "3. **测试** — 运行测试验证修改正确性",
            "4. **验证** — 确认无回归",
            "",
        ])
        if issue.full_name:
            plan.append(f"> 仓库: {issue.full_name}")
            plan.append(f"> Issue: #{issue.number}")

        return "\n".join(plan)

    def _modify(self, issue: IssueDescription, plan: str) -> dict[str, Any]:
        """使用 SWE Agent 执行修改。"""
        import asyncio
        from zmai.runtime import Runtime

        task_prompt = plan + "\n\n" + issue.format_for_agent()

        config = Config(sources=[])
        config.set("runtime.max_iterations", self._max_steps)
        runtime = Runtime(config=config)

        # Use the project's Workspace as the project path
        if issue.source == "file":
            # Local file: work in CWD
            project_path = str(Path.cwd())
        else:
            # GitHub issue: clone the repo
            project_path = self._ensure_repo(issue)
            if not project_path:
                project_path = str(Path.cwd())

        config.set("project_path", project_path)

        def on_progress(typ: str, msg: str) -> None:
            if typ == "tool":
                sys.stderr.write(f"\n  > {msg}")
            sys.stderr.flush()

        result = asyncio.run(runtime.run(
            agent_id=f"issue_{issue.number or abs(hash(issue.title))}",
            task=task_prompt,
            config={
                "project_path": project_path,
                "auto_plan": True,
            },
            on_progress=on_progress,
        ))

        return {
            "status": result.get("status", "?"),
            "output": result.get("output", ""),
            "steps": result.get("steps", 0),
        }

    def _run_tests(self, issue: IssueDescription) -> str:
        """运行测试。"""
        project_path = self._resolve_project_path(issue)
        if not project_path:
            return "(无可用项目路径，跳过测试)"

        outputs: list[str] = []

        # Try pytest
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "-q", "--no-header"],
                cwd=project_path,
                capture_output=True, text=True, timeout=120,
            )
            output = (result.stdout or "") + (result.stderr or "")
            outputs.append(f"=== pytest ===\n{output[:2000]}")
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            outputs.append("=== pytest ===\n(超时)")
        except Exception as e:
            outputs.append(f"=== pytest ===\n(错误: {e})")

        return "\n\n".join(outputs)

    def _verify(self, issue: IssueDescription, modify_result: dict[str, Any], test_output: str) -> str:
        """验证修复结果。"""
        lines: list[str] = []

        agent_status = modify_result.get("status", "?")
        if agent_status == "completed":
            lines.append("✅ Agent 已完成修改")
        else:
            lines.append(f"❌ Agent 状态: {agent_status}")
            err = modify_result.get("error") or modify_result.get("output", "")
            if err:
                lines.append(f"   错误: {str(err)[:200]}")

        if test_output:
            if "FAILED" in test_output or "failed" in test_output.split("\n")[-3:]:
                lines.append("❌ 测试存在失败项")
            elif "passed" in test_output:
                lines.append("✅ 测试通过")
            else:
                lines.append("⚠️ 测试结果无法判定")

        return "\n".join(lines)

    def _generate_diff(self, issue: IssueDescription) -> str:
        """生成 git diff。"""
        project_path = self._resolve_project_path(issue)
        if not project_path:
            return ""

        try:
            result = subprocess.run(
                ["git", "diff"],
                cwd=project_path,
                capture_output=True, text=True, timeout=30,
            )
            diff = result.stdout or ""
            if not diff:
                # Try diff --cached
                result = subprocess.run(
                    ["git", "diff", "--cached"],
                    cwd=project_path,
                    capture_output=True, text=True, timeout=30,
                )
                diff = result.stdout or ""
            return diff[:10000]
        except Exception as e:
            return f"(生成 diff 失败: {e})"

    def _simulate_diff(self, issue: IssueDescription, plan: str) -> str:
        """模拟预期 diff（dry-run 用）。"""
        lines: list[str] = [
            "# 预期变更 (DRY RUN — 未实际应用)",
            "",
            plan,
            "",
            "--- 以下文件将被修改 ---",
        ]
        # Extract filenames from plan
        for line in plan.split("\n"):
            line_s = line.strip()
            if any(line_s.lower().startswith(x) for x in ("- `", "- ", "* `")):
                for ch in ("`", "*", '"'):
                    line_s = line_s.replace(ch, "")
                lines.append(f"  {line_s}")
        return "\n".join(lines)

    def _build_summary(self, result: IssueResult) -> str:
        """构建最终摘要。"""
        lines: list[str] = [
            "## 完成摘要",
            "",
            f"Issue: {result.issue.title}",
            f"状态: ✅ 已完成",
            f"耗时: {result.duration_seconds:.1f}s",
        ]

        if result.issue.full_name:
            lines.append(f"仓库: {result.issue.full_name}")
        if result.issue.number:
            lines.append(f"Issue #{result.issue.number}")

        diff_lines = (result.diff or "").strip().split("\n")
        if diff_lines and len(diff_lines) > 1:
            added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
            lines.append(f"变更: +{added}/-{removed} 行")
            lines.append("")
            if result.verification:
                lines.append(result.verification)

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════════════════════

    def _resolve_project_path(self, issue: IssueDescription) -> str | None:
        """解析项目路径。"""
        if issue.source == "github" and issue.full_name:
            repo_dir = self._work_dir / issue.repo
            if repo_dir.exists():
                return str(repo_dir)
        return str(Path.cwd())

    def _ensure_repo(self, issue: IssueDescription) -> str | None:
        """确保 GitHub 仓库已克隆。"""
        if not issue.full_name:
            return None

        from zmai.swe.github import clone_repo, GitHubError

        repo_dir = self._work_dir / issue.repo
        if repo_dir.exists():
            return str(repo_dir)

        try:
            logger.info("克隆仓库 %s ...", issue.full_name)
            clone_repo(issue.owner, issue.repo, self._work_dir)
            return str(repo_dir)
        except GitHubError as e:
            logger.warning("克隆失败: %s", e)
            return None
