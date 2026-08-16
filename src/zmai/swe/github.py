"""GitHub API client — pure stdlib, no dependencies.

Handles:
  - Parsing GitHub issue URLs
  - Fetching issue details (title, body, comments)
  - Repository operations (clone, branch, commit)
  - Creating pull requests
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("zmai.swe.github")

# ── URL pattern ─────────────────────────────────────────────────

_GITHUB_ISSUE_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)$"
)
_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:/|$)"
)

_API_BASE = "https://api.github.com"


# ── Data models ─────────────────────────────────────────────────


@dataclass
class GitHubIssue:
    """A GitHub issue with parsed details."""

    owner: str
    repo: str
    number: int
    title: str = ""
    body: str = ""
    state: str = "open"
    labels: list[str] = field(default_factory=list)
    comments: list[dict[str, str]] = field(default_factory=list)
    html_url: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass
class GitHubRepo:
    """A GitHub repository reference."""

    owner: str
    repo: str
    clone_url: str = ""
    default_branch: str = "main"


# ── Errors ──────────────────────────────────────────────────────


class GitHubError(Exception):
    """Base error for GitHub operations."""


class IssueNotFound(GitHubError):
    """The requested issue does not exist."""


class RepoNotFound(GitHubError):
    """The requested repository does not exist."""


class AuthFailed(GitHubError):
    """GitHub API authentication failed."""


# ── API Client ─────────────────────────────────────────────────


def _get_token() -> str:
    """Get GitHub token from environment."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    return token


def _api_request(
    method: str,
    path: str,
    data: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Make a GitHub API request using urllib.

    Args:
        method: HTTP method (GET, POST, PATCH, etc.)
        path: API path (e.g. /repos/owner/repo/issues/1)
        data: Optional JSON body
        token: GitHub token (defaults to GITHUB_TOKEN env)

    Returns:
        Parsed JSON response as dict.

    Raises:
        AuthFailed: 401 response
        IssueNotFound: 404 response
        GitHubError: Other errors
    """
    token = token or _get_token()
    url = f"{_API_BASE}{path}"

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "zmai-swe-agent",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        status = e.code
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        if status == 401:
            raise AuthFailed(
                "GitHub API authentication failed. "
                "Set GITHUB_TOKEN or GH_TOKEN environment variable."
            ) from e
        if status == 403:
            raise AuthFailed(
                f"GitHub API rate limited or access denied (403): {body_text}"
            ) from e
        if status == 404:
            raise IssueNotFound(f"Resource not found: {path}") from e
        raise GitHubError(f"GitHub API error {status}: {body_text}") from e
    except urllib.error.URLError as e:
        raise GitHubError(f"GitHub API connection error: {e}") from e


# ── URL Parsing ─────────────────────────────────────────────────


def parse_issue_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub issue URL into (owner, repo, number).

    Args:
        url: Full GitHub issue URL like https://github.com/owner/repo/issues/123

    Returns:
        (owner, repo, issue_number)

    Raises:
        ValueError: URL is not a valid GitHub issue URL.
    """
    m = _GITHUB_ISSUE_RE.match(url.strip())
    if not m:
        raise ValueError(
            f"Invalid GitHub issue URL: {url}. "
            f"Expected format: https://github.com/owner/repo/issues/123"
        )
    return m.group(1), m.group(2), int(m.group(3))


def parse_repo_url(url: str) -> tuple[str, str]:
    """Parse a GitHub repo URL into (owner, repo).

    Args:
        url: GitHub repo URL like https://github.com/owner/repo

    Returns:
        (owner, repo)
    """
    m = _GITHUB_REPO_RE.match(url.strip())
    if not m:
        raise ValueError(f"Invalid GitHub repo URL: {url}")
    return m.group(1), m.group(2)


# ── Issue Operations ────────────────────────────────────────────


def fetch_issue(url: str, token: str | None = None) -> GitHubIssue:
    """Fetch issue details from GitHub API.

    Args:
        url: Full GitHub issue URL.
        token: GitHub token (defaults to GITHUB_TOKEN env).

    Returns:
        GitHubIssue with title, body, comments, labels.

    Raises:
        ValueError: Invalid URL.
        IssueNotFound: Issue doesn't exist.
        AuthFailed: Authentication failed.
    """
    owner, repo, number = parse_issue_url(url)

    # Fetch issue
    issue_data = _api_request("GET", f"/repos/{owner}/{repo}/issues/{number}", token=token)

    # Fetch comments
    try:
        comments_data = _api_request(
            "GET", f"/repos/{owner}/{repo}/issues/{number}/comments", token=token
        )
    except GitHubError:
        comments_data = []

    comments = []
    for c in comments_data:
        comments.append({
            "author": c.get("user", {}).get("login", "unknown"),
            "body": c.get("body", "")[:2000],
        })

    labels = [label.get("name", "") for label in issue_data.get("labels", [])]

    return GitHubIssue(
        owner=owner,
        repo=repo,
        number=number,
        title=issue_data.get("title", ""),
        body=issue_data.get("body", "") or "",
        state=issue_data.get("state", "open"),
        labels=labels,
        comments=comments,
        html_url=issue_data.get("html_url", url),
    )


# ── Repository Operations ───────────────────────────────────────


def get_default_branch(owner: str, repo: str, token: str | None = None) -> str:
    """Get the default branch of a repository."""
    data = _api_request("GET", f"/repos/{owner}/{repo}", token=token)
    return data.get("default_branch", "main")


def clone_repo(
    owner: str,
    repo: str,
    target_dir: str | Path,
    token: str | None = None,
) -> Path:
    """Clone a GitHub repository to a local directory.

    Uses git CLI under the hood (subprocess).

    Args:
        owner: Repository owner.
        repo: Repository name.
        target_dir: Directory to clone into.
        token: GitHub token for authentication.

    Returns:
        Path to the cloned repository.
    """
    target = Path(target_dir)
    repo_path = target / repo

    if repo_path.exists():
        logger.info("Repo already exists at %s, pulling latest", repo_path)
        subprocess.run(
            ["git", "-C", str(repo_path), "pull"],
            capture_output=True, text=True, timeout=60,
        )
        return repo_path

    # Build clone URL with token if available
    token = token or _get_token()
    if token:
        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    else:
        clone_url = f"https://github.com/{owner}/{repo}.git"

    target.mkdir(parents=True, exist_ok=True)
    logger.info("Cloning %s/%s into %s", owner, repo, repo_path)

    result = subprocess.run(
        ["git", "clone", clone_url, str(repo_path)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise GitHubError(f"Clone failed: {result.stderr[:500]}")

    return repo_path


def create_branch(repo_path: str | Path, branch_name: str, base_branch: str = "main") -> None:
    """Create and switch to a new branch."""
    repo_path = Path(repo_path)
    subprocess.run(
        ["git", "-C", str(repo_path), "checkout", base_branch],
        capture_output=True, text=True, timeout=30,
    )
    result = subprocess.run(
        ["git", "-C", str(repo_path), "checkout", "-b", branch_name],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise GitHubError(f"Branch creation failed: {result.stderr[:500]}")


def commit_and_push(
    repo_path: str | Path,
    message: str,
    branch_name: str,
    token: str | None = None,
) -> None:
    """Commit all changes and push to remote."""
    repo_path = Path(repo_path)
    token = token or _get_token()

    # Stage all
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "-A"],
        capture_output=True, text=True, timeout=30,
    )

    # Commit
    result = subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", message],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 and "nothing to commit" not in result.stderr:
        logger.warning("Commit may have failed: %s", result.stderr[:200])

    # Push
    push_url = f"https://x-access-token:{token}@github.com"
    result = subprocess.run(
        ["git", "-C", str(repo_path), "push", push_url, branch_name],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise GitHubError(f"Push failed: {result.stderr[:500]}")


# ── PR Operations ───────────────────────────────────────────────


def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str = "main",
    token: str | None = None,
) -> dict[str, Any]:
    """Create a pull request on GitHub.

    Args:
        owner: Repository owner.
        repo: Repository name.
        title: PR title.
        body: PR description body.
        head: Branch name with changes.
        base: Target branch (default: main).
        token: GitHub token.

    Returns:
        PR data dict with 'html_url' and 'number'.

    Raises:
        GitHubError: PR creation failed.
    """
    data = {
        "title": title,
        "body": body,
        "head": head,
        "base": base,
    }
    result = _api_request(
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        data=data,
        token=token,
    )
    return {
        "number": result.get("number", 0),
        "html_url": result.get("html_url", ""),
        "state": result.get("state", ""),
    }


# ── Formatting ──────────────────────────────────────────────────


def format_issue_for_agent(issue: GitHubIssue) -> str:
    """Format a GitHub issue as a prompt for the SWE agent.

    Args:
        issue: The fetched GitHub issue.

    Returns:
        Formatted string suitable as an agent task prompt.
    """
    lines = [
        f"## GitHub Issue #{issue.number}: {issue.title}",
        f"Repository: {issue.full_name}",
        f"State: {issue.state}",
        f"Labels: {', '.join(issue.labels) if issue.labels else 'none'}",
        f"URL: {issue.html_url}",
        "",
        "### Description",
        issue.body[:3000] if issue.body else "(no description)",
    ]

    if issue.comments:
        lines.append("")
        lines.append("### Comments")
        for c in issue.comments[:5]:
            lines.append(f"**{c['author']}**: {c['body'][:500]}")
        if len(issue.comments) > 5:
            lines.append(f"... +{len(issue.comments) - 5} more comments")

    lines.append("")
    lines.append("---")
    lines.append(
        "Your task: Reproduce the issue, understand the root cause, "
        "implement the fix, and verify it works."
    )

    return "\n".join(lines)
