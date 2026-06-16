"""
github/pr_fetcher.py — Synchronous GitHub PR data fetcher for RepoMind AI.

All functions are synchronous (use asyncio.to_thread() at call sites).
Handles: PR metadata, changed files list, unified diff per file, PR list.

Security notes
--------------
* GITHUB_TOKEN is read from settings — never from environment directly.
* Token is never logged or echoed.
* All HTTP errors produce informative ValueError messages.
* Diff content is returned as raw text — callers must not expose secrets.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from requests import Response

from config import settings

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
_TIMEOUT = 20  # seconds — diffs can be large


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _build_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    """Return request headers with optional Bearer token."""
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "RepoMind-AI/0.1",
    }
    token = settings.github_token.strip() if settings.github_token else ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        logger.warning(
            "GITHUB_TOKEN not set — GitHub API requests will be rate-limited "
            "(60 req/hr unauthenticated). Set GITHUB_TOKEN in .env."
        )
    return headers


def _raise_for_github_error(resp: Response, context: str) -> None:
    """
    Raise a user-friendly ValueError for common GitHub API errors.

    Checks status codes before calling raise_for_status so we emit
    actionable messages rather than raw HTTP errors.
    """
    if resp.status_code == 401:
        raise ValueError(
            f"GitHub token is invalid or expired (HTTP 401) while {context}. "
            "Update GITHUB_TOKEN in your .env file."
        )
    if resp.status_code == 403:
        remaining = resp.headers.get("X-RateLimit-Remaining", "?")
        reset_ts   = resp.headers.get("X-RateLimit-Reset", "?")
        raise ValueError(
            f"GitHub API access denied (HTTP 403) while {context}. "
            f"Rate-limit remaining: {remaining}, resets at: {reset_ts}. "
            "Set or refresh GITHUB_TOKEN in .env."
        )
    if resp.status_code == 404:
        raise ValueError(
            f"Resource not found (HTTP 404) while {context}. "
            "Check that the repository is accessible and the PR number is correct."
        )
    if resp.status_code == 410:
        raise ValueError(
            f"Resource is gone (HTTP 410) while {context}. "
            "The PR may have been deleted."
        )
    resp.raise_for_status()


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    """
    Extract (owner, repo_name) from a GitHub URL.

    Accepts:
      https://github.com/owner/repo
      https://github.com/owner/repo.git
      owner/repo
    """
    url = repo_url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith("https://github.com/"):
        url = url[len("https://github.com/"):]
    parts = url.split("/")
    if len(parts) < 2:
        raise ValueError(
            f"Cannot parse owner/repo from URL: {repo_url!r}. "
            "Expected format: https://github.com/owner/repo"
        )
    return parts[0], parts[1]


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_pull_requests(
    repo_url: str,
    state: str = "open",
    per_page: int = 30,
    page: int = 1,
) -> list[dict[str, Any]]:
    """
    List pull requests for a repository.

    Args:
        repo_url:  Full GitHub URL or 'owner/repo' string.
        state:     'open' | 'closed' | 'all'
        per_page:  Results per page (max 100).
        page:      Page number (1-indexed).

    Returns:
        List of PR summary dicts with keys:
            number, title, state, draft, user_login,
            head_ref, base_ref, html_url,
            created_at, updated_at, body_preview

    Raises:
        ValueError: For auth, rate-limit, or not-found errors.
        requests.Timeout: On network timeout.
    """
    owner, repo_name = _parse_owner_repo(repo_url)
    url = (
        f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/pulls"
        f"?state={state}&per_page={per_page}&page={page}&sort=updated&direction=desc"
    )
    logger.info("Fetching PRs: GET %s", url)

    try:
        resp = requests.get(url, headers=_build_headers(), timeout=_TIMEOUT)
    except requests.Timeout:
        raise ValueError(f"Timed out fetching PRs for {owner}/{repo_name}.")
    except requests.ConnectionError as exc:
        raise ValueError(f"Network error contacting GitHub: {exc}")

    _raise_for_github_error(resp, f"listing PRs for {owner}/{repo_name}")

    raw_prs: list[dict] = resp.json()
    return [
        {
            "number":       pr["number"],
            "title":        pr.get("title", ""),
            "state":        pr.get("state", ""),
            "draft":        bool(pr.get("draft", False)),
            "user_login":   pr.get("user", {}).get("login", ""),
            "head_ref":     pr.get("head", {}).get("ref", ""),
            "base_ref":     pr.get("base", {}).get("ref", ""),
            "html_url":     pr.get("html_url", ""),
            "created_at":   pr.get("created_at", ""),
            "updated_at":   pr.get("updated_at", ""),
            "body_preview": (pr.get("body") or "")[:300],
            "additions":    pr.get("additions", 0),
            "deletions":    pr.get("deletions", 0),
            "changed_files":pr.get("changed_files", 0),
        }
        for pr in raw_prs
    ]


def fetch_pr_detail(repo_url: str, pr_number: int) -> dict[str, Any]:
    """
    Fetch full metadata for a single pull request.

    Returns:
        Dict with: number, title, state, draft, merged,
        user_login, head_ref, base_ref, html_url,
        body, additions, deletions, changed_files,
        created_at, updated_at, merged_at.

    Raises:
        ValueError: For auth, rate-limit, not-found, or bad URL errors.
    """
    owner, repo_name = _parse_owner_repo(repo_url)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/pulls/{pr_number}"
    logger.info("Fetching PR detail: GET %s", url)

    try:
        resp = requests.get(url, headers=_build_headers(), timeout=_TIMEOUT)
    except requests.Timeout:
        raise ValueError(f"Timed out fetching PR #{pr_number} for {owner}/{repo_name}.")
    except requests.ConnectionError as exc:
        raise ValueError(f"Network error: {exc}")

    _raise_for_github_error(resp, f"fetching PR #{pr_number} for {owner}/{repo_name}")

    pr: dict = resp.json()
    return {
        "number":       pr["number"],
        "title":        pr.get("title", ""),
        "state":        pr.get("state", ""),
        "draft":        bool(pr.get("draft", False)),
        "merged":       bool(pr.get("merged", False)),
        "user_login":   pr.get("user", {}).get("login", ""),
        "head_ref":     pr.get("head", {}).get("ref", ""),
        "base_ref":     pr.get("base", {}).get("ref", ""),
        "head_sha":     pr.get("head", {}).get("sha", ""),
        "base_sha":     pr.get("base", {}).get("sha", ""),
        "html_url":     pr.get("html_url", ""),
        "body":         pr.get("body") or "",
        "additions":    pr.get("additions", 0),
        "deletions":    pr.get("deletions", 0),
        "changed_files":pr.get("changed_files", 0),
        "created_at":   pr.get("created_at", ""),
        "updated_at":   pr.get("updated_at", ""),
        "merged_at":    pr.get("merged_at"),
    }


def fetch_pr_files(repo_url: str, pr_number: int) -> list[dict[str, Any]]:
    """
    Fetch the list of files changed in a pull request.

    Uses pagination to handle PRs with > 30 files (GitHub default page size).
    Caps at 300 files to avoid abuse.

    Returns:
        List of file dicts with keys:
            filename, status, additions, deletions, changes,
            patch (unified diff string or "" if no patch).

    Raises:
        ValueError: For auth/rate-limit/not-found errors.
    """
    owner, repo_name = _parse_owner_repo(repo_url)
    all_files: list[dict] = []
    page = 1
    max_pages = 10   # 10 × 30 = 300 files max

    while page <= max_pages:
        url = (
            f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}"
            f"/pulls/{pr_number}/files?per_page=30&page={page}"
        )
        logger.info("Fetching PR files page %d: GET %s", page, url)

        try:
            resp = requests.get(url, headers=_build_headers(), timeout=_TIMEOUT)
        except requests.Timeout:
            raise ValueError(
                f"Timed out fetching files for PR #{pr_number} (page {page})."
            )
        except requests.ConnectionError as exc:
            raise ValueError(f"Network error: {exc}")

        _raise_for_github_error(resp, f"fetching files for PR #{pr_number} page {page}")

        page_data: list[dict] = resp.json()
        if not page_data:
            break

        for f in page_data:
            all_files.append({
                "filename":  f.get("filename", ""),
                "status":    f.get("status", ""),     # added|removed|modified|renamed
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "changes":   f.get("changes", 0),
                "patch":     f.get("patch", ""),      # unified diff — may be "" for binary
            })

        if len(page_data) < 30:
            break   # last page
        page += 1

    logger.info("PR #%d has %d changed files", pr_number, len(all_files))
    return all_files
