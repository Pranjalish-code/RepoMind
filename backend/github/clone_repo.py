"""
github/clone_repo.py — Clone GitHub repositories to local disk using GitPython.

Safety guarantees:
  - URL validated against strict regex before cloning.
  - Local path is always resolved inside REPOS_DIR (path-traversal proof).
  - Partial clones are cleaned up on failure.
  - Repos larger than MAX_REPO_SIZE_BYTES are rejected and removed.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import git
from git.exc import GitCommandError, InvalidGitRepositoryError

from config import settings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# 500 MB hard cap on total cloned size (post-clone check)
MAX_REPO_SIZE_BYTES: int = 500 * 1024 * 1024

# Strict GitHub URL pattern — supports https with optional .git suffix
_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/"
    r"([a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38})"  # owner
    r"/"
    r"([a-zA-Z0-9_.\-]{1,100}?)"                              # repo name
    r"(?:\.git)?/?$"
)


# ── Public helpers ────────────────────────────────────────────────────────────

def validate_github_url(repo_url: str) -> tuple[str, str]:
    """
    Validate a GitHub repository URL.

    Returns:
        (owner, repo_name) on success.

    Raises:
        ValueError: if the URL is not a valid public GitHub repo URL.
    """
    url = repo_url.strip().rstrip("/")
    match = _GITHUB_URL_RE.match(url)
    if not match:
        raise ValueError(
            f"Invalid GitHub URL: {url!r}. "
            "Expected: https://github.com/owner/repo-name"
        )
    owner = match.group(1)
    repo_name = match.group(2)
    return owner, repo_name


def get_clone_path(repo_id: str, repo_name: str) -> Path:
    """
    Compute the safe local filesystem path for a repo clone.

    The directory name is ``<repo_id>_<sanitised_repo_name>`` inside
    ``settings.repos_path``.  The resolved path is verified to stay inside
    ``repos_path`` to prevent any path-traversal attack.

    Args:
        repo_id:   UUID of the repo DB record.
        repo_name: Human-readable repo name (will be sanitised).

    Returns:
        Absolute Path inside REPOS_DIR.

    Raises:
        ValueError: if the resolved path escapes REPOS_DIR.
    """
    # Sanitise repo_name: only allow alphanumerics, hyphens, underscores, dots
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", repo_name)[:80]
    dir_name = f"{repo_id}_{safe_name}"

    base = settings.repos_path.resolve()
    target = (base / dir_name).resolve()

    # Guard: resolved target must be a direct child of base (no traversal)
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(
            f"Path traversal detected: computed path {target!r} "
            f"is outside REPOS_DIR {base!r}"
        )

    return target


def clone_repository(
    repo_url: str,
    repo_id: str,
    repo_name: str,
    branch: str | None = None,
) -> Path:
    """
    Clone a GitHub repository to local disk (blocking – run via executor).

    Uses ``--depth 1`` (shallow clone) and skips tags to minimise download
    size.  On any failure the partial clone directory is removed.

    Args:
        repo_url:  Validated HTTPS GitHub URL.
        repo_id:   Repository UUID (used to name the local directory).
        repo_name: Repository name (used for human-readable directory suffix).
        branch:    Branch to clone; if None, uses the remote's default.

    Returns:
        Path to the cloned repository root.

    Raises:
        ValueError:      Path traversal or repo too large.
        GitCommandError: Clone failure (network, auth, 404, etc.).
        OSError:         Disk errors.
    """
    local_path = get_clone_path(repo_id, repo_name)

    # Remove stale partial clone from a previous failed attempt
    if local_path.exists():
        logger.warning("Removing stale clone at %s before re-cloning", local_path)
        shutil.rmtree(local_path, ignore_errors=True)

    clone_kwargs: dict = {
        "depth": 1,
        "multi_options": ["--no-tags"],
    }
    if branch:
        clone_kwargs["branch"] = branch

    logger.info("Cloning %s → %s (shallow, no-tags)", repo_url, local_path)
    try:
        git.Repo.clone_from(repo_url, str(local_path), **clone_kwargs)
    except GitCommandError as exc:
        # Always clean up on failure
        if local_path.exists():
            shutil.rmtree(local_path, ignore_errors=True)
        logger.error("Clone failed: %s", exc)
        raise

    # ── Post-clone size gate ──────────────────────────────────────────────────
    total_size = sum(
        f.stat().st_size
        for f in local_path.rglob("*")
        if f.is_file()
    )
    size_mb = total_size / (1024 ** 2)
    limit_mb = MAX_REPO_SIZE_BYTES / (1024 ** 2)

    if total_size > MAX_REPO_SIZE_BYTES:
        shutil.rmtree(local_path, ignore_errors=True)
        raise ValueError(
            f"Repository too large: {size_mb:.1f} MB (limit: {limit_mb:.0f} MB). "
            "Only repositories under 500 MB are supported."
        )

    logger.info(
        "Clone complete: %s  (%.1f MB, %d files)",
        local_path.name,
        size_mb,
        sum(1 for _ in local_path.rglob("*") if _.is_file()),
    )
    return local_path


def remove_clone(local_path: str | Path) -> None:
    """
    Remove a cloned repository directory from disk.

    Safe to call even if the path does not exist.
    """
    path = Path(local_path).resolve()
    base = settings.repos_path.resolve()

    # Safety: only delete directories that are inside REPOS_DIR
    try:
        path.relative_to(base)
    except ValueError:
        logger.error(
            "Refusing to delete %s — it is outside REPOS_DIR %s", path, base
        )
        return

    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        logger.info("Deleted clone at %s", path)
