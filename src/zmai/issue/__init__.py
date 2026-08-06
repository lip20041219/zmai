"""IssueParser — 解析 GitHub Issue URL 和本地 Markdown 文件。

返回统一的 IssueDescription 结构。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("zmai.issue.parser")

# GitHub Issue URL pattern
_GITHUB_ISSUE_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)$"
)


@dataclass
class IssueDescription:
    """统一的 Issue 描述结构，屏蔽 URL/文件来源差异。"""

    title: str
    body: str
    source: str  # "github" | "file"
    number: int = 0
    owner: str = ""
    repo: str = ""
    url: str = ""
    comments: list[dict[str, str]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    file_path: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}" if self.owner and self.repo else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body[:500],
            "source": self.source,
            "number": self.number,
            "owner": self.owner,
            "repo": self.repo,
            "url": self.url,
            "comment_count": len(self.comments),
            "labels": self.labels,
            "file_path": self.file_path,
        }

    def format_for_agent(self) -> str:
        """格式化为 Agent 可读的任务描述。"""
        parts: list[str] = []
        parts.append(f"# {self.title}")
        parts.append("")
        parts.append(self.body.strip())

        if self.comments:
            parts.append("\n## Comments\n")
            for c in self.comments[:5]:
                parts.append(f"**{c.get('author', 'unknown')}**: {c.get('body', '')[:300]}")
                parts.append("")

        if self.full_name:
            repo_info = (
                f"\n## Repository\n"
                f"- Owner: {self.owner}\n"
                f"- Repo: {self.repo}\n"
                f"- Issue #{self.number}\n"
            )
            parts.append(repo_info)

        return "\n".join(parts)


class IssueParser:
    """解析 Issue 输入（URL 或本地文件）。"""

    def parse(self, input_str: str) -> IssueDescription:
        """解析输入，返回 IssueDescription。

        Args:
            input_str: GitHub Issue URL 或本地 Markdown 文件路径。

        Returns:
            IssueDescription 对象。

        Raises:
            ValueError: 输入无法解析。
            FileNotFoundError: 本地文件不存在。
        """
        # 尝试 GitHub URL
        m = _GITHUB_ISSUE_RE.match(input_str)
        if m:
            return self._parse_github_url(input_str, m.group(1), m.group(2), int(m.group(3)))

        # 尝试本地文件
        p = Path(input_str)
        if p.exists():
            return self._parse_file(p)

        # 可能是 URL 但格式不对
        if input_str.startswith(("http://", "https://")):
            raise ValueError(
                f"不支持的 URL 格式: {input_str}\n"
                f"  期望: https://github.com/owner/repo/issues/NUMBER"
            )

        # 文件不存在
        raise FileNotFoundError(
            f"文件不存在: {input_str}\n"
            f"  请提供 GitHub Issue URL 或本地 Markdown 文件路径。"
        )

    # ── GitHub URL ──────────────────────────────────────────────

    def _parse_github_url(
        self, url: str, owner: str, repo: str, number: int,
    ) -> IssueDescription:
        """从 GitHub API 获取 Issue 详情。"""
        from zmai.swe.github import GitHubError, fetch_issue as _gh_fetch

        try:
            gh_issue = _gh_fetch(url)
        except GitHubError as e:
            logger.warning("GitHub API 请求失败: %s", e)
            raise ValueError(str(e)) from e

        return IssueDescription(
            title=gh_issue.title,
            body=gh_issue.body or "",
            source="github",
            number=gh_issue.number,
            owner=gh_issue.owner,
            repo=gh_issue.repo,
            url=gh_issue.html_url or url,
            comments=gh_issue.comments,
            labels=gh_issue.labels,
        )

    # ── 本地文件 ────────────────────────────────────────────────

    def _parse_file(self, path: Path) -> IssueDescription:
        """从本地 Markdown 文件解析 Issue。"""
        content = path.read_text(encoding="utf-8")

        # 提取标题（第一个 # 行）
        title = "Issue from file"
        for line in content.split("\n"):
            line_stripped = line.strip()
            if line_stripped.startswith("# "):
                title = line_stripped[2:].strip()
                break

        return IssueDescription(
            title=title,
            body=content,
            source="file",
            file_path=str(path.resolve()),
        )

    # ── 辅助 ────────────────────────────────────────────────────

    @staticmethod
    def is_github_url(text: str) -> bool:
        return bool(_GITHUB_ISSUE_RE.match(text))

    @staticmethod
    def is_file_path(text: str) -> bool:
        return Path(text).exists()
