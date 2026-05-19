# ============================================================
# Stack detector — paste a GitHub URL, get the tech stack
# ============================================================
# Heuristic only (no AI). Reads filenames + manifest files via GitHub API.
# Maps detections to skills registered in skills.py.
#
# Usage:
#   python detect_stack.py https://github.com/owner/repo
#   python detect_stack.py https://github.com/owner/repo/tree/main/subdir
#   python detect_stack.py owner/repo
# Optional env: GITHUB_TOKEN (avoids rate limits)
# ============================================================

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable

import skills  # for SKILL_REGISTRY → suggestion mapping

GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"


# ── HTTP ─────────────────────────────────────────────────────

def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ReviewMe-Stack-Detector"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ── URL parsing ──────────────────────────────────────────────

@dataclass
class RepoRef:
    owner:  str
    repo:   str
    branch: str | None = None
    subdir: str = ""


def parse_url(s: str) -> RepoRef:
    s = s.strip().rstrip("/")
    s = re.sub(r"\.git$", "", s)

    if "/" in s and "github.com" not in s and "://" not in s:
        # shorthand "owner/repo"
        owner, repo = s.split("/", 1)
        return RepoRef(owner=owner, repo=repo)

    u = urllib.parse.urlparse(s)
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"Cannot parse repo from {s!r}")

    owner, repo = parts[0], parts[1]
    branch  = None
    subdir  = ""
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        branch = parts[3]
        subdir = "/".join(parts[4:])
    return RepoRef(owner=owner, repo=repo, branch=branch, subdir=subdir)


def resolve_default_branch(ref: RepoRef) -> str:
    if ref.branch:
        return ref.branch
    meta = json.loads(_http_get(f"{GITHUB_API}/repos/{ref.owner}/{ref.repo}"))
    return meta.get("default_branch", "main")


def list_tree(ref: RepoRef, branch: str) -> list[str]:
    url  = f"{GITHUB_API}/repos/{ref.owner}/{ref.repo}/git/trees/{branch}?recursive=1"
    data = json.loads(_http_get(url))
    if data.get("truncated"):
        print("[warn] tree was truncated — large repo, detection may be partial")
    prefix = ref.subdir.rstrip("/") + "/" if ref.subdir else ""
    return [
        n["path"] for n in data.get("tree", [])
        if n["type"] == "blob" and n["path"].startswith(prefix)
    ]


def fetch_file(ref: RepoRef, branch: str, path: str) -> str:
    url = f"{GITHUB_RAW}/{ref.owner}/{ref.repo}/{branch}/{path}"
    try:
        return _http_get(url)
    except urllib.error.HTTPError:
        return ""


# ── Detection rules ──────────────────────────────────────────

@dataclass
class Signal:
    tech:        str
    why:         str
    confidence:  str = "high"  # high | medium | low


@dataclass
class Detection:
    languages:    set[str]            = field(default_factory=set)
    runtimes:     set[str]            = field(default_factory=set)
    frameworks:   set[str]            = field(default_factory=set)
    package_mgr:  set[str]            = field(default_factory=set)
    databases:    set[str]            = field(default_factory=set)
    infra:        set[str]            = field(default_factory=set)
    testing:      set[str]            = field(default_factory=set)
    signals:      list[Signal]        = field(default_factory=list)
    versions:     dict[str, str]      = field(default_factory=dict)   # tech -> raw range
    majors:       dict[str, int]      = field(default_factory=dict)   # tech -> major int


def parse_major(version_range: str) -> int | None:
    """Pull the leading major version int from an npm-style range. None if unparseable."""
    if not version_range:
        return None
    m = re.search(r"\d+", version_range)
    return int(m.group(0)) if m else None


# filename → (bucket, tech, why)
FILE_RULES: list[tuple[re.Pattern, str, str, str]] = [
    # JS/TS frameworks
    (re.compile(r"(^|/)next\.config\.(js|mjs|ts|cjs)$"), "frameworks", "Next.js", "next.config present"),
    (re.compile(r"(^|/)nuxt\.config\.(js|ts|mjs)$"),     "frameworks", "Nuxt",    "nuxt.config present"),
    (re.compile(r"(^|/)angular\.json$"),                 "frameworks", "Angular", "angular.json present"),
    (re.compile(r"(^|/)svelte\.config\.(js|ts)$"),       "frameworks", "Svelte",  "svelte.config present"),
    (re.compile(r"(^|/)vite\.config\.(js|ts|mjs)$"),     "frameworks", "Vite",    "vite.config present"),
    (re.compile(r"(^|/)remix\.config\.(js|ts|mjs)$"),    "frameworks", "Remix",   "remix.config present"),
    (re.compile(r"(^|/)astro\.config\.(mjs|js|ts)$"),    "frameworks", "Astro",   "astro.config present"),
    (re.compile(r"(^|/)gatsby-config\.(js|ts)$"),        "frameworks", "Gatsby",  "gatsby-config present"),
    (re.compile(r"(^|/)expo\.json$"), "frameworks", "Expo / React Native", "expo.json present"),
    (re.compile(r"(^|/)tailwind\.config\.(js|ts|mjs|cjs)$"), "frameworks", "Tailwind CSS", "tailwind config"),
    # JS package manager
    (re.compile(r"(^|/)package-lock\.json$"), "package_mgr", "npm",  "package-lock.json"),
    (re.compile(r"(^|/)yarn\.lock$"),         "package_mgr", "Yarn", "yarn.lock"),
    (re.compile(r"(^|/)pnpm-lock\.yaml$"),    "package_mgr", "pnpm", "pnpm-lock.yaml"),
    (re.compile(r"(^|/)bun\.lock(b)?$"),      "package_mgr", "Bun",  "bun.lock"),
    # Python
    (re.compile(r"(^|/)pyproject\.toml$"),   "languages",   "Python", "pyproject.toml"),
    (re.compile(r"(^|/)requirements.*\.txt$"), "languages", "Python", "requirements*.txt"),
    (re.compile(r"(^|/)Pipfile$"),           "package_mgr", "pipenv", "Pipfile"),
    (re.compile(r"(^|/)poetry\.lock$"),      "package_mgr", "Poetry", "poetry.lock"),
    (re.compile(r"(^|/)uv\.lock$"),          "package_mgr", "uv",     "uv.lock"),
    (re.compile(r"(^|/)manage\.py$"),        "frameworks",  "Django", "manage.py"),
    # Go / Rust / Ruby / PHP / .NET / Java
    (re.compile(r"(^|/)go\.mod$"),           "languages",   "Go",     "go.mod"),
    (re.compile(r"(^|/)Cargo\.toml$"),       "languages",   "Rust",   "Cargo.toml"),
    (re.compile(r"(^|/)Gemfile$"),           "languages",   "Ruby",   "Gemfile"),
    (re.compile(r"(^|/)config/application\.rb$"), "frameworks", "Rails", "config/application.rb"),
    (re.compile(r"(^|/)composer\.json$"),    "languages",   "PHP",    "composer.json"),
    (re.compile(r"(^|/)artisan$"),           "frameworks",  "Laravel", "artisan present"),
    (re.compile(r"(^|/).+\.csproj$"),        "languages",   "C# / .NET", "*.csproj"),
    (re.compile(r"(^|/)pom\.xml$"),          "languages",   "Java",   "pom.xml"),
    (re.compile(r"(^|/)build\.gradle(\.kts)?$"), "languages", "Java/Kotlin", "build.gradle"),
    # Infra
    (re.compile(r"(^|/)Dockerfile$"),        "infra",       "Docker",     "Dockerfile"),
    (re.compile(r"(^|/)docker-compose\.ya?ml$"), "infra",   "Docker Compose", "docker-compose"),
    (re.compile(r"(^|/)\.github/workflows/.+\.ya?ml$"), "infra", "GitHub Actions", "workflow"),
    (re.compile(r"(^|/)vercel\.json$"),      "infra",       "Vercel",     "vercel.json"),
    (re.compile(r"(^|/)netlify\.toml$"),     "infra",       "Netlify",    "netlify.toml"),
    (re.compile(r"(^|/)serverless\.ya?ml$"), "infra",       "Serverless Framework", "serverless config"),
    (re.compile(r"(^|/)terraform/.+\.tf$|(^|/).+\.tf$"), "infra", "Terraform", "*.tf"),
    (re.compile(r"(^|/)k8s/.+\.ya?ml$|kubernetes/.+\.ya?ml$"), "infra", "Kubernetes", "k8s manifests"),
    # Testing
    (re.compile(r"(^|/)jest\.config\.(js|ts|mjs)$"), "testing", "Jest",      "jest.config"),
    (re.compile(r"(^|/)vitest\.config\.(js|ts)$"),   "testing", "Vitest",    "vitest.config"),
    (re.compile(r"(^|/)playwright\.config\.(js|ts)$"), "testing", "Playwright", "playwright.config"),
    (re.compile(r"(^|/)cypress\.config\.(js|ts)$"),  "testing", "Cypress",   "cypress.config"),
    (re.compile(r"(^|/)karma\.conf\.(js|ts)$"),      "testing", "Karma",     "karma.conf"),
    # DB
    (re.compile(r"(^|/)prisma/schema\.prisma$"), "databases", "Prisma",   "prisma schema"),
    (re.compile(r"(^|/)drizzle\.config\.(ts|js)$"), "databases", "Drizzle", "drizzle.config"),
    (re.compile(r"(^|/)alembic\.ini$"),       "databases",   "Alembic",   "alembic.ini"),
]

# dep-name regex → (bucket, tech)
DEP_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^next$"),                 "frameworks", "Next.js"),
    (re.compile(r"^@angular/core$"),        "frameworks", "Angular"),
    (re.compile(r"^react$"),                "frameworks", "React"),
    (re.compile(r"^react-dom$"),            "frameworks", "React DOM"),
    (re.compile(r"^vue$"),                  "frameworks", "Vue"),
    (re.compile(r"^svelte$"),               "frameworks", "Svelte"),
    (re.compile(r"^@sveltejs/kit$"),        "frameworks", "SvelteKit"),
    (re.compile(r"^nuxt$"),                 "frameworks", "Nuxt"),
    (re.compile(r"^astro$"),                "frameworks", "Astro"),
    (re.compile(r"^@remix-run/"),           "frameworks", "Remix"),
    (re.compile(r"^expo$"),                 "frameworks", "Expo / React Native"),
    (re.compile(r"^react-native$"),         "frameworks", "React Native"),
    (re.compile(r"^express$"),              "frameworks", "Express"),
    (re.compile(r"^fastify$"),              "frameworks", "Fastify"),
    (re.compile(r"^@nestjs/core$"),         "frameworks", "NestJS"),
    (re.compile(r"^hono$"),                 "frameworks", "Hono"),
    (re.compile(r"^typescript$"),           "languages",  "TypeScript"),
    (re.compile(r"^tailwindcss$"),          "frameworks", "Tailwind CSS"),
    (re.compile(r"^prisma$|^@prisma/client$"), "databases", "Prisma"),
    (re.compile(r"^drizzle-orm$"),          "databases",  "Drizzle"),
    (re.compile(r"^mongoose$"),             "databases",  "MongoDB / Mongoose"),
    (re.compile(r"^pg$|^postgres$"),        "databases",  "PostgreSQL"),
    (re.compile(r"^mysql2?$"),              "databases",  "MySQL"),
    (re.compile(r"^redis$|^ioredis$"),      "databases",  "Redis"),
    # python deps
    (re.compile(r"^django$"),               "frameworks", "Django"),
    (re.compile(r"^fastapi$"),              "frameworks", "FastAPI"),
    (re.compile(r"^flask$"),                "frameworks", "Flask"),
    (re.compile(r"^sqlalchemy$"),           "databases",  "SQLAlchemy"),
    (re.compile(r"^pydantic$"),             "frameworks", "Pydantic"),
]

# go.mod modules
GO_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"github\.com/gin-gonic/gin"),       "frameworks", "Gin"),
    (re.compile(r"github\.com/labstack/echo"),       "frameworks", "Echo"),
    (re.compile(r"github\.com/gofiber/fiber"),       "frameworks", "Fiber"),
    (re.compile(r"github\.com/spf13/cobra"),         "frameworks", "Cobra CLI"),
]


# ── Detector ─────────────────────────────────────────────────

def detect(ref: RepoRef) -> Detection:
    branch = resolve_default_branch(ref)
    print(f"[info] resolved branch: {branch} (subdir={ref.subdir or '/'})")

    paths = list_tree(ref, branch)
    print(f"[info] {len(paths)} files in tree")

    d = Detection()

    # filename signals
    for p in paths:
        rel = p[len(ref.subdir) + 1:] if ref.subdir and p.startswith(ref.subdir + "/") else p
        for pat, bucket, tech, why in FILE_RULES:
            if pat.search(rel):
                getattr(d, bucket).add(tech)
                d.signals.append(Signal(tech, why))

    # crawl manifest files at any depth
    def _matches(pattern: str) -> list[str]:
        return [p for p in paths if p.endswith("/" + pattern) or p.split("/")[-1] == pattern]

    for pkg_path in _matches("package.json"):
        body = fetch_file(ref, branch, pkg_path)
        if not body:
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        deps = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update(data.get(key) or {})
        for name, ver in deps.items():
            for pat, bucket, tech in DEP_RULES:
                if pat.match(name):
                    getattr(d, bucket).add(tech)
                    d.signals.append(Signal(tech, f"{pkg_path} dep {name} ({ver})", "high"))
                    if isinstance(ver, str):
                        d.versions.setdefault(tech, ver)
                        mj = parse_major(ver)
                        if mj is not None:
                            # keep highest observed major
                            d.majors[tech] = max(d.majors.get(tech, 0), mj)
        if "type" in data and data["type"] == "module":
            d.runtimes.add("ESM")

    for py_path in _matches("pyproject.toml") + _matches("requirements.txt"):
        body = fetch_file(ref, branch, py_path)
        if not body:
            continue
        lowered = body.lower()
        for pat, bucket, tech in DEP_RULES:
            # crude: dep name appears as token in body
            name = pat.pattern.strip("^$")
            if re.search(rf"(^|[^a-z0-9_]){re.escape(name)}([^a-z0-9_]|$)", lowered):
                getattr(d, bucket).add(tech)
                d.signals.append(Signal(tech, f"{py_path} mentions {name}"))

    for go_path in _matches("go.mod"):
        body = fetch_file(ref, branch, go_path)
        if not body:
            continue
        for pat, bucket, tech in GO_RULES:
            if pat.search(body):
                getattr(d, bucket).add(tech)
                d.signals.append(Signal(tech, f"{go_path} imports {tech}"))

    # language fallback from extensions
    ext_map = {
        ".ts": "TypeScript", ".tsx": "TypeScript",
        ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
        ".py": "Python", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
        ".php": "PHP", ".java": "Java", ".kt": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
        ".cs": "C#", ".fs": "F#", ".vb": "Visual Basic",
        ".swift": "Swift", ".dart": "Dart",
        ".c": "C", ".h": "C",
        ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
        ".m": "Objective-C", ".mm": "Objective-C++",
        ".lua": "Lua", ".pl": "Perl", ".r": "R", ".jl": "Julia",
        ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang",
        ".hs": "Haskell", ".ml": "OCaml", ".clj": "Clojure",
        ".nim": "Nim", ".zig": "Zig", ".sh": "Shell", ".bash": "Shell",
        ".ps1": "PowerShell", ".sql": "SQL",
    }
    ext_counts: dict[str, int] = {}
    for p in paths:
        m = re.search(r"\.[a-z]+$", p)
        if not m:
            continue
        lang = ext_map.get(m.group(0))
        if lang:
            ext_counts[lang] = ext_counts.get(lang, 0) + 1
    # add top 3
    for lang, _ in sorted(ext_counts.items(), key=lambda kv: -kv[1])[:3]:
        d.languages.add(lang)

    return d


# ── Skill suggestion ─────────────────────────────────────────

# (tech_name, skill_name, min_major_or_None)
# min_major None → suggest regardless of version.
SKILL_RULES: list[tuple[str, str, int | None]] = [
    ("Next.js",   "nextjs16",   16),
    ("Angular",   "angular",    None),
    ("React",     "react19",    19),
    ("SvelteKit", "sveltekit",  None),
    ("Svelte",    "sveltekit",  None),
    ("Astro",     "astro",      None),
]


def suggest_skills(d: Detection) -> tuple[list[str], list[str]]:
    """Return (suggested_skills, version_notes)."""
    hits:  list[str] = []
    notes: list[str] = []
    for tech, skill, min_major in SKILL_RULES:
        if tech not in d.frameworks:
            continue
        if skill not in skills.SKILL_REGISTRY:
            continue
        major = d.majors.get(tech)
        if min_major is None or major is None or major >= min_major:
            hits.append(skill)
            if major is not None and min_major is not None:
                notes.append(f"{tech} v{major}: use `{skill}` skill")
        else:
            notes.append(
                f"{tech} v{major} detected — skill `{skill}` targets v{min_major}+; "
                f"model should rely on its own knowledge"
            )
    return list(dict.fromkeys(hits)), notes


# ── Output ───────────────────────────────────────────────────

def _bullet(label: str, items: Iterable[str]) -> None:
    items = sorted(set(items))
    if not items:
        return
    print(f"{label}: {', '.join(items)}")


def print_report(ref: RepoRef, d: Detection) -> None:
    print()
    print("=" * 60)
    print(f"Stack — {ref.owner}/{ref.repo}" + (f" /{ref.subdir}" if ref.subdir else ""))
    print("=" * 60)
    _bullet("Languages   ", d.languages)
    _bullet("Frameworks  ", d.frameworks)
    _bullet("Pkg manager ", d.package_mgr)
    _bullet("Databases   ", d.databases)
    _bullet("Testing     ", d.testing)
    _bullet("Infra       ", d.infra)
    _bullet("Runtimes    ", d.runtimes)

    if d.versions:
        _bullet("Versions    ", [f"{t} {v}" for t, v in sorted(d.versions.items())])

    suggested, notes = suggest_skills(d)
    print()
    if suggested:
        print(f"Matching skills available: {', '.join(suggested)}")
    else:
        print("No matching skill in registry.")
    for n in notes:
        print(f"  note: {n}")

    if d.signals:
        print()
        print("-- Evidence --")
        seen: set[tuple[str, str]] = set()
        for s in d.signals:
            key = (s.tech, s.why)
            if key in seen:
                continue
            seen.add(key)
            print(f"  - {s.tech:<22} <- {s.why}")


# ── Main ─────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python detect_stack.py <github-url-or-owner/repo>")
        return 1
    try:
        ref = parse_url(argv[1])
    except ValueError as e:
        print(f"Error: {e}")
        return 2
    print(f"[info] target: {ref.owner}/{ref.repo}")
    d = detect(ref)
    print_report(ref, d)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
