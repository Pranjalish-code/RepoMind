"""
github/diff_parser.py — Parse GitHub unified diffs into structured data.

Given the raw 'patch' string returned by the GitHub Files API, this module
extracts:
  - Changed line numbers (added / removed / context) per file
  - Hunk headers (original range, new range)
  - Per-line annotations (line type, old_line, new_line, content)

Design decisions
----------------
* Pure Python, no external dependencies.
* All functions are synchronous — safe to call directly.
* Line numbers use 1-based indexing throughout (matches GitHub UI).
* A line with no patch (binary file or large file) returns empty structures.
* Secret-scanning is NOT done here — that's the output guardrail's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# Regex matching hunk header: @@ -old_start,old_count +new_start,new_count @@
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@"
)

LineType = Literal["added", "removed", "context", "no_newline"]


@dataclass
class DiffLine:
    """A single line in a unified diff."""

    line_type: LineType     # 'added' | 'removed' | 'context' | 'no_newline'
    old_line: int | None    # line number in old file (None for added lines)
    new_line: int | None    # line number in new file (None for removed lines)
    content: str            # raw line content (without the +/-/space prefix)


@dataclass
class DiffHunk:
    """A single @@ ... @@ hunk from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    """Parsed diff for one changed file."""

    filename: str
    status: str                      # added | removed | modified | renamed
    additions: int
    deletions: int
    hunks: list[DiffHunk]

    @property
    def added_lines(self) -> set[int]:
        """Set of new-file line numbers that were ADDED (+lines)."""
        return {
            ln.new_line
            for hunk in self.hunks
            for ln in hunk.lines
            if ln.line_type == "added" and ln.new_line is not None
        }

    @property
    def removed_lines(self) -> set[int]:
        """Set of old-file line numbers that were REMOVED (-lines)."""
        return {
            ln.old_line
            for hunk in self.hunks
            for ln in hunk.lines
            if ln.line_type == "removed" and ln.old_line is not None
        }

    @property
    def changed_line_range(self) -> tuple[int, int] | None:
        """
        (min_new_line, max_new_line) spanning all added/context lines,
        or None if no lines are available.
        """
        new_lines = [
            ln.new_line
            for hunk in self.hunks
            for ln in hunk.lines
            if ln.new_line is not None
        ]
        if not new_lines:
            return None
        return min(new_lines), max(new_lines)

    def contains_new_line(self, line_number: int) -> bool:
        """Return True if line_number appears in the new-file side of this diff."""
        return line_number in self.added_lines or any(
            ln.new_line == line_number
            for hunk in self.hunks
            for ln in hunk.lines
            if ln.line_type == "context"
        )


# ── Core parser ───────────────────────────────────────────────────────────────

def parse_patch(
    patch_text: str,
    filename: str = "",
    status: str = "modified",
    additions: int = 0,
    deletions: int = 0,
) -> FileDiff:
    """
    Parse a GitHub unified diff patch string into a structured FileDiff.

    Args:
        patch_text: Raw unified diff string from the GitHub API
                    (the 'patch' field on a PR file object).
        filename:   Filename for labelling (not parsed from patch).
        status:     GitHub file status string.
        additions:  Total additions (from GitHub API, for cross-check).
        deletions:  Total deletions (from GitHub API, for cross-check).

    Returns:
        FileDiff with all hunks parsed.

    Notes:
        - Empty patch_text → FileDiff with no hunks (binary / large file).
        - Lines starting with '\\' (no newline at end of file) get type
          'no_newline' and do not increment line counters.
    """
    file_diff = FileDiff(
        filename=filename,
        status=status,
        additions=additions,
        deletions=deletions,
        hunks=[],
    )

    if not patch_text:
        return file_diff

    current_hunk: DiffHunk | None = None
    old_line = 0
    new_line = 0

    for raw_line in patch_text.splitlines():
        # ── Hunk header ───────────────────────────────────────────────────────
        hunk_m = _HUNK_HEADER_RE.match(raw_line)
        if hunk_m:
            old_start  = int(hunk_m.group(1))
            old_count  = int(hunk_m.group(2)) if hunk_m.group(2) is not None else 1
            new_start  = int(hunk_m.group(3))
            new_count  = int(hunk_m.group(4)) if hunk_m.group(4) is not None else 1

            current_hunk = DiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
            file_diff.hunks.append(current_hunk)

            old_line = old_start
            new_line = new_start
            continue

        if current_hunk is None:
            # Lines before the first hunk header — skip
            continue

        # ── Diff content lines ────────────────────────────────────────────────
        if raw_line.startswith("+"):
            content = raw_line[1:]
            current_hunk.lines.append(
                DiffLine("added", old_line=None, new_line=new_line, content=content)
            )
            new_line += 1

        elif raw_line.startswith("-"):
            content = raw_line[1:]
            current_hunk.lines.append(
                DiffLine("removed", old_line=old_line, new_line=None, content=content)
            )
            old_line += 1

        elif raw_line.startswith("\\"):
            # "\ No newline at end of file"
            current_hunk.lines.append(
                DiffLine("no_newline", old_line=None, new_line=None, content=raw_line[1:].strip())
            )
            # Do NOT increment counters

        else:
            # Context line (starts with space, or empty for short patches)
            content = raw_line[1:] if raw_line.startswith(" ") else raw_line
            current_hunk.lines.append(
                DiffLine("context", old_line=old_line, new_line=new_line, content=content)
            )
            old_line += 1
            new_line += 1

    return file_diff


def parse_pr_files(pr_files: list[dict]) -> dict[str, FileDiff]:
    """
    Parse all files from a PR (as returned by fetch_pr_files).

    Args:
        pr_files: List of file dicts from GitHub API, each containing:
                  filename, status, additions, deletions, patch.

    Returns:
        Dict mapping filename → FileDiff (parsed).
        Files with no patch (binary/large) are included with empty hunks.
    """
    result: dict[str, FileDiff] = {}
    for f in pr_files:
        filename = f.get("filename", "")
        if not filename:
            continue
        result[filename] = parse_patch(
            patch_text=f.get("patch", "") or "",
            filename=filename,
            status=f.get("status", "modified"),
            additions=f.get("additions", 0),
            deletions=f.get("deletions", 0),
        )
    return result


def build_diff_context_block(
    file_diffs: dict[str, FileDiff],
    max_lines_per_file: int = 150,
    max_total_chars: int = 40_000,
) -> str:
    """
    Render parsed diffs into a human-readable context block for the LLM.

    Truncates per-file to max_lines_per_file lines to avoid context overflow.
    Stops adding files after max_total_chars total characters.

    Format per file:
        ### filename (added/modified/removed) +X -Y
        @@ hunk header @@
        +added line
        -removed line
         context line
        ...

    Returns:
        A single string suitable for inclusion in an LLM prompt.
    """
    parts: list[str] = []
    total_chars = 0

    for filename, fd in file_diffs.items():
        if total_chars >= max_total_chars:
            parts.append(f"\n[... {len(file_diffs) - len(parts)} more files truncated ...]")
            break

        header = (
            f"### {filename}  ({fd.status})  "
            f"+{fd.additions} -{fd.deletions}\n"
        )
        lines_block: list[str] = []
        line_count = 0

        for hunk in fd.hunks:
            hunk_header = (
                f"@@ -{hunk.old_start},{hunk.old_count} "
                f"+{hunk.new_start},{hunk.new_count} @@"
            )
            lines_block.append(hunk_header)

            for dl in hunk.lines:
                if line_count >= max_lines_per_file:
                    lines_block.append(f"  [... truncated after {max_lines_per_file} lines ...]")
                    break
                prefix = {"added": "+", "removed": "-", "context": " ", "no_newline": "\\"}.get(
                    dl.line_type, " "
                )
                lines_block.append(f"{prefix}{dl.content}")
                line_count += 1

            if line_count >= max_lines_per_file:
                break

        file_block = header + "\n".join(lines_block)
        parts.append(file_block)
        total_chars += len(file_block)

    return "\n\n".join(parts)


def get_changed_file_paths(pr_files: list[dict]) -> set[str]:
    """Return a set of all filenames changed in the PR."""
    return {f["filename"] for f in pr_files if f.get("filename")}


def line_in_diff(file_diffs: dict[str, FileDiff], filename: str, line_number: int) -> bool:
    """
    Return True if *line_number* (new-file side) appears in the diff for *filename*.

    Used by PR guardrails to validate that a reported line is actually in the PR.
    """
    fd = file_diffs.get(filename)
    if fd is None:
        return False
    return fd.contains_new_line(line_number)
