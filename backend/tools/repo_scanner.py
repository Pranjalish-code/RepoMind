"""
tools/repo_scanner.py — Walk a cloned repository and collect indexable file metadata.

Design decisions:
  - All path comparisons use resolved absolute paths to prevent traversal.
  - Directory parts are checked against IGNORED_DIRS at every level (not just the top).
  - Files named .env (exact match) are always skipped regardless of extension.
  - Binary detection uses a null-byte heuristic on the first 8 KB — fast and accurate
    for source code vs. compiled/image content.
  - Content hash is SHA-256 of raw bytes — collision-resistant and consistent.
  - All paths in ScannedFile use POSIX separators for cross-platform consistency.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Ignore lists ──────────────────────────────────────────────────────────────

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "venv",
        ".venv",
        "dist",
        "build",
        "coverage",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        ".eggs",
        "htmlcov",
        "site-packages",
        ".next",
        ".nuxt",
        "out",
        ".turbo",
    }
)

IGNORED_FILENAMES: frozenset[str] = frozenset(
    {
        ".env",           # ← CRITICAL: never read secrets
        ".DS_Store",
        ".gitignore",
        ".gitattributes",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "Pipfile.lock",
        "composer.lock",
        "Cargo.lock",
    }
)

# Extension → language label
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".py":   "python",
    ".js":   "javascript",
    ".jsx":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "typescript",
    ".json": "json",
    ".md":   "markdown",
    ".html": "html",
    ".css":  "css",
    ".yaml": "yaml",
    ".yml":  "yaml",
    ".toml": "toml",
    ".sh":   "shell",
    ".bash": "shell",
    ".rs":   "rust",
    ".go":   "go",
    ".java": "java",
    ".cpp":  "cpp",
    ".c":    "c",
    ".h":    "c",
    ".rb":   "ruby",
    ".php":  "php",
    ".vue":  "vue",
    ".svelte": "svelte",
    ".graphql": "graphql",
    ".sql":  "sql",
    ".xml":  "xml",
    ".env.example": "text",   # Safe – no secrets
}

# ── Limits ────────────────────────────────────────────────────────────────────

MAX_FILE_SIZE_BYTES: int = 1 * 1024 * 1024   # 1 MB per file
BINARY_PROBE_SIZE: int = 8_192               # bytes to read for binary detection


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ScannedFile:
    """Metadata for a single indexable source file."""

    file_path: str       # POSIX-style path relative to repo root
    language: str        # Detected language label
    size_bytes: int      # File size in bytes
    content_hash: str    # SHA-256 hex digest of raw file bytes

    def to_dict(self) -> dict:
        return {
            "file_path":    self.file_path,
            "language":     self.language,
            "size_bytes":   self.size_bytes,
            "content_hash": self.content_hash,
        }


@dataclass
class ScanSummary:
    """Aggregate statistics returned alongside the file list."""

    total_indexed: int = 0
    skipped_ignored: int = 0
    skipped_extension: int = 0
    skipped_large: int = 0
    skipped_binary: int = 0
    skipped_empty: int = 0
    skipped_error: int = 0


# ── Private helpers ───────────────────────────────────────────────────────────

def _any_part_ignored(rel: Path) -> bool:
    """
    Return True if any directory component of *rel* is in IGNORED_DIRS
    or starts with a dot (hidden directories like .git are already in the
    set, but this catches arbitrary hidden dirs like .cache too).

    ``rel`` is a Path relative to the repo root (no leading slash).
    """
    # Check every directory segment (all parts except the final filename)
    for part in rel.parts[:-1]:
        if part in IGNORED_DIRS:
            return True
        # Hidden directories (start with '.') are skipped, EXCEPT
        # top-level files like '.env.example' which have no parent dirs.
        if part.startswith("."):
            return True
    return False


def _is_binary(abs_path: Path) -> bool:
    """
    Return True if the file looks like a binary (non-text) file.

    Reads at most BINARY_PROBE_SIZE bytes and checks for null bytes —
    a reliable heuristic used by git, grep, and ripgrep.
    """
    try:
        with abs_path.open("rb") as fh:
            chunk = fh.read(BINARY_PROBE_SIZE)
        return b"\x00" in chunk
    except OSError:
        return True   # If we can't probe it, treat as binary → skip


def _sha256_file(abs_path: Path) -> str:
    """Return the SHA-256 hex digest of a file, read in 64-KB chunks."""
    h = hashlib.sha256()
    try:
        with abs_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                h.update(chunk)
    except OSError as exc:
        raise OSError(f"Cannot read {abs_path}: {exc}") from exc
    return h.hexdigest()


# ── Public API ────────────────────────────────────────────────────────────────

def scan_repository(repo_root: str | Path) -> tuple[list[ScannedFile], ScanSummary]:
    """
    Recursively walk *repo_root* and collect metadata for all indexable files.

    Skips:
      • Directories in IGNORED_DIRS (at any depth)
      • Hidden directories (start with '.')
      • Filenames in IGNORED_FILENAMES (e.g. .env)
      • Files with unsupported extensions
      • Empty files
      • Files exceeding MAX_FILE_SIZE_BYTES
      • Binary files (null-byte heuristic)
      • Files that cause read errors

    Args:
        repo_root: Absolute path to the cloned repository root directory.

    Returns:
        A tuple of:
          - List of ScannedFile instances (one per indexable file).
          - ScanSummary with skip/index counts.

    Raises:
        ValueError: if repo_root does not exist or is not a directory.
    """
    root = Path(repo_root).resolve()

    if not root.exists():
        raise ValueError(f"Repository root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Repository root is not a directory: {root}")

    results: list[ScannedFile] = []
    summary = ScanSummary()

    for abs_path in sorted(root.rglob("*")):
        if not abs_path.is_file():
            continue

        # ── Build relative path (POSIX) ───────────────────────────────────────
        try:
            rel = abs_path.relative_to(root)
        except ValueError:
            # Should never happen since we're globbing from root
            summary.skipped_error += 1
            continue

        rel_posix = rel.as_posix()

        # ── 1. Skip ignored directories ───────────────────────────────────────
        if _any_part_ignored(rel):
            summary.skipped_ignored += 1
            continue

        # ── 2. Skip ignored filenames (.env, .DS_Store, lock files …) ─────────
        if abs_path.name in IGNORED_FILENAMES:
            logger.debug("Skipping ignored file: %s", rel_posix)
            summary.skipped_ignored += 1
            continue

        # ── 3. Extension check ────────────────────────────────────────────────
        # Use lower-cased suffix; handle compound like '.env.example' via name check
        suffix = abs_path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            summary.skipped_extension += 1
            continue

        language = SUPPORTED_EXTENSIONS[suffix]

        # ── 4. Size check ─────────────────────────────────────────────────────
        try:
            size = abs_path.stat().st_size
        except OSError as exc:
            logger.warning("Cannot stat %s: %s", rel_posix, exc)
            summary.skipped_error += 1
            continue

        if size == 0:
            summary.skipped_empty += 1
            continue

        if size > MAX_FILE_SIZE_BYTES:
            logger.debug(
                "Skipping large file (%.1f KB): %s",
                size / 1024,
                rel_posix,
            )
            summary.skipped_large += 1
            continue

        # ── 5. Binary check ───────────────────────────────────────────────────
        if _is_binary(abs_path):
            logger.debug("Skipping binary file: %s", rel_posix)
            summary.skipped_binary += 1
            continue

        # ── 6. Compute content hash ───────────────────────────────────────────
        try:
            content_hash = _sha256_file(abs_path)
        except OSError as exc:
            logger.warning("Cannot hash %s: %s", rel_posix, exc)
            summary.skipped_error += 1
            continue

        # ── 7. Emit result ────────────────────────────────────────────────────
        results.append(
            ScannedFile(
                file_path=rel_posix,
                language=language,
                size_bytes=size,
                content_hash=content_hash,
            )
        )
        summary.total_indexed += 1

    logger.info(
        "Scan complete — indexed: %d | ignored: %d | ext-skip: %d | "
        "large: %d | binary: %d | empty: %d | errors: %d",
        summary.total_indexed,
        summary.skipped_ignored,
        summary.skipped_extension,
        summary.skipped_large,
        summary.skipped_binary,
        summary.skipped_empty,
        summary.skipped_error,
    )
    return results, summary
