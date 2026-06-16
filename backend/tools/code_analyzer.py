"""
tools/code_analyzer.py — Static code analysis for architecture detection.

Scans a cloned repository and builds a component graph by:
  1. Detecting directories → frontend / backend / service / database layers
  2. Parsing Python imports (import / from … import)
  3. Parsing JS/TS imports (import … from, require())
  4. Detecting database usage (ORM, raw queries, DB clients)
  5. Detecting external API usage (requests, axios, fetch, httpx, etc.)
  6. Inspecting package.json for framework / library signals
  7. Detecting config filenames (Dockerfile, docker-compose, nginx.conf, etc.)
  8. Detecting route definitions (FastAPI, Express, Flask, Next.js pages)

All analysis is pure-Python static analysis — no execution, no LLM.

Output:  AnalysisResult dataclass with:
  - components      : list[ComponentInfo]   — detected system components
  - edges           : list[tuple[str, str]] — directed connections
  - confidence      : int                   — 0-100
  - raw_facts       : dict                  — debug metadata

Security notes
--------------
* .env files are NEVER read (IGNORED_FILENAMES check).
* File content is read as UTF-8 with errors='replace' — never executed.
* No subprocess calls.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from tools.repo_scanner import IGNORED_DIRS, IGNORED_FILENAMES

logger = logging.getLogger(__name__)

# ── File size cap for content reading ─────────────────────────────────────────
_MAX_READ_BYTES = 512 * 1024  # 512 KB per file for import analysis
_MAX_FILES_PER_LANG = 200     # cap to avoid huge monorepos grinding


# ═══════════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ComponentInfo:
    """A detected architectural component."""
    name: str          # Canonical label (used as Mermaid node ID)
    kind: str          # frontend | backend | database | auth | api | service | config
    label: str         # Human-readable display label
    evidence: list[str] = field(default_factory=list)  # files/dirs that triggered detection


@dataclass
class AnalysisResult:
    """Full static analysis result for a repository."""
    components: list[ComponentInfo]
    edges: list[tuple[str, str]]   # (from_node_name, to_node_name)
    confidence: int                 # 0-100
    raw_facts: dict                 # debug info


# ═══════════════════════════════════════════════════════════════════════════════
# Detection patterns
# ═══════════════════════════════════════════════════════════════════════════════

# Directory name → component kind + label
_DIR_SIGNALS: list[tuple[re.Pattern[str], str, str, str]] = [
    # (pattern, kind, name, label)
    (re.compile(r"^frontend$|^client$|^web$|^ui$|^app$", re.I),      "frontend",  "Frontend",    "Frontend (UI)"),
    (re.compile(r"^react|^next|^vue|^svelte|^angular",   re.I),      "frontend",  "Frontend",    "Frontend (UI)"),
    (re.compile(r"^backend$|^server$|^api$|^service$",   re.I),      "backend",   "BackendAPI",  "Backend API"),
    (re.compile(r"^src$",                                 re.I),      "backend",   "BackendAPI",  "Backend API"),
    (re.compile(r"^auth$|^authentication$|^identity$",   re.I),      "auth",      "AuthService", "Auth Service"),
    (re.compile(r"^db$|^database$|^models$|^migrations$",re.I),      "database",  "Database",    "Database"),
    (re.compile(r"^cache$|^redis$",                       re.I),      "service",   "Cache",       "Cache (Redis)"),
    (re.compile(r"^queue$|^worker$|^jobs$|^tasks$",       re.I),      "service",   "Queue",       "Message Queue"),
    (re.compile(r"^storage$|^s3$|^uploads$",              re.I),      "service",   "Storage",     "Object Storage"),
    (re.compile(r"^email$|^mail$|^notifications?$",       re.I),      "service",   "Notifications", "Notification Service"),
    (re.compile(r"^gateway$|^proxy$|^nginx$",             re.I),      "service",   "Gateway",     "API Gateway"),
    (re.compile(r"^mobile$|^ios$|^android$|^react-native",re.I),     "frontend",  "MobileApp",   "Mobile App"),
]

# File patterns → component signal
_FILE_SIGNALS: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"docker-compose\.(yml|yaml)$",  re.I), "config",   "DockerCompose", "Docker Compose"),
    (re.compile(r"^Dockerfile$",                  re.I), "config",   "Docker",        "Docker"),
    (re.compile(r"nginx\.conf$",                  re.I), "service",  "Nginx",         "Nginx (Reverse Proxy)"),
    (re.compile(r"^\.github/workflows/",          re.I), "config",   "CI",            "CI/CD Pipeline"),
    (re.compile(r"package\.json$",                re.I), "frontend", "Frontend",      "Frontend (UI)"),
    (re.compile(r"requirements\.txt$|pyproject\.toml$|setup\.py$", re.I), "backend", "BackendAPI", "Backend API"),
    (re.compile(r"Pipfile$",                      re.I), "backend",  "BackendAPI",    "Backend API"),
    (re.compile(r"go\.mod$",                      re.I), "backend",  "BackendAPI",    "Backend Go API"),
    (re.compile(r"pom\.xml$|build\.gradle$",      re.I), "backend",  "BackendAPI",    "Backend Java API"),
    (re.compile(r"Cargo\.toml$",                  re.I), "backend",  "BackendAPI",    "Backend Rust API"),
]

# Python import patterns → dependency detection
_PY_IMPORT_RE = re.compile(
    r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))",
    re.MULTILINE,
)

# JS/TS import patterns
_JS_IMPORT_RE = re.compile(
    r'(?:import\s+.*?from\s+["\']([^"\']+)["\']|require\s*\(\s*["\']([^"\']+)["\']\s*\))',
    re.MULTILINE,
)

# Database usage signals (Python)
_DB_PY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsqlalchemy\b",              re.I), "SQLAlchemy ORM"),
    (re.compile(r"\bpsycopg2\b|\bpsycopg\b",   re.I), "PostgreSQL"),
    (re.compile(r"\bpymysql\b|\bmysql\b",       re.I), "MySQL"),
    (re.compile(r"\bsqlite3\b|\baiosqlite\b",   re.I), "SQLite"),
    (re.compile(r"\bmongodb\b|\bmotor\b|\bpymongo\b", re.I), "MongoDB"),
    (re.compile(r"\bredis\b",                   re.I), "Redis"),
    (re.compile(r"\belasticsearch\b",           re.I), "Elasticsearch"),
    (re.compile(r"\bdynamodb\b",                re.I), "DynamoDB"),
    (re.compile(r"\bprisma\b",                  re.I), "Prisma ORM"),
]

# Database usage signals (JS/TS)
_DB_JS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bpg\b|postgres",             re.I), "PostgreSQL"),
    (re.compile(r"\bmysql\b|\bmysql2\b",         re.I), "MySQL"),
    (re.compile(r"\bmongoose\b|\bmongodb\b",     re.I), "MongoDB"),
    (re.compile(r"\bredis\b|ioredis\b",          re.I), "Redis"),
    (re.compile(r"\bsqlite\b|better-sqlite",     re.I), "SQLite"),
    (re.compile(r"\bprisma\b",                   re.I), "Prisma ORM"),
    (re.compile(r"\bsequelize\b",                re.I), "Sequelize ORM"),
    (re.compile(r"\bdrizzle\b",                  re.I), "Drizzle ORM"),
    (re.compile(r"\bfirebase\b|firestore",       re.I), "Firebase"),
    (re.compile(r"\bsupabase\b",                 re.I), "Supabase"),
]

# External API call patterns
_API_PY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brequests\b",          re.I), "HTTP Client"),
    (re.compile(r"\bhttpx\b",             re.I), "HTTP Client"),
    (re.compile(r"\baiohttp\b",           re.I), "Async HTTP Client"),
    (re.compile(r"\bopenai\b",            re.I), "OpenAI API"),
    (re.compile(r"\bstripe\b",            re.I), "Stripe API"),
    (re.compile(r"\btwilio\b",            re.I), "Twilio API"),
    (re.compile(r"\bsendgrid\b",          re.I), "SendGrid API"),
    (re.compile(r"\baws\b|boto3\b",       re.I), "AWS SDK"),
]

_API_JS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\baxios\b",             re.I), "HTTP Client"),
    (re.compile(r"\bfetch\b",             re.I), "Fetch API"),
    (re.compile(r"\bopenai\b",            re.I), "OpenAI API"),
    (re.compile(r"\bstripe\b",            re.I), "Stripe API"),
    (re.compile(r"\btwilio\b",            re.I), "Twilio API"),
    (re.compile(r"\bsendgrid\b",          re.I), "SendGrid API"),
    (re.compile(r"@aws-sdk",              re.I), "AWS SDK"),
]

# Auth signals
_AUTH_PY_PATTERNS = [
    re.compile(r"\bjwt\b|python-jose|pyjwt",    re.I),
    re.compile(r"\bpasslib\b|bcrypt\b",          re.I),
    re.compile(r"\boauth\b",                     re.I),
    re.compile(r"\bfastapi.security\b",          re.I),
    re.compile(r"\bdjango.contrib.auth\b",       re.I),
]
_AUTH_JS_PATTERNS = [
    re.compile(r"\bnext-auth\b|@auth/",          re.I),
    re.compile(r"\bpassport\b",                  re.I),
    re.compile(r"\bjwt\b|jsonwebtoken",          re.I),
    re.compile(r"\bclerk\b",                     re.I),
    re.compile(r"\bauth0\b",                     re.I),
    re.compile(r"\bfirebase.auth\b",             re.I),
]

# Framework detection for frontend label refinement
_FRONTEND_FRAMEWORK_RE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'"next"',                re.I), "Next.js"),
    (re.compile(r'"react"',               re.I), "React"),
    (re.compile(r'"vue"',                 re.I), "Vue.js"),
    (re.compile(r'"svelte"',              re.I), "Svelte"),
    (re.compile(r'"nuxt"',                re.I), "Nuxt.js"),
    (re.compile(r'"@angular',             re.I), "Angular"),
    (re.compile(r'"vite"',                re.I), "Vite"),
]

# Backend framework detection
_BACKEND_FRAMEWORK_RE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfastapi\b",           re.I), "FastAPI"),
    (re.compile(r"\bdjango\b",            re.I), "Django"),
    (re.compile(r"\bflask\b",             re.I), "Flask"),
    (re.compile(r"\bexpress\b",           re.I), "Express.js"),
    (re.compile(r"\bhono\b",              re.I), "Hono"),
    (re.compile(r"\bfastify\b",           re.I), "Fastify"),
    (re.compile(r"\bnestjs\b|@nestjs",    re.I), "NestJS"),
    (re.compile(r"\bspring\b",            re.I), "Spring Boot"),
    (re.compile(r"\bgin\b|echo\b",        re.I), "Go HTTP Framework"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_read(path: Path) -> str:
    """Read up to _MAX_READ_BYTES from a file as UTF-8 text."""
    try:
        raw = path.read_bytes()[:_MAX_READ_BYTES]
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _is_safe_to_read(path: Path) -> bool:
    """Return True if the file is safe to read for analysis."""
    if path.name in IGNORED_FILENAMES:
        return False
    if path.name.startswith(".env"):
        return False
    return True


def _walk_files(root: Path) -> list[Path]:
    """Walk root, skipping ignored dirs, return list of file paths."""
    results: list[Path] = []
    try:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            # Skip ignored dirs
            if any(part in IGNORED_DIRS or part.startswith(".") for part in rel.parts[:-1]):
                continue
            results.append(p)
    except Exception as exc:
        logger.warning("Walk error under %s: %s", root, exc)
    return results


def _make_safe_node_id(name: str) -> str:
    """Convert a display name to a Mermaid-safe node ID (alphanumeric + underscore)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_") or "Node"


# ═══════════════════════════════════════════════════════════════════════════════
# Analyzer
# ═══════════════════════════════════════════════════════════════════════════════

class CodeAnalyzer:
    """
    Static code analyzer — builds a component graph from a repository.

    Usage::

        analyzer = CodeAnalyzer("/path/to/cloned/repo")
        result = analyzer.analyze()
    """

    def __init__(self, repo_root: str | Path) -> None:
        self.root = Path(repo_root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"Repository root not found: {self.root}")

        self._components: dict[str, ComponentInfo] = {}  # name → ComponentInfo
        self._edges: set[tuple[str, str]] = set()
        self._facts: dict = {
            "frameworks": [],
            "databases": [],
            "auth_detected": False,
            "external_apis": [],
            "config_files": [],
            "py_files": 0,
            "js_files": 0,
        }

    # ── Component registry ────────────────────────────────────────────────────

    def _add_component(
        self,
        name: str,
        kind: str,
        label: str,
        evidence: str = "",
    ) -> None:
        if name not in self._components:
            self._components[name] = ComponentInfo(
                name=name, kind=kind, label=label, evidence=[]
            )
        if evidence:
            self._components[name].evidence.append(evidence)

    def _add_edge(self, src: str, dst: str) -> None:
        if src != dst and src in self._components and dst in self._components:
            self._edges.add((src, dst))

    # ── Phase 1: directory scan ────────────────────────────────────────────────

    def _scan_directories(self) -> None:
        """Detect components from top-level and second-level directory names."""
        try:
            top_dirs = [d for d in self.root.iterdir() if d.is_dir()
                        and d.name not in IGNORED_DIRS and not d.name.startswith(".")]
        except Exception:
            return

        for d in top_dirs:
            for pattern, kind, name, label in _DIR_SIGNALS:
                if pattern.search(d.name):
                    self._add_component(name, kind, label, evidence=f"dir:{d.name}")
                    break

    # ── Phase 2: config file detection ────────────────────────────────────────

    def _scan_config_files(self) -> None:
        """Detect components from config filenames."""
        for p in self.root.iterdir():
            if not p.is_file():
                continue
            for pattern, kind, name, label in _FILE_SIGNALS:
                if pattern.search(p.name):
                    self._add_component(name, kind, label, evidence=f"file:{p.name}")
                    self._facts["config_files"].append(p.name)
                    break

        # Also check .github/workflows
        workflow_dir = self.root / ".github" / "workflows"
        if workflow_dir.is_dir():
            self._add_component("CI", "config", "CI/CD Pipeline", evidence=".github/workflows/")

    # ── Phase 3: package.json analysis ────────────────────────────────────────

    def _analyze_package_json(self) -> None:
        """Parse package.json to detect frontend frameworks and dependencies."""
        pkg_path = self.root / "package.json"
        if not pkg_path.is_file():
            # Look in subdirs
            for candidate in self.root.rglob("package.json"):
                if not any(p in IGNORED_DIRS for p in candidate.parts):
                    pkg_path = candidate
                    break
            else:
                return

        content = _safe_read(pkg_path)
        if not content:
            return

        try:
            pkg = json.loads(content)
        except json.JSONDecodeError:
            return

        deps: dict = {}
        deps.update(pkg.get("dependencies", {}))
        deps.update(pkg.get("devDependencies", {}))
        deps_text = json.dumps(deps)

        # Frontend framework detection
        for pattern, framework in _FRONTEND_FRAMEWORK_RE:
            if pattern.search(deps_text):
                self._facts["frameworks"].append(framework)
                # Refine frontend component label
                if "Frontend" in self._components:
                    self._components["Frontend"].label = f"Frontend ({framework})"
                else:
                    self._add_component("Frontend", "frontend", f"Frontend ({framework})", evidence="package.json")
                break  # only first match

        # JS DB signals
        for pattern, db_name in _DB_JS_PATTERNS:
            if pattern.search(deps_text):
                if db_name not in self._facts["databases"]:
                    self._facts["databases"].append(db_name)
                    db_node = _make_safe_node_id(db_name.replace(" ", ""))
                    self._add_component(db_node, "database", db_name, evidence="package.json")

        # Auth signals
        for pattern in _AUTH_JS_PATTERNS:
            if pattern.search(deps_text):
                self._facts["auth_detected"] = True
                self._add_component("AuthService", "auth", "Auth Service", evidence="package.json")
                break

    # ── Phase 4: Python file analysis ─────────────────────────────────────────

    def _analyze_python_files(self, all_files: list[Path]) -> None:
        """Scan .py files for imports, DB usage, auth, and API calls."""
        py_files = [f for f in all_files if f.suffix == ".py" and _is_safe_to_read(f)]
        self._facts["py_files"] = len(py_files)

        seen_db: set[str] = set()
        seen_api: set[str] = set()
        has_backend = False
        has_routes = False

        for i, fp in enumerate(py_files[:_MAX_FILES_PER_LANG]):
            content = _safe_read(fp)
            if not content:
                continue

            rel = fp.relative_to(self.root).as_posix()

            # Backend framework detection
            for pattern, framework in _BACKEND_FRAMEWORK_RE:
                if pattern.search(content):
                    if framework not in self._facts["frameworks"]:
                        self._facts["frameworks"].append(framework)
                    has_backend = True
                    if "BackendAPI" in self._components:
                        self._components["BackendAPI"].label = f"Backend API ({framework})"
                    else:
                        self._add_component("BackendAPI", "backend", f"Backend API ({framework})", evidence=rel)

            # Route detection (FastAPI / Flask / Django)
            if re.search(r'@(app|router)\.(get|post|put|delete|patch)\s*\(', content):
                has_routes = True

            # DB usage
            for pattern, db_name in _DB_PY_PATTERNS:
                if pattern.search(content) and db_name not in seen_db:
                    seen_db.add(db_name)
                    self._facts["databases"].append(db_name)
                    db_node = _make_safe_node_id(db_name.replace(" ", ""))
                    self._add_component(db_node, "database", db_name, evidence=rel)

            # Auth usage
            if not self._facts["auth_detected"]:
                for pattern in _AUTH_PY_PATTERNS:
                    if pattern.search(content):
                        self._facts["auth_detected"] = True
                        self._add_component("AuthService", "auth", "Auth Service", evidence=rel)
                        break

            # External API calls
            for pattern, api_name in _API_PY_PATTERNS:
                if pattern.search(content) and api_name not in seen_api:
                    seen_api.add(api_name)
                    if api_name not in self._facts["external_apis"]:
                        self._facts["external_apis"].append(api_name)

        if has_backend or has_routes:
            if "BackendAPI" not in self._components:
                self._add_component("BackendAPI", "backend", "Backend API", evidence="*.py")

    # ── Phase 5: JS/TS file analysis ──────────────────────────────────────────

    def _analyze_js_files(self, all_files: list[Path]) -> None:
        """Scan .js/.ts/.jsx/.tsx files for imports, DB usage, and auth."""
        js_exts = {".js", ".jsx", ".ts", ".tsx"}
        js_files = [f for f in all_files if f.suffix.lower() in js_exts and _is_safe_to_read(f)]
        self._facts["js_files"] = len(js_files)

        seen_db: set[str] = set()
        seen_api: set[str] = set()
        has_frontend = False
        has_routes = False

        for fp in js_files[:_MAX_FILES_PER_LANG]:
            content = _safe_read(fp)
            if not content:
                continue

            rel = fp.relative_to(self.root).as_posix()

            # Frontend signals (JSX / TSX / component files)
            if fp.suffix.lower() in (".jsx", ".tsx") or "component" in rel.lower():
                has_frontend = True

            # Route/page detection (Next.js pages/, app/)
            if re.search(r'(?:pages|app)/.*\.(jsx?|tsx?)$', rel) or \
               re.search(r'createRouter|useRouter|BrowserRouter', content):
                has_routes = True

            # Backend framework (Express / NestJS)
            for pattern, framework in _BACKEND_FRAMEWORK_RE:
                if pattern.search(content):
                    if framework not in self._facts["frameworks"]:
                        self._facts["frameworks"].append(framework)
                    self._add_component("BackendAPI", "backend", f"Backend API ({framework})", evidence=rel)

            # DB usage
            for pattern, db_name in _DB_JS_PATTERNS:
                if pattern.search(content) and db_name not in seen_db:
                    seen_db.add(db_name)
                    if db_name not in self._facts["databases"]:
                        self._facts["databases"].append(db_name)
                    db_node = _make_safe_node_id(db_name.replace(" ", ""))
                    self._add_component(db_node, "database", db_name, evidence=rel)

            # Auth usage
            if not self._facts["auth_detected"]:
                for pattern in _AUTH_JS_PATTERNS:
                    if pattern.search(content):
                        self._facts["auth_detected"] = True
                        self._add_component("AuthService", "auth", "Auth Service", evidence=rel)
                        break

            # External APIs
            for pattern, api_name in _API_JS_PATTERNS:
                if pattern.search(content) and api_name not in seen_api:
                    seen_api.add(api_name)
                    if api_name not in self._facts["external_apis"]:
                        self._facts["external_apis"].append(api_name)

        if has_frontend:
            if "Frontend" not in self._components:
                self._add_component("Frontend", "frontend", "Frontend (React)", evidence="*.tsx")

    # ── Phase 6: Build edges ──────────────────────────────────────────────────

    def _build_edges(self) -> None:
        """
        Create directed edges based on detected components.

        Edge heuristics:
        - User → Frontend (if frontend exists)
        - User → BackendAPI (if no frontend)
        - Frontend → BackendAPI
        - Frontend → AuthService
        - BackendAPI → AuthService
        - BackendAPI → Database nodes
        - BackendAPI → Cache
        - BackendAPI → Queue
        - BackendAPI → ExternalAPI (represented as a node)
        - Gateway → BackendAPI
        """
        c = self._components

        # Add User entry point
        if "Frontend" in c or "BackendAPI" in c:
            self._add_component("User", "frontend", "User", evidence="entry-point")

        if "Frontend" in c:
            self._add_edge("User", "Frontend")
            if "BackendAPI" in c:
                self._add_edge("Frontend", "BackendAPI")
        elif "BackendAPI" in c:
            self._add_edge("User", "BackendAPI")

        if "AuthService" in c:
            if "Frontend" in c:
                self._add_edge("Frontend", "AuthService")
            if "BackendAPI" in c:
                self._add_edge("BackendAPI", "AuthService")

        if "Gateway" in c and "BackendAPI" in c:
            self._add_edge("Gateway", "BackendAPI")
            if "User" in c and "Frontend" not in c:
                self._add_edge("User", "Gateway")

        # BackendAPI → all database nodes
        db_kinds = {"database"}
        for name, comp in c.items():
            if comp.kind in db_kinds and name not in ("User", "Frontend", "BackendAPI", "AuthService"):
                self._add_edge("BackendAPI", name)

        # BackendAPI → service nodes (cache, queue, etc.)
        service_nodes = {"Cache", "Queue", "Storage", "Notifications"}
        for svc in service_nodes:
            if svc in c:
                self._add_edge("BackendAPI", svc)

        # Add external API nodes from detected external_apis
        for api in self._facts.get("external_apis", []):
            if api in ("HTTP Client", "Fetch API", "Async HTTP Client"):
                continue  # generic — skip
            node_id = _make_safe_node_id(api.replace(" ", ""))
            self._add_component(node_id, "api", api, evidence="import analysis")
            self._add_edge("BackendAPI", node_id)

    # ── Phase 7: Confidence score ─────────────────────────────────────────────

    def _compute_confidence(self) -> int:
        """
        Compute a 0-100 confidence score based on how much evidence was found.

        Higher when:
        - Multiple component types detected
        - Frameworks explicitly identified
        - Databases explicitly identified
        - Many source files analyzed
        """
        score = 0
        c = self._components
        facts = self._facts

        # Base: number of unique component types detected
        kinds = {comp.kind for comp in c.values()}
        score += len(kinds) * 8   # up to ~48 for 6 kinds

        # Framework identified
        if facts["frameworks"]:
            score += 15
        # Database identified
        if facts["databases"]:
            score += 15
        # Auth detected
        if facts["auth_detected"]:
            score += 5
        # Both frontend and backend detected
        if "frontend" in kinds and "backend" in kinds:
            score += 10
        # Sufficient source files
        total_files = facts["py_files"] + facts["js_files"]
        if total_files >= 20:
            score += 10
        elif total_files >= 5:
            score += 5

        # Penalise very thin analyses
        if len(c) <= 2:
            score = max(score - 20, 10)

        return min(score, 95)  # Cap at 95 — never claim 100%

    # ── Main entry point ──────────────────────────────────────────────────────

    def analyze(self) -> AnalysisResult:
        """
        Run the full static analysis pipeline and return an AnalysisResult.

        This is synchronous and safe to call from asyncio.to_thread().
        """
        logger.info("Starting code analysis for: %s", self.root)

        # Phase 1: directory names
        self._scan_directories()

        # Phase 2: config files
        self._scan_config_files()

        # Phase 3: package.json
        self._analyze_package_json()

        # Phase 4 & 5: source files
        all_files = _walk_files(self.root)
        self._analyze_python_files(all_files)
        self._analyze_js_files(all_files)

        # Phase 6: build edges
        self._build_edges()

        confidence = self._compute_confidence()

        components = list(self._components.values())
        edges = list(self._edges)

        logger.info(
            "Analysis complete: %d components, %d edges, confidence=%d%%",
            len(components), len(edges), confidence,
        )

        return AnalysisResult(
            components=components,
            edges=edges,
            confidence=confidence,
            raw_facts=dict(self._facts),
        )
