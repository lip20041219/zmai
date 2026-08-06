"""GitHub & Issue CLI — zmai issue, zmai pr.

子命令:
  zmai issue <url|file>       完整 Issue 工作流
  zmai issue <url|file> --plan    仅生成计划
  zmai issue <url|file> --dry-run 模拟运行
  zmai issue <url|file> --json    JSON 输出
  zmai pr <title>               从当前分支创建 PR
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from zmai.cli.formatters import Theme, print_error, print_info, print_success


def _get_theme() -> Theme:
    return Theme.dark()


# ═══════════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════════


def run_github(argv: list[str]) -> None:
    """Dispatch GitHub/Issue CLI subcommands."""
    if not argv:
        _print_help()
        return

    cmd = argv[0]
    rest = argv[1:]

    if cmd == "issue":
        _cmd_issue(rest)
    elif cmd == "pr":
        _cmd_pr(rest)
    else:
        print_error(f"Unknown command: {cmd}")
        _print_help()


def _print_help() -> None:
    theme = _get_theme()
    print()
    print(f"  {theme.dim('zmai issue <url|file>')}             完整 Issue 工作流")
    print(f"  {theme.dim('zmai issue <url|file> --plan')}      仅生成计划")
    print(f"  {theme.dim('zmai issue <url|file> --dry-run')}   模拟运行")
    print(f"  {theme.dim('zmai issue <url|file> --json')}      JSON 输出")
    print(f"  {theme.dim('zmai pr <title>')}                   从当前分支创建 PR")
    print()


# ═══════════════════════════════════════════════════════════════
# zmai issue
# ═══════════════════════════════════════════════════════════════


def _cmd_issue(argv: list[str]) -> None:
    """Handle `zmai issue <url|file> [--plan] [--dry-run] [--json]`."""
    theme = _get_theme()

    if not argv:
        print_error("Usage: zmai issue <github-issue-url | issue.md> [--plan] [--dry-run] [--json]")
        sys.exit(1)

    input_str = argv[0]
    plan_only = "--plan" in argv
    dry_run = "--dry-run" in argv
    use_json = "--json" in argv

    from zmai.issue.agent import IssueAgent

    agent = IssueAgent()

    print(f"  Issue 工作流开始: {input_str}")
    if plan_only:
        print(f"  模式: 仅计划 (--plan)")
    elif dry_run:
        print(f"  模式: 模拟 (--dry-run)")
    print()

    result = agent.run(
        input_str,
        plan_only=plan_only,
        dry_run=dry_run,
    )

    if use_json:
        _output_json(result)
    else:
        _output_console(result, theme)


def _output_console(result, theme) -> None:
    """控制台输出。"""
    from zmai.issue.agent import IssueResult
    if not isinstance(result, IssueResult):
        print(json.dumps(result if isinstance(result, dict) else result.to_dict(), indent=2, ensure_ascii=False))
        return

    # 错误处理
    if result.error:
        print_error(f"工作流失败: {result.error}", theme)
        print(f"  阶段: {result.workflow_status}")
        return

    # Issue 信息
    issue = result.issue
    print()
    print(f"  {theme.highlight(issue.title)}")
    if issue.full_name:
        print(f"  {theme.dim(f'{issue.full_name}' + (f' #{issue.number}' if issue.number else ''))}")
    print()

    # 计划
    if result.plan:
        print(f"  {theme.dim('─' * 50)}")
        print(f"  📋 修复计划")
        print(f"  {theme.dim('─' * 50)}")
        for line in result.plan.split("\n"):
            print(f"  {line}")
        print()

    # Diff
    if result.diff:
        diff_lines = result.diff.strip().split("\n")
        if len(diff_lines) > 1:
            print(f"  {theme.dim('─' * 50)}")
            print(f"  📝 变更内容 ({len(diff_lines)} 行)")
            print(f"  {theme.dim('─' * 50)}")
            # Show first 30 lines
            for line in diff_lines[:30]:
                if line.startswith("+"):
                    print(f"  {theme.highlight(line)}")
                elif line.startswith("-"):
                    print(f"  {theme.dim(line)}")
                else:
                    print(f"  {line}")
            if len(diff_lines) > 30:
                print(f"  ... ({len(diff_lines) - 30} 行省略)")
            print()

    # 测试输出
    if result.test_output:
        lines = result.test_output.strip().split("\n")
        print(f"  {theme.dim('─' * 50)}")
        print(f"  🧪 测试输出")
        print(f"  {theme.dim('─' * 50)}")
        for line in lines[:15]:
            clean = line.strip()
            if clean:
                print(f"  {clean}")
        if len(lines) > 15:
            print(f"  ... ({len(lines) - 15} 行省略)")
        print()

    # 验证
    if result.verification:
        print(f"  {theme.dim('─' * 50)}")
        print(f"  ✅ 验证结果")
        print(f"  {theme.dim('─' * 50)}")
        for line in result.verification.split("\n"):
            print(f"  {line}")
        print()

    # 完成摘要
    if result.summary:
        print(f"  {theme.dim('─' * 50)}")
        print(f"  📊 摘要")
        print(f"  {theme.dim('─' * 50)}")
        for line in result.summary.split("\n"):
            print(f"  {line}")
        print()

    # 元信息
    print(f"  {theme.dim(f'耗时: {result.duration_seconds:.1f}s  |  状态: {result.workflow_status}')}")


def _output_json(result) -> None:
    """JSON 输出。"""
    if hasattr(result, "to_dict"):
        data = result.to_dict()
    elif isinstance(result, dict):
        data = result
    else:
        data = {"error": str(result)}
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
# zmai pr (保留原有功能)
# ═══════════════════════════════════════════════════════════════


def _cmd_pr(argv: list[str]) -> None:
    """Handle `zmai pr <title>`."""
    theme = _get_theme()

    if not argv:
        print_error("Usage: zmai pr <pr-title> [--body <description>]")
        sys.exit(1)

    title = argv[0]
    body = ""
    if "--body" in argv:
        idx = argv.index("--body")
        if idx + 1 < len(argv):
            body = argv[idx + 1]

    # Detect owner/repo from git remote
    try:
        import subprocess
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        remote_url = (result.stdout or "").strip()
    except Exception:
        print_error("Not in a git repository or no remote 'origin'")
        sys.exit(1)

    from zmai.swe.github import parse_repo_url, create_pull_request

    try:
        owner, repo = parse_repo_url(remote_url)
    except ValueError:
        import re
        m = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
        if m:
            owner, repo = m.group(1), m.group(2)
        else:
            print_error(f"Cannot parse remote URL: {remote_url}")
            sys.exit(1)

    # Get current branch name
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        branch = (result.stdout or "").strip()
    except Exception:
        branch = "main"

    print_info(f"Creating PR: {title}", theme)
    print(f"  {theme.dim(f'{owner}/{repo}')}  |  branch: {branch}")
    print()

    try:
        pr = create_pull_request(
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            head=branch,
        )
        print_success(f"PR #{pr['number']} created!", theme)
        print(f"  {pr['html_url']}")
    except Exception as e:
        print_error(f"PR creation failed: {e}", theme)
        if "push" in str(e).lower():
            print_info(
                f"Try pushing your branch first:\n"
                f"  git push origin {branch}",
                theme,
            )
        sys.exit(1)
