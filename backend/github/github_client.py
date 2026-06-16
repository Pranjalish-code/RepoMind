"""
github/github_client.py — Thin client for the GitHub REST API v3.

Uses the ``requests`` library (sync, run via executor in async contexts).
Handles: repo metadata fetch, auth headers, rate-limit and 404 errors.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from requests import Response

from config import settings

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
_TIMEOUT = 15  # seconds


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_headers() -> dict[str, str]:
    """Build request headers, including auth if GITHUB_TOKEN is set."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "RepoMind-AI/0.1",
    }
    token = settings.github_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _handle_error(resp: Response, owner: str, repo_name: str) -> None:
    """
    Raise an informative ValueError or re-raise requests.HTTPError.

    Inspects the status code before calling raise_for_status so we can
    emit actionable messages for common failure modes.
    """
    if resp.status_code == 404:
        raise ValueError(
            f"Repository '{owner}/{repo_name}' not found. "
            "It may not exist, be private, or have been deleted. "
            "For private repos, set GITHUB_TOKEN in your .env file."
        )
    if resp.status_code == 403:
        remaining = resp.headers.get("X-RateLimit-Remaining", "?")
        raise ValueError(
            f"GitHub API access denied (HTTP 403). "
            f"Rate-limit remaining: {remaining}. "
            "Set or refresh GITHUB_TOKEN in .env to raise the limit."
        )
    if resp.status_code == 401:
        raise ValueError(
            "GitHub token is invalid or expired (HTTP 401). "
            "Update GITHUB_TOKEN in your .env file."
        )
    resp.raise_for_status()


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_repo_metadata(owner: str, repo_name: str) -> dict[str, Any]:
    """
    Fetch repository metadata from the GitHub REST API.

    This is a synchronous call — run via ``asyncio.get_event_loop().run_in_executor``
    or ``asyncio.to_thread`` inside async contexts.

    Args:
        owner:     GitHub organisation or user name.
        repo_name: Repository name (without .git).

    Returns:
        Dict containing:
            name, full_name, default_branch, description,
            private, size_kb, html_url, clone_url, topics.

    Raises:
        ValueError:            Informative error for 404/403/401.
        requests.HTTPError:    For other HTTP error codes.
        requests.Timeout:      If the request exceeds _TIMEOUT seconds.
        requests.ConnectionError: Network issues.
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}"
    logger.info("Fetching GitHub metadata: GET %s", url)

    try:
        resp = requests.get(url, headers=_build_headers(), timeout=_TIMEOUT)
    except requests.Timeout:
        raise ValueError(
            f"Timed out fetching metadata for '{owner}/{repo_name}'. "
            "Check your network connection."
        )
    except requests.ConnectionError as exc:
        raise ValueError(
            f"Network error while contacting GitHub API: {exc}"
        )

    _handle_error(resp, owner, repo_name)
    data: dict[str, Any] = resp.json()

    # Private repo without token — catch at metadata level too
    if data.get("private") and not settings.github_token.strip():
        raise ValueError(
            f"Repository '{owner}/{repo_name}' is private. "
            "Set GITHUB_TOKEN in .env to clone private repositories."
        )

    return {
        "name": data["name"],
        "full_name": data["full_name"],
        "default_branch": data.get("default_branch") or "main",
        "description": data.get("description") or "",
        "private": bool(data.get("private", False)),
        "size_kb": int(data.get("size", 0)),
        "html_url": data.get("html_url", ""),
        "clone_url": data.get("clone_url", ""),
        "ssh_url": data.get("ssh_url", ""),
        "topics": data.get("topics", []),
        "language": data.get("language") or "",
        "stars": int(data.get("stargazers_count", 0)),
        "forks": int(data.get("forks_count", 0)),
    }
