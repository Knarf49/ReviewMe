"""
Tests for Layer 4a research module.

Invariants:
  1. Reddit JSON parser: score filter, snippet length cap, url construction.
  2. Product Hunt RSS filter: keyword match, dedup, ≤ limit.
  3. Query derivation: returns exactly 3 lowercase queries.
  4. research_jd: per-query errors don't abort, sources get sequential ids,
     duplicate URLs are deduped.
"""

from __future__ import annotations

import asyncio
import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_reviewer  # noqa: E402
import research  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────

class _FakeHTTPResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _mk_completion(payload: dict, prompt_tokens: int = 5, completion_tokens: int = 10):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        ),
    )


def _stub_openai_client(payload: dict):
    async def _create(**kwargs):
        return _mk_completion(payload)

    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)
    return client


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


# ── _clean_snippet ───────────────────────────────────────────

def test_clean_snippet_caps_length_and_strips_html_markdown():
    long = "<b>Hello</b> [click](http://x) " + ("word " * 200)
    out = research._clean_snippet(long, max_chars=80)
    assert len(out) <= 80
    assert "<b>" not in out
    assert "click" in out                  # link text preserved
    assert "http://x" not in out           # link target dropped
    assert out.endswith("…")


def test_clean_snippet_handles_empty():
    assert research._clean_snippet("") == ""
    assert research._clean_snippet(None) == ""  # type: ignore[arg-type]


# ── _search_reddit ───────────────────────────────────────────

REDDIT_FIXTURE = {
    "data": {
        "children": [
            {"data": {
                "title": "Pain with internal tools",
                "selftext": "We keep building bespoke admin dashboards. " * 20,
                "permalink": "/r/devops/comments/a1/pain/",
                "score": 42,
            }},
            {"data": {
                "title": "Low-score noise",
                "selftext": "ignore me",
                "permalink": "/r/x/y",
                "score": 1,
            }},
            {"data": {
                "title": "Title-only post",
                "selftext": "",
                "permalink": "/r/foo/bar/",
                "score": 10,
            }},
        ]
    }
}


def test_search_reddit_parses_json_and_filters_score():
    body = json.dumps(REDDIT_FIXTURE).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        return _FakeHTTPResp(body)

    with patch.object(research.urllib.request, "urlopen", _fake_urlopen):
        sources = research._search_reddit("admin tool pain", limit=5)

    assert len(sources) == 2                       # low-score one dropped
    assert sources[0].platform == "reddit"
    assert sources[0].url.startswith("https://www.reddit.com/r/devops")
    assert len(sources[0].snippet) <= research.SNIPPET_MAX
    # title-only post uses title as snippet
    assert sources[1].snippet == "Title-only post"


def test_search_reddit_handles_missing_data_gracefully():
    body = b'{"foo": "bar"}'

    def _fake_urlopen(req, timeout=None):
        return _FakeHTTPResp(body)

    with patch.object(research.urllib.request, "urlopen", _fake_urlopen):
        sources = research._search_reddit("q")
    assert sources == []


HN_FIXTURE = {
    "hits": [
        {
            "title": "Show HN: CRM for ops teams",
            "url": "https://example.com/crm",
            "story_text": "We built a CRM that handles internal ops tooling.",
            "points": 120,
            "objectID": "111",
        },
        {
            "title": "Low-point noise",
            "url": "https://example.com/lp",
            "story_text": "ignore me",
            "points": 1,
            "objectID": "222",
        },
        {
            "title": "Ask HN: how to scale CRM",
            "url": None,                       # Ask HN has no external URL
            "story_text": "We struggle with scaling our CRM data.",
            "points": 80,
            "objectID": "333",
        },
    ]
}


def test_search_hackernews_parses_and_filters_points():
    body = json.dumps(HN_FIXTURE).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        return _FakeHTTPResp(body)

    with patch.object(research.urllib.request, "urlopen", _fake_urlopen):
        sources = research._search_hackernews("crm", limit=5)

    assert len(sources) == 2                    # low-point dropped
    assert sources[0].platform == "hackernews"
    assert sources[0].url == "https://example.com/crm"
    assert "internal ops" in sources[0].snippet


def test_search_hackernews_falls_back_to_item_url_for_ask_hn():
    """Ask HN posts have `url=null`; we should link to HN's item page."""
    body = json.dumps(HN_FIXTURE).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        return _FakeHTTPResp(body)

    with patch.object(research.urllib.request, "urlopen", _fake_urlopen):
        sources = research._search_hackernews("crm", limit=5)

    ask_hn = next(s for s in sources if "Ask HN" in s.title)
    assert ask_hn.url == "https://news.ycombinator.com/item?id=333"


# ── GitHub stack enrichment ──────────────────────────────────

def test_extract_github_repo_from_url():
    assert research._extract_github_repo("https://github.com/foo/bar", "") == ("foo", "bar")
    assert research._extract_github_repo("http://www.GitHub.com/foo/bar/tree/main", "") == ("foo", "bar")
    assert research._extract_github_repo("https://github.com/foo/bar.git", "") == ("foo", "bar")


def test_extract_github_repo_keeps_hyphens_in_repo_name():
    """Regression: `\\b` boundary used to truncate hyphenated repo names at first dash."""
    assert research._extract_github_repo(
        "https://github.com/aws-samples/sample-agentic-frameworks-on-aws", ""
    ) == ("aws-samples", "sample-agentic-frameworks-on-aws")
    assert research._extract_github_repo(
        "https://github.com/psychic-api/rag-stack", ""
    ) == ("psychic-api", "rag-stack")
    assert research._extract_github_repo(
        "https://github.com/openziti/llm-gateway", ""
    ) == ("openziti", "llm-gateway")


def test_extract_github_repo_from_snippet():
    assert research._extract_github_repo(
        "https://example.com/blog",
        "We open-sourced it at https://github.com/owner/repo as MIT.",
    ) == ("owner", "repo")


def test_extract_github_repo_returns_none_for_non_github():
    assert research._extract_github_repo("https://reddit.com/r/x", "no github here") is None


def test_extract_github_repo_skips_reserved_paths():
    assert research._extract_github_repo("https://github.com/orgs/anthropic", "") is None
    assert research._extract_github_repo("https://github.com/topics/python", "") is None


def test_fetch_github_stack_swallows_errors(monkeypatch):
    import detect_stack
    monkeypatch.setattr(detect_stack, "detect", lambda ref: (_ for _ in ()).throw(RuntimeError("rate limited")))
    assert research._fetch_github_stack("foo", "bar") is None


def test_fetch_github_stack_returns_lean_summary(monkeypatch):
    import detect_stack

    class _FakeDetection:
        languages = {"Python"}
        frameworks = {"FastAPI"}
        databases = {"PostgreSQL"}
        infra: set = set()

    monkeypatch.setattr(detect_stack, "detect", lambda ref: _FakeDetection())
    result = research._fetch_github_stack("foo", "bar")
    assert result == {
        "languages": ["Python"],
        "frameworks": ["FastAPI"],
        "databases": ["PostgreSQL"],
    }
    assert "infra" not in result               # empty bucket dropped


def test_research_jd_enriches_github_sources(monkeypatch):
    """A source whose url points to a github repo gets a populated github_stack."""
    monkeypatch.setattr(research, "_search_hackernews", lambda q, limit=5: [])
    monkeypatch.setattr(research.asyncio, "sleep", AsyncMock())

    def _reddit_with_github(query, limit=5, subreddit=None):
        return [
            research.Source(
                platform="reddit",
                url="https://github.com/acme/widget",
                title="Acme widget — release thread",
                snippet="domain match here",
            ),
            research.Source(
                platform="reddit",
                url="https://www.reddit.com/r/CRM/no-repo",
                title="Pure reddit thread, no repo",
                snippet="domain talk only",
            ),
        ]

    monkeypatch.setattr(research, "_search_reddit", _reddit_with_github)
    monkeypatch.setattr(
        research, "_derive_queries",
        _fake_derive(["q1"], subs=["CRM"], terms=["domain"]),
    )

    # Stub the stack detection so the test doesn't hit GitHub.
    def _fake_fetch_stack(owner, repo):
        return {"frameworks": ["FastAPI"], "languages": ["Python"]}

    monkeypatch.setattr(research, "_fetch_github_stack", _fake_fetch_stack)

    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=MagicMock()):
        result = asyncio.run(research.research_jd("JD"))

    by_url = {s.url: s for s in result.sources}
    assert by_url["https://github.com/acme/widget"].github_stack == {
        "frameworks": ["FastAPI"],
        "languages": ["Python"],
    }
    assert by_url["https://www.reddit.com/r/CRM/no-repo"].github_stack is None


def test_search_hackernews_handles_empty_response():
    def _fake_urlopen(req, timeout=None):
        return _FakeHTTPResp(b'{"hits": []}')

    with patch.object(research.urllib.request, "urlopen", _fake_urlopen):
        sources = research._search_hackernews("anything")
    assert sources == []


# ── _derive_queries ──────────────────────────────────────────

def test_derive_queries_returns_three_lowercase():
    client = _stub_openai_client({
        "queries": ["Internal Admin TOOL pain", "  ops dashboard alternatives  ", "react Pain"],
        "subreddits": ["devops", "/r/sre", "Python"],
        "domain_terms": ["FastAPI", "Postgres", "REST API"],
    })
    queries, subs, terms, usage = asyncio.run(
        research._derive_queries(client, "FastAPI engineer JD")
    )
    assert queries == [
        "internal admin tool pain",
        "ops dashboard alternatives",
        "react pain",
    ]
    assert subs == ["devops", "sre", "Python"]      # leading "/r/" stripped
    assert terms == ["fastapi", "postgres", "rest api"]
    assert usage.prompt_tokens == 5
    assert usage.completion_tokens == 10


def test_derive_queries_caps_at_three_queries():
    client = _stub_openai_client({
        "queries": ["one", "two", "three", "four", "five"],
        "subreddits": [],
        "domain_terms": [],
    })
    queries, subs, terms, _ = asyncio.run(research._derive_queries(client, "JD"))
    assert queries == ["one", "two", "three"]
    assert subs == []
    assert terms == []


def test_derive_queries_handles_malformed_payload():
    client = _stub_openai_client({"not_queries": "oops"})
    queries, subs, terms, _ = asyncio.run(research._derive_queries(client, "JD"))
    assert queries == []
    assert subs == []
    assert terms == []


# ── research_jd integration ──────────────────────────────────

def _fake_derive(queries, subs=None, terms=None, prompt_tokens=5, completion_tokens=10):
    """Patch helper: bypass the LLM call in _derive_queries."""
    async def _impl(client, jd, model=None):
        return (
            list(queries),
            list(subs or []),
            list(terms or ["domain"]),  # default keeps relevance filter open
            research.CallUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        )
    return _impl


def test_research_jd_swallows_per_query_errors(monkeypatch):
    """One reddit call raises, others return — final result still aggregates."""

    monkeypatch.setattr(research, "_search_hackernews", lambda q, limit=5: [])

    calls = {"n": 0}

    def _flaky_reddit(query, limit=5, subreddit=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("network kaboom")
        return [
            research.Source(
                platform="reddit",
                url=f"https://www.reddit.com/r/test/{calls['n']}",
                title=f"hit {calls['n']} with domain",
                snippet="snippet domain",
            )
        ]

    monkeypatch.setattr(research, "_search_reddit", _flaky_reddit)
    monkeypatch.setattr(research.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        research, "_derive_queries",
        _fake_derive(["q1", "q2", "q3"], subs=["foo"], terms=["domain"]),
    )

    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=MagicMock()):
        result = asyncio.run(research.research_jd("real JD with content"))

    assert result.queries == ["q1", "q2", "q3"]
    assert result.subreddits == ["foo"]
    assert result.domain_terms == ["domain"]
    # Two queries succeeded (q1, q3); q2 raised and was swallowed.
    assert len(result.sources) == 2
    assert [s.id for s in result.sources] == ["s1", "s2"]
    assert result.usage.prompt_tokens == 5


def test_research_jd_dedupes_by_url(monkeypatch):
    monkeypatch.setattr(research, "_search_hackernews", lambda q, limit=5: [])

    def _same_url_each_time(query, limit=5, subreddit=None):
        return [
            research.Source(
                platform="reddit",
                url="https://www.reddit.com/r/same/post/",
                title="repeated domain",
                snippet="x domain",
            )
        ]

    monkeypatch.setattr(research, "_search_reddit", _same_url_each_time)
    monkeypatch.setattr(research.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        research, "_derive_queries",
        _fake_derive(["q1", "q2", "q3"], subs=["foo"], terms=["domain"]),
    )

    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=MagicMock()):
        result = asyncio.run(research.research_jd("JD"))

    assert len(result.sources) == 1
    assert result.sources[0].id == "s1"


def test_research_jd_filters_off_topic_via_domain_terms(monkeypatch):
    """Domain-term filter drops sources with no overlap; keeps the on-topic one."""
    monkeypatch.setattr(research, "_search_hackernews", lambda q, limit=5: [])

    on_topic = research.Source(
        platform="reddit",
        url="https://www.reddit.com/r/CRM/1",
        title="Customizing CRM workflows is painful",
        snippet="Our team struggles with CRM template editing.",
    )
    off_topic = research.Source(
        platform="reddit",
        url="https://www.reddit.com/r/games/1",
        title="This Week In Destiny",
        snippet="Bungie weekly update for the Destiny game.",
    )

    def _mixed(query, limit=5, subreddit=None):
        return [on_topic, off_topic]

    monkeypatch.setattr(research, "_search_reddit", _mixed)
    monkeypatch.setattr(research.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        research, "_derive_queries",
        _fake_derive(["q1"], subs=["CRM"], terms=["crm", "workflow"]),
    )

    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=MagicMock()):
        result = asyncio.run(research.research_jd("JD"))

    urls = [s.url for s in result.sources]
    assert "https://www.reddit.com/r/CRM/1" in urls
    assert "https://www.reddit.com/r/games/1" not in urls


def test_research_jd_falls_back_to_global_search_without_subreddits(monkeypatch):
    """If LLM returns no subreddits, we still issue one global Reddit search per query."""
    monkeypatch.setattr(research, "_search_hackernews", lambda q, limit=5: [])
    calls: list[str | None] = []

    def _capture(query, limit=5, subreddit=None):
        calls.append(subreddit)
        return [
            research.Source(
                platform="reddit",
                url=f"https://www.reddit.com/x/{len(calls)}",
                title="hit domain",
                snippet="snippet",
            )
        ]

    monkeypatch.setattr(research, "_search_reddit", _capture)
    monkeypatch.setattr(research.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        research, "_derive_queries",
        _fake_derive(["q1"], subs=[], terms=["domain"]),
    )

    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=MagicMock()):
        result = asyncio.run(research.research_jd("JD"))

    assert calls == [None]                  # global search, no restrict_sr
    assert len(result.sources) == 1


def test_research_jd_empty_jd_raises():
    with pytest.raises(ValueError, match="JD is required"):
        asyncio.run(research.research_jd(""))


def test_research_jd_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        asyncio.run(research.research_jd("real JD"))
