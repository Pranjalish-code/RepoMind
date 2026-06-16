"""
rag/chunker.py — Semantic code chunker for RepoMind AI RAG pipeline.

Language strategies:
  Python  → AST-based (functions, classes, methods), line-based fallback
  JS/TS   → Regex boundary detection (functions, classes, React components)
  Markdown → Split by headings
  JSON/YAML/TOML → Full-file if small, line-based if large
  Other   → Fixed-window line chunker with overlap

All chunkers return a list of CodeChunk objects ready for embedding.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Chunk sizing constants ─────────────────────────────────────────────────────

FALLBACK_CHUNK_LINES: int = 50     # window size for line-based chunking
FALLBACK_CHUNK_OVERLAP: int = 10   # overlap between consecutive windows
MIN_CHUNK_LINES: int = 3           # skip chunks shorter than this
MAX_FULL_FILE_LINES: int = 120     # config files ≤ this → single chunk
MAX_CLASS_LINES_SINGLE: int = 80   # classes ≤ this → single class chunk

# Languages to use full-file (or line-based large) chunking
FULL_FILE_LANGUAGES: frozenset[str] = frozenset(
    {"json", "yaml", "toml", "sql", "xml", "text"}
)

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class CodeChunk:
    """A semantic chunk of source code ready for embedding."""

    repo_id: str
    file_path: str          # POSIX path relative to repo root
    language: str
    symbol_name: str | None
    symbol_type: str | None  # function | method | class | component | section | block | module
    start_line: int          # 1-indexed, inclusive
    end_line: int            # 1-indexed, inclusive
    content: str             # Raw text of this chunk
    content_hash: str        # SHA-256 of content bytes

    def to_metadata(self) -> dict:
        """Return Qdrant payload dict (no 'content' — stored separately)."""
        return {
            "repo_id":      self.repo_id,
            "file_path":    self.file_path,
            "language":     self.language,
            "symbol_name":  self.symbol_name,
            "symbol_type":  self.symbol_type,
            "start_line":   self.start_line,
            "end_line":     self.end_line,
            "content_hash": self.content_hash,
            "content":      self.content,   # stored in payload for retrieval
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _make_chunk(
    repo_id: str,
    file_path: str,
    language: str,
    content: str,
    start_line: int,
    end_line: int,
    symbol_name: str | None = None,
    symbol_type: str | None = None,
) -> CodeChunk | None:
    """Build a CodeChunk, returning None if content is too short or empty."""
    text = content.strip()
    if not text:
        return None
    num_lines = end_line - start_line + 1
    if num_lines < MIN_CHUNK_LINES:
        return None
    return CodeChunk(
        repo_id=repo_id,
        file_path=file_path,
        language=language,
        symbol_name=symbol_name,
        symbol_type=symbol_type,
        start_line=start_line,
        end_line=end_line,
        content=content,
        content_hash=_sha256(content),
    )


# ── Line-based fallback chunker ───────────────────────────────────────────────

def _chunk_by_lines(
    lines: list[str],
    repo_id: str,
    file_path: str,
    language: str,
    symbol_type: str = "block",
) -> list[CodeChunk]:
    """
    Fixed-window chunker with overlap.
    Used as fallback when AST/regex chunking yields nothing.
    """
    if not lines:
        return []

    chunks: list[CodeChunk] = []
    step = max(1, FALLBACK_CHUNK_LINES - FALLBACK_CHUNK_OVERLAP)
    total = len(lines)

    for start in range(0, total, step):
        end = min(start + FALLBACK_CHUNK_LINES, total)
        content = "\n".join(lines[start:end])
        chunk = _make_chunk(
            repo_id, file_path, language, content,
            start_line=start + 1,
            end_line=end,
            symbol_name=None,
            symbol_type=symbol_type,
        )
        if chunk:
            chunks.append(chunk)
        if end >= total:
            break

    return chunks


# ── Python AST chunker ────────────────────────────────────────────────────────

def _ast_node_to_chunk(
    node: ast.AST,
    lines: list[str],
    repo_id: str,
    file_path: str,
    language: str,
    symbol_type: str,
) -> CodeChunk | None:
    """Extract source lines for an AST node and return a CodeChunk."""
    start_0 = getattr(node, "lineno", 1) - 1       # 0-indexed start
    end_0 = getattr(node, "end_lineno", start_0) - 1  # 0-indexed end

    if end_0 < start_0 or start_0 >= len(lines):
        return None

    # For classes that are too large, only take the header (signature + docstring)
    if symbol_type == "class" and (end_0 - start_0 + 1) > MAX_CLASS_LINES_SINGLE:
        # Take first 10 lines (signature + docstring)
        end_0 = min(start_0 + 9, end_0)
        symbol_type = "class_header"

    content = "\n".join(lines[start_0 : end_0 + 1])
    return _make_chunk(
        repo_id, file_path, language, content,
        start_line=start_0 + 1,
        end_line=end_0 + 1,
        symbol_name=getattr(node, "name", None),
        symbol_type=symbol_type,
    )


def _chunk_python(
    content: str,
    lines: list[str],
    repo_id: str,
    file_path: str,
    language: str,
) -> list[CodeChunk]:
    """AST-based Python chunker. Falls back to line chunking on SyntaxError."""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        logger.debug("AST parse failed for %s (%s), using line chunker", file_path, exc)
        return _chunk_by_lines(lines, repo_id, file_path, language)

    chunks: list[CodeChunk] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunk = _ast_node_to_chunk(node, lines, repo_id, file_path, language, "function")
            if chunk:
                chunks.append(chunk)

        elif isinstance(node, ast.ClassDef):
            class_lines = getattr(node, "end_lineno", node.lineno) - node.lineno + 1

            if class_lines <= MAX_CLASS_LINES_SINGLE:
                # Chunk the whole class
                chunk = _ast_node_to_chunk(node, lines, repo_id, file_path, language, "class")
                if chunk:
                    chunks.append(chunk)
            else:
                # Class is large: chunk header + individual methods
                header_chunk = _ast_node_to_chunk(
                    node, lines, repo_id, file_path, language, "class"
                )
                if header_chunk:
                    chunks.append(header_chunk)

                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        mchunk = _ast_node_to_chunk(
                            child, lines, repo_id, file_path, language, "method"
                        )
                        if mchunk:
                            chunks.append(mchunk)

    if not chunks:
        # File has module-level code only (no top-level defs)
        return _chunk_by_lines(lines, repo_id, file_path, language)

    return chunks


# ── JS/TS boundary-based chunker ──────────────────────────────────────────────

# Patterns to detect the START of a named symbol (tested against stripped lines)
_JS_FUNC_DECL = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*[(<]"
)
_JS_CONST_ARROW = re.compile(
    r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$]\w*)\s*=\s*"
    r"(?:React\.memo\(|React\.forwardRef\(|React\.createContext\()?(?:async\s+)?"
    r"(?:\(|[A-Za-z_$])"  # arrow: ( or single param without parens
)
_JS_CLASS_DECL = re.compile(
    r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)"
)
_JS_EXPORT_DEFAULT_FUNC = re.compile(
    r"^export\s+default\s+(?:async\s+)?function\s*(\w*)"
)


@dataclass
class _Boundary:
    line_idx: int   # 0-indexed line where symbol starts
    name: str
    kind: str       # function | component | class


def _find_js_boundaries(lines: list[str]) -> list[_Boundary]:
    """Find all symbol start boundaries in JS/TS/JSX/TSX files."""
    boundaries: list[_Boundary] = []

    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("*"):
            continue

        # Class
        m = _JS_CLASS_DECL.match(stripped)
        if m:
            boundaries.append(_Boundary(i, m.group(1), "class"))
            continue

        # Named function declaration
        m = _JS_FUNC_DECL.match(stripped)
        if m:
            name = m.group(1)
            kind = "component" if name and name[0].isupper() else "function"
            boundaries.append(_Boundary(i, name, kind))
            continue

        # export default function (anonymous or named)
        m = _JS_EXPORT_DEFAULT_FUNC.match(stripped)
        if m:
            name = m.group(1) or "default"
            boundaries.append(_Boundary(i, name, "function"))
            continue

        # const/let arrow functions
        m = _JS_CONST_ARROW.match(stripped)
        if m:
            name = m.group(1)
            # Filter out simple variable assignments (not functions)
            # Heuristic: skip single-word RHS without () or =>
            rest = stripped[m.end():]
            if not any(tok in rest[:30] for tok in ("=>", "(", "function", "async")):
                continue
            kind = "component" if name and name[0].isupper() else "function"
            boundaries.append(_Boundary(i, name, kind))

    return boundaries


def _chunk_js_ts(
    lines: list[str],
    repo_id: str,
    file_path: str,
    language: str,
) -> list[CodeChunk]:
    """Boundary-based chunker for JS/TS/JSX/TSX files."""
    boundaries = _find_js_boundaries(lines)

    if not boundaries:
        return _chunk_by_lines(lines, repo_id, file_path, language)

    chunks: list[CodeChunk] = []
    total = len(lines)

    for i, bnd in enumerate(boundaries):
        start_idx = bnd.line_idx
        # End: line before next boundary, or EOF
        end_idx = (boundaries[i + 1].line_idx - 1) if i + 1 < len(boundaries) else total - 1
        end_idx = min(end_idx, total - 1)

        content = "\n".join(lines[start_idx : end_idx + 1])
        chunk = _make_chunk(
            repo_id, file_path, language, content,
            start_line=start_idx + 1,
            end_line=end_idx + 1,
            symbol_name=bnd.name,
            symbol_type=bnd.kind,
        )
        if chunk:
            chunks.append(chunk)

    return chunks if chunks else _chunk_by_lines(lines, repo_id, file_path, language)


# ── Markdown chunker ──────────────────────────────────────────────────────────

_MD_HEADING = re.compile(r"^(#{1,3})\s+(.+)$")


def _chunk_markdown(
    lines: list[str],
    repo_id: str,
    file_path: str,
    language: str,
) -> list[CodeChunk]:
    """Split Markdown by top-three heading levels."""
    # Collect section start indices and titles
    boundaries: list[tuple[int, str]] = []  # (line_idx, title)

    for i, line in enumerate(lines):
        m = _MD_HEADING.match(line.rstrip())
        if m:
            boundaries.append((i, m.group(2).strip()))

    if not boundaries:
        return _chunk_by_lines(lines, repo_id, file_path, language)

    chunks: list[CodeChunk] = []
    total = len(lines)

    for idx, (start_i, title) in enumerate(boundaries):
        end_i = (boundaries[idx + 1][0] - 1) if idx + 1 < len(boundaries) else total - 1
        content = "\n".join(lines[start_i : end_i + 1])
        chunk = _make_chunk(
            repo_id, file_path, language, content,
            start_line=start_i + 1,
            end_line=end_i + 1,
            symbol_name=title[:100],
            symbol_type="section",
        )
        if chunk:
            chunks.append(chunk)

    return chunks if chunks else _chunk_by_lines(lines, repo_id, file_path, language)


# ── Config/data chunker ───────────────────────────────────────────────────────

def _chunk_config(
    lines: list[str],
    repo_id: str,
    file_path: str,
    language: str,
) -> list[CodeChunk]:
    """
    Full-file chunk for small config/data files.
    Falls back to line-based if the file is large.
    """
    if len(lines) <= MAX_FULL_FILE_LINES:
        content = "\n".join(lines)
        chunk = _make_chunk(
            repo_id, file_path, language, content,
            start_line=1,
            end_line=len(lines),
            symbol_name=Path(file_path).name,
            symbol_type="module",
        )
        return [chunk] if chunk else []

    # Large config file: use line-based
    return _chunk_by_lines(lines, repo_id, file_path, language)


# ── Public dispatcher ─────────────────────────────────────────────────────────

def chunk_file(
    content: str,
    file_path: str,
    language: str,
    repo_id: str,
) -> list[CodeChunk]:
    """
    Chunk a source file into semantic CodeChunk objects.

    Args:
        content:   Raw text content of the file.
        file_path: POSIX-style path relative to the repo root.
        language:  Language label from the scanner (e.g. 'python', 'typescript').
        repo_id:   Repository UUID for metadata.

    Returns:
        List of CodeChunk objects (may be empty for truly unreadable files).
    """
    if not content or not content.strip():
        return []

    lines = content.splitlines()

    if language == "python":
        return _chunk_python(content, lines, repo_id, file_path, language)

    if language in ("javascript", "typescript", "vue", "svelte"):
        return _chunk_js_ts(lines, repo_id, file_path, language)

    if language == "markdown":
        return _chunk_markdown(lines, repo_id, file_path, language)

    if language in FULL_FILE_LANGUAGES:
        return _chunk_config(lines, repo_id, file_path, language)

    # All other languages: line-based fallback
    return _chunk_by_lines(lines, repo_id, file_path, language)
