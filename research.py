"""
Layer 4a — Web research from a Job Description.

Pipeline:
  1. _derive_queries: one LLM call extracts 3 queries + 3-6 subreddits + 6-12
     domain terms from the JD. Subreddits scope the Reddit search; domain
     terms filter off-topic results post-fetch.
  2. For each query × each subreddit, hit Reddit's restricted JSON search.
  3. For each query, hit HN Algolia's full-historical JSON search.
  4. Score candidates by domain-term overlap; drop sources with zero hits;
     keep top max_sources sorted by score.

Guardrails:
  - Snippet body capped at 280 chars.
  - Polite User-Agent identifies the tool.
  - 0.4s sleep between calls.
  - One failed query/subreddit does not abort the whole step.

No new pip deps: urllib + json from stdlib.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

from openai import AsyncOpenAI

from ai_reviewer import (
    CallUsage,
    _build_async_client,
    _chat_json,
    _load_prompt,
    _resolve_provider_model,
)

USER_AGENT = "ReviewMe/0.1 (portfolio research; +github.com/local)"
SNIPPET_MAX = 280
HTTP_TIMEOUT = 20
REDDIT_MIN_SCORE = 5
REDDIT_GLOBAL_SEARCH = "https://www.reddit.com/search.json"
REDDIT_SUB_SEARCH = "https://www.reddit.com/r/{sub}/search.json"
HN_SEARCH = "https://hn.algolia.com/api/v1/search"
HN_MIN_POINTS = 5
HN_HITS_PER_PAGE = 10

MAX_SUBS = 3
RESULTS_PER_CALL = 3
SLEEP_BETWEEN_CALLS = 0.4
MIN_DOMAIN_HITS = 1


@dataclass
class Source:
    id: str = ""
    platform: str = ""        # "reddit" | "hackernews"
    url: str = ""
    title: str = ""
    snippet: str = ""         # <= SNIPPET_MAX chars, stripped
    pain_point: str = ""      # filled by LLM in Layer 4 main call
    github_stack: dict | None = None  # populated when source links a public GitHub repo


@dataclass
class ResearchResult:
    queries: list[str] = field(default_factory=list)
    subreddits: list[str] = field(default_factory=list)
    domain_terms: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    elapsed_ms: int = 0
    usage: CallUsage = field(default_factory=CallUsage)

    def to_dict(self) -> dict:
        return {
            "queries": list(self.queries),
            "subreddits": list(self.subreddits),
            "domain_terms": list(self.domain_terms),
            "sources": [asdict(s) for s in self.sources],
            "elapsed_ms": self.elapsed_ms,
            "usage": asdict(self.usage),
        }


# ── Snippet cleaning ─────────────────────────────────────────

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean_snippet(text: str, max_chars: int = SNIPPET_MAX) -> str:
    if not text:
        return ""
    t = _MD_LINK.sub(r"\1", text)
    t = _HTML_TAG.sub(" ", t)
    t = html.unescape(t)
    t = _WS.sub(" ", t).strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1].rstrip() + "…"


# ── HTTP helper ──────────────────────────────────────────────

def _http_get(url: str, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


# ── Reddit ───────────────────────────────────────────────────

def _search_reddit(
    query: str, limit: int = 5, subreddit: str | None = None
) -> list[Source]:
    params = {
        "q": query,
        "limit": str(limit),
        "sort": "relevance",
        "t": "year",
    }
    if subreddit:
        params["restrict_sr"] = "1"
        base = REDDIT_SUB_SEARCH.format(sub=urllib.parse.quote(subreddit))
    else:
        base = REDDIT_GLOBAL_SEARCH
    url = f"{base}?{urllib.parse.urlencode(params)}"
    raw = _http_get(url, accept="application/json")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    out: list[Source] = []
    for child in (data.get("data") or {}).get("children") or []:
        d = child.get("data") or {}
        score = d.get("score", 0) or 0
        if score < REDDIT_MIN_SCORE:
            continue
        permalink = d.get("permalink") or ""
        full_url = (
            f"https://www.reddit.com{permalink}" if permalink else (d.get("url") or "")
        )
        if not full_url:
            continue
        title = (d.get("title") or "").strip()
        body = d.get("selftext") or ""
        snippet_src = body if body else title
        out.append(
            Source(
                platform="reddit",
                url=full_url,
                title=title,
                snippet=_clean_snippet(snippet_src),
            )
        )
    return out


# ── Hacker News (Algolia) ────────────────────────────────────

def _search_hackernews(query: str, limit: int = 5) -> list[Source]:
    """HN Algolia full-historical search.

    Public JSON API, no token, no rate-limit auth. `tags=story` excludes
    comments. Hits include `title`, `url` (story URL, may be null for Ask HN),
    `story_text` (Ask HN body), `points`, `objectID` (HN id). For Ask HN
    posts with no external URL, falls back to `news.ycombinator.com/item?id`.
    """
    params = {
        "query": query,
        "tags": "story",
        "hitsPerPage": str(HN_HITS_PER_PAGE),
    }
    url = f"{HN_SEARCH}?{urllib.parse.urlencode(params)}"
    raw = _http_get(url, accept="application/json")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    out: list[Source] = []
    for hit in data.get("hits") or []:
        points = hit.get("points") or 0
        if points < HN_MIN_POINTS:
            continue
        story_url = (hit.get("url") or "").strip()
        if not story_url:
            obj_id = hit.get("objectID") or ""
            if not obj_id:
                continue
            story_url = f"https://news.ycombinator.com/item?id={obj_id}"
        title = (hit.get("title") or "").strip()
        body = hit.get("story_text") or ""
        snippet_src = body if body else title
        out.append(
            Source(
                platform="hackernews",
                url=story_url,
                title=title,
                snippet=_clean_snippet(snippet_src),
            )
        )
        if len(out) >= limit:
            break
    return out


# ── GitHub stack enrichment ──────────────────────────────────

_GITHUB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([\w][\w-]*)/([\w.][\w.-]*?)(?:\.git)?(?![\w.-])",
    re.IGNORECASE,
)

_GITHUB_RESERVED_OWNERS = {
    "orgs", "topics", "sponsors", "settings", "marketplace", "pricing",
    "features", "explore", "search", "trending", "collections", "events",
    "about", "issues", "pulls", "notifications", "watching", "stars",
}


def _extract_github_repo(url: str, snippet: str) -> tuple[str, str] | None:
    """Return (owner, repo) if URL or snippet mentions a github repo, else None.

    Filters out non-repo github paths (orgs/, topics/, etc.) and strips
    trailing punctuation / `.git` suffixes.
    """
    for hay in (url, snippet):
        if not hay:
            continue
        m = _GITHUB_RE.search(hay)
        if not m:
            continue
        owner = m.group(1).strip()
        repo = m.group(2).strip().rstrip(".")
        if not owner or not repo:
            continue
        if owner.lower() in _GITHUB_RESERVED_OWNERS:
            continue
        return owner, repo
    return None


def _fetch_github_stack(owner: str, repo: str) -> dict | None:
    """Detect tech stack of a public github repo via the existing detect_stack
    module. Returns a lean summary (only non-empty buckets). Swallows errors
    (rate-limit, private, missing) — returns None.
    """
    try:
        import detect_stack  # lazy: avoid pulling skills registry if unused
        ref = detect_stack.RepoRef(owner=owner, repo=repo)
        d = detect_stack.detect(ref)
    except Exception as e:
        sys.stderr.write(
            f"[research] github stack {owner}/{repo} failed: {e}\n"
        )
        return None
    summary: dict[str, list[str]] = {
        "languages": sorted(d.languages),
        "frameworks": sorted(d.frameworks),
        "databases": sorted(d.databases),
        "infra": sorted(d.infra),
    }
    summary = {k: v for k, v in summary.items() if v}
    return summary or None


# ── Relevance filter ─────────────────────────────────────────

def _relevance_score(source: Source, domain_terms: list[str]) -> int:
    if not domain_terms:
        return 1
    hay = (source.title + " " + source.snippet).lower()
    return sum(1 for t in domain_terms if t and t in hay)


# ── Parsing helpers ──────────────────────────────────────────

def _take_strings(
    raw: object, max_n: int, lower: bool, strip_chars: str = ""
) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        if not isinstance(x, str):
            continue
        s = x.strip()
        if strip_chars:
            s = s.strip(strip_chars).strip()
        if lower:
            s = s.lower()
        if s:
            out.append(s)
    return out[:max_n]


# ── Query derivation (small LLM call) ────────────────────────

async def _derive_queries(
    client: AsyncOpenAI, jd: str, model: str | None = None,
) -> tuple[list[str], list[str], list[str], CallUsage]:
    """Returns (queries, subreddits, domain_terms, usage).

    Subreddits are stripped of any leading 'r/' or '/r/' prefix. Domain
    terms are lowercased. Queries are lowercased and capped at 3.
    """
    system = _load_prompt("layer4_query_derivation.md")
    user = (
        "Extract search queries, subreddits, and domain terms from the JD below. "
        "Return JSON only.\n\n"
        "### Job Description\n"
        f"{jd.strip()}"
    )
    payload, usage = await _chat_json(
        client, system, user, "Layer 4a queries", model=model,
    )

    queries = _take_strings(
        payload.get("queries") if isinstance(payload, dict) else None,
        max_n=3,
        lower=True,
    )
    subreddits = _take_strings(
        payload.get("subreddits") if isinstance(payload, dict) else None,
        max_n=6,
        lower=False,
        strip_chars="/r ",
    )
    domain_terms = _take_strings(
        payload.get("domain_terms") if isinstance(payload, dict) else None,
        max_n=12,
        lower=True,
    )
    return queries, subreddits, domain_terms, usage


# ── Public entry ─────────────────────────────────────────────

async def research_jd(
    jd: str,
    max_sources: int = 8,
    provider: str | None = None,
    model: str | None = None,
) -> ResearchResult:
    jd = (jd or "").strip()
    if not jd:
        raise ValueError("JD is required for Layer 4a research.")

    eff_provider, eff_model = _resolve_provider_model(provider, model)
    client = _build_async_client(eff_provider)
    start = time.time()

    queries, subreddits, domain_terms, usage = await _derive_queries(
        client, jd, model=eff_model,
    )
    target_subs: list[str | None] = (
        subreddits[:MAX_SUBS] if subreddits else [None]
    )

    candidates: list[Source] = []
    seen_urls: set[str] = set()

    for q in queries:
        for sub in target_subs:
            try:
                hits = await asyncio.to_thread(
                    _search_reddit, q, RESULTS_PER_CALL, sub
                )
            except Exception as e:
                sys.stderr.write(
                    f"[research] reddit r/{sub or '*'} {q!r} failed: {e}\n"
                )
                hits = []
            for s in hits:
                if not s.url or s.url in seen_urls:
                    continue
                seen_urls.add(s.url)
                candidates.append(s)
            await asyncio.sleep(SLEEP_BETWEEN_CALLS)

        try:
            hn_hits = await asyncio.to_thread(_search_hackernews, q, 5)
        except Exception as e:
            sys.stderr.write(f"[research] hn {q!r} failed: {e}\n")
            hn_hits = []
        for s in hn_hits:
            if not s.url or s.url in seen_urls:
                continue
            seen_urls.add(s.url)
            candidates.append(s)
        await asyncio.sleep(SLEEP_BETWEEN_CALLS)

    # Relevance filter + rank by domain-term hit count
    scored = [(s, _relevance_score(s, domain_terms)) for s in candidates]
    kept = [(s, sc) for s, sc in scored if sc >= MIN_DOMAIN_HITS]
    kept.sort(key=lambda x: -x[1])

    sources: list[Source] = []
    for i, (s, _sc) in enumerate(kept[:max_sources]):
        s.id = f"s{i + 1}"
        repo = _extract_github_repo(s.url, s.snippet)
        if repo:
            owner, name = repo
            s.github_stack = await asyncio.to_thread(
                _fetch_github_stack, owner, name
            )
        sources.append(s)

    elapsed_ms = int((time.time() - start) * 1000)
    return ResearchResult(
        queries=queries,
        subreddits=subreddits,
        domain_terms=domain_terms,
        sources=sources,
        elapsed_ms=elapsed_ms,
        usage=usage,
    )
