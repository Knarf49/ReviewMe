# ============================================================
# Skill Fetcher — exposes GitHub-hosted skill packs as OpenAI tools
# ============================================================
# Registered skills are pre-pulled into ./skills_cache/ at startup.
# Models call list_skills / fetch_skill via tool calling.
# To add a skill: append an entry to SKILL_REGISTRY below.
# ============================================================

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"

# Registry entries:
#   GitHub-hosted: {repo, branch, path}
#   Local file:    {local: True, files: {dest_name: source_path_or_inline_content_marker}}
#                  -or-  {local: True, files: [(dest_name, abs_path), ...]}
SKILL_REGISTRY: dict[str, dict] = {
    "nextjs16": {
        "repo":   "gocallum/nextjs16-agent-skills",
        "branch": "main",
        "path":   "skills/nextjs16-skills",
    },
    "angular": {
        "repo":   "angular/skills",
        "branch": "main",
        "path":   "angular-developer",
    },
    "react19": {
        "local": True,
        "files": [("SKILL.md", str(Path(__file__).parent / "react19-skill.md"))],
    },
    "sveltekit": {
        "repo":   "sveltejs/ai-tools",
        "branch": "main",
        "path":   "documentation/docs/40-skills/.generated",
    },
    "astro": {
        "repo":   "mindrally/skills",
        "branch": "main",
        "path":   "astro",
    },
}

CACHE_DIR = Path(__file__).parent / "skills_cache"
MAX_CONTENT_CHARS = 16000   # cap per fetch_skill call to protect context budget

_CATALOG: dict[str, dict] = {}


# ── HTTP helpers ─────────────────────────────────────────────

def _http_get(url: str, raw: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": "ReviewMe-Skill-Fetcher"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        return data if raw else data.decode("utf-8")


def _list_repo_blobs(repo: str, branch: str, prefix: str) -> list[str]:
    url = f"{GITHUB_API}/repos/{repo}/git/trees/{branch}?recursive=1"
    tree = json.loads(_http_get(url))["tree"]
    prefix = prefix.rstrip("/")
    return [
        n["path"] for n in tree
        if n["type"] == "blob" and (n["path"].startswith(prefix + "/") or n["path"] == prefix)
    ]


# ── Prepull ──────────────────────────────────────────────────

def prepull_skills(force: bool = False) -> dict[str, dict]:
    CACHE_DIR.mkdir(exist_ok=True)
    catalog: dict[str, dict] = {}

    for name, meta in SKILL_REGISTRY.items():
        skill_dir = CACHE_DIR / name
        skill_dir.mkdir(exist_ok=True)

        # Local-source skill: copy declared files into cache dir.
        if meta.get("local"):
            rel_files: list[str] = []
            for dest, src in meta.get("files", []):
                src_path = Path(src)
                if not src_path.is_file():
                    print(f"[skills] WARN {name}: missing local source {src}")
                    continue
                target = skill_dir / dest
                target.parent.mkdir(parents=True, exist_ok=True)
                if force or not target.exists() or target.read_bytes() != src_path.read_bytes():
                    target.write_bytes(src_path.read_bytes())
                rel_files.append(dest)
            catalog[name] = {
                "description": _extract_description(skill_dir),
                "files":       rel_files,
            }
            print(f"[skills] {name}: {len(rel_files)} local file(s) cached")
            continue

        prefix = meta["path"].rstrip("/")

        try:
            blob_paths = _list_repo_blobs(meta["repo"], meta["branch"], prefix)
        except urllib.error.HTTPError as e:
            print(f"[skills] WARN {name}: tree fetch failed ({e}) — using cached files only")
            blob_paths = []

        rel_files: list[str] = []
        for path in blob_paths:
            rel = path[len(prefix) + 1:] if path != prefix else Path(path).name
            local = skill_dir / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            if force or not local.exists():
                raw_url = f"{GITHUB_RAW}/{meta['repo']}/{meta['branch']}/{path}"
                try:
                    local.write_bytes(_http_get(raw_url, raw=True))
                except urllib.error.HTTPError as e:
                    print(f"[skills] WARN {name}/{rel}: download failed ({e})")
                    continue
            rel_files.append(rel)

        # if network failed, fall back to whatever sits in the cache dir
        if not rel_files and skill_dir.exists():
            rel_files = sorted(
                str(p.relative_to(skill_dir)).replace("\\", "/")
                for p in skill_dir.rglob("*") if p.is_file()
            )

        catalog[name] = {
            "description": _extract_description(skill_dir),
            "files":       rel_files,
        }
        print(f"[skills] {name}: {len(rel_files)} file(s) cached")

    return catalog


def _extract_description(skill_dir: Path) -> str:
    for fname in ("SKILL.md", "SKILL.MD", "Skill.md", "README.md"):
        f = skill_dir / fname
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if m:
            dm = re.search(r"^description:\s*(.+)$", m.group(1), re.MULTILINE)
            if dm:
                return dm.group(1).strip()
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("---"):
                return s[:240]
    return ""


def init(force: bool = False) -> dict[str, dict]:
    global _CATALOG
    _CATALOG = prepull_skills(force=force)
    _load_external_index(force=force)
    return _CATALOG


# ── External catalog (mindrally/skills) ──────────────────────

EXTERNAL_REPO   = "mindrally/skills"
EXTERNAL_BRANCH = "main"
_EXTERNAL_INDEX: list[str] = []


def _load_external_index(force: bool = False) -> list[str]:
    """Cache top-level dir names of mindrally/skills (each = one skill)."""
    global _EXTERNAL_INDEX
    cache = CACHE_DIR / "_index_mindrally.json"
    if not force and cache.is_file():
        try:
            _EXTERNAL_INDEX = json.loads(cache.read_text(encoding="utf-8"))
            print(f"[skills] external index: {len(_EXTERNAL_INDEX)} entries (cached)")
            return _EXTERNAL_INDEX
        except Exception:
            pass
    try:
        url  = f"{GITHUB_API}/repos/{EXTERNAL_REPO}/git/trees/{EXTERNAL_BRANCH}"
        data = json.loads(_http_get(url))
        _EXTERNAL_INDEX = sorted(
            n["path"] for n in data.get("tree", [])
            if n["type"] == "tree" and not n["path"].startswith(".")
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(_EXTERNAL_INDEX), encoding="utf-8")
        print(f"[skills] external index: {len(_EXTERNAL_INDEX)} entries (fetched)")
    except Exception as e:
        print(f"[skills] WARN external index fetch failed: {e}")
        _EXTERNAL_INDEX = []
    return _EXTERNAL_INDEX


def search_skills_catalog(query: str, limit: int = 20) -> list[str]:
    if not _EXTERNAL_INDEX:
        _load_external_index()
    q = (query or "").lower().strip()
    if not q:
        return _EXTERNAL_INDEX[:limit]
    starts = [s for s in _EXTERNAL_INDEX if s.lower().startswith(q)]
    contains = [s for s in _EXTERNAL_INDEX if q in s.lower() and s not in starts]
    return (starts + contains)[:limit]


def fetch_external_skill(name: str) -> str:
    """Pull SKILL.md from mindrally/skills/<name>/ on demand. Caches to disk."""
    if not _EXTERNAL_INDEX:
        _load_external_index()
    if _EXTERNAL_INDEX and name not in _EXTERNAL_INDEX:
        hits = search_skills_catalog(name, limit=10)
        return (
            f"ERROR: '{name}' not in external catalog. "
            f"Closest matches: {hits}. Use search_skills_catalog first."
        )
    target_dir = CACHE_DIR / "_external" / name
    target_dir.mkdir(parents=True, exist_ok=True)
    skill_md = target_dir / "SKILL.md"
    if not skill_md.is_file():
        url = f"{GITHUB_RAW}/{EXTERNAL_REPO}/{EXTERNAL_BRANCH}/{name}/SKILL.md"
        try:
            skill_md.write_bytes(_http_get(url, raw=True))
        except urllib.error.HTTPError as e:
            return f"ERROR: fetch failed for '{name}' ({e})"
    return skill_md.read_text(encoding="utf-8", errors="ignore")[:MAX_CONTENT_CHARS]


# ── Tool handlers ────────────────────────────────────────────

def list_skills() -> list[dict]:
    return [
        {"name": n, "description": d["description"], "files": d["files"]}
        for n, d in _CATALOG.items()
    ]


def fetch_skill(name: str, file: Optional[str] = None) -> str:
    if name not in _CATALOG:
        return f"ERROR: unknown skill '{name}'. Available: {list(_CATALOG.keys())}"

    skill_dir = CACHE_DIR / name

    if file:
        target = skill_dir / file
        if not target.is_file():
            return (
                f"ERROR: file '{file}' not in skill '{name}'. "
                f"Files: {_CATALOG[name]['files']}"
            )
        return target.read_text(encoding="utf-8", errors="ignore")[:MAX_CONTENT_CHARS]

    parts: list[str] = []
    used = 0
    for rel in _CATALOG[name]["files"]:
        f = skill_dir / rel
        if not f.is_file():
            continue
        body = f.read_text(encoding="utf-8", errors="ignore")
        block = f"=== {rel} ===\n{body}"
        if used + len(block) > MAX_CONTENT_CHARS:
            block = block[: MAX_CONTENT_CHARS - used] + "\n…[truncated]"
            parts.append(block)
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


# ── OpenAI tool schemas ──────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": (
                "List available reference skill packs (knowledge bundles). "
                "Returns name, description and file list for each. "
                "Call this first if unsure which skill is relevant."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_skill",
            "description": (
                "Fetch the content of a skill pack. "
                "By default returns every file concatenated (capped). "
                "For skills with many files (see list_skills `files`), prefer passing "
                "`file` to read one specific reference. Start with SKILL.md for overview."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type":        "string",
                        "description": "Skill name (key from list_skills).",
                    },
                    "file": {
                        "type":        "string",
                        "description": "Optional specific filename within the skill.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_skills_catalog",
            "description": (
                "Search the EXTERNAL mindrally/skills catalog (200+ community skills) "
                "for skill names matching a query. Use this when list_skills shows no "
                "locally-registered skill for the topic. Returns a list of skill names "
                "you can then pass to fetch_external_skill."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type":        "string",
                        "description": "Substring/topic, e.g. 'fastapi', 'prisma', 'rust', 'tailwind'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_external_skill",
            "description": (
                "Fetch SKILL.md content for one skill from the external "
                "mindrally/skills catalog. Call search_skills_catalog first to find the "
                "exact name. Content is cached locally after first fetch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type":        "string",
                        "description": "Exact skill directory name from search_skills_catalog.",
                    },
                },
                "required": ["name"],
            },
        },
    },
]


def dispatch(tool_name: str, arguments: dict) -> str:
    if tool_name == "list_skills":
        return json.dumps(list_skills())
    if tool_name == "fetch_skill":
        return fetch_skill(arguments.get("name"), arguments.get("file"))
    if tool_name == "search_skills_catalog":
        return json.dumps(search_skills_catalog(arguments.get("query", "")))
    if tool_name == "fetch_external_skill":
        return fetch_external_skill(arguments.get("name", ""))
    return f"ERROR: unknown tool '{tool_name}'"


if __name__ == "__main__":
    init(force=False)
    print(json.dumps(list_skills(), indent=2))
