"""
Tests for Layer 4 (project_suggester).

Invariants:
  1. Empty / whitespace JD raises ValueError BEFORE any LLM call.
  2. Missing OPENAI_API_KEY raises RuntimeError.
  3. Suggestion JSON from the model is returned in `result.suggestion`,
     with research_sources merged back from the deterministic research step.
  4. log_run writes input.json + output.json + research.json + index.jsonl.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_reviewer  # noqa: E402
import project_suggester  # noqa: E402
import research  # noqa: E402


SAMPLE_SUGGESTION = {
    "project_title": "Rate-Limited Public API",
    "problem_statement": "Hobby devs need a free, rate-limited weather proxy.",
    "must_have_features": [
        "versioned REST endpoints",
        "per-key rate limiting",
        "OpenAPI spec generated from code",
    ],
    "stretch_goals": ["WebSocket push", "Grafana dashboard"],
    "recommended_stack": [
        "Python 3.12 — because JD asks for Python",
        "FastAPI — because JD asks for typed REST APIs",
        "PostgreSQL 16 — because JD asks for SQL",
    ],
    "skills_demonstrated": [
        {"skill": "REST API design", "from_jd": "design and ship REST APIs"},
        {"skill": "observability", "from_jd": "instrumenting production services"},
    ],
    "success_rubric": [
        {"criterion": "rate limit enforcement",
         "measure": "429 returned after N requests within 60s in integration test"},
        {"criterion": "API docs", "measure": "openapi.json served at /openapi.json"},
    ],
    "research_sources": [
        {
            "id": "s1",
            "platform": "reddit",
            "url": "https://www.reddit.com/r/foo/1",
            "title": "Free weather APIs are rate-limited weirdly",
            "snippet": "Every free weather API has weird quotas and no per-key keys.",
            "pain_point": "Free weather APIs hide quotas and lack per-key controls.",
        }
    ],
    "inspired_by": [
        {"feature": "per-key rate limiting", "source_ids": ["s1"]},
    ],
}


def _mk_completion(payload: dict, prompt_tokens: int = 50, completion_tokens: int = 200):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def _multi_call_stub_client(responses: list[dict]):
    """Build a fake AsyncOpenAI whose chat.completions.create returns each
    payload in `responses` in order — used to mock both the query-derivation
    and main Layer 4 calls."""
    seq = iter(responses)

    async def _create(**kwargs):
        try:
            payload = next(seq)
        except StopIteration:
            payload = {"error": "no more stub responses"}
        return _mk_completion(payload)

    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)
    return client


def _fake_research_result(sources: list[research.Source] | None = None) -> research.ResearchResult:
    return research.ResearchResult(
        queries=["q1", "q2", "q3"],
        sources=sources or [
            research.Source(
                id="s1",
                platform="reddit",
                url="https://www.reddit.com/r/foo/1",
                title="Free weather APIs are rate-limited weirdly",
                snippet="Every free weather API has weird quotas and no per-key keys.",
            )
        ],
        elapsed_ms=42,
        usage=project_suggester.CallUsage(prompt_tokens=20, completion_tokens=30),
    )


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_empty_jd_raises():
    with pytest.raises(ValueError, match="JD is required"):
        project_suggester.run_layer4_sync("")
    with pytest.raises(ValueError, match="JD is required"):
        project_suggester.run_layer4_sync("   \n  \t ")


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        project_suggester.run_layer4_sync("Senior backend role with REST APIs.")


def test_suggestion_returned_with_research_merged():
    """Layer 4 should swallow LLM source content and force-write the real sources back."""
    async def _fake_research(jd, **_):
        return _fake_research_result()

    fake_client = _multi_call_stub_client([SAMPLE_SUGGESTION])
    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=fake_client), \
         patch.object(project_suggester, "research_jd", _fake_research):
        result = project_suggester.run_layer4_sync(
            "We need a backend engineer who can design and ship REST APIs "
            "and is comfortable instrumenting production services."
        )

    assert result.suggestion["project_title"] == "Rate-Limited Public API"
    assert result.suggestion["research_sources"][0]["id"] == "s1"
    assert result.suggestion["research_sources"][0]["url"] == "https://www.reddit.com/r/foo/1"
    # pain_point kept from LLM output
    assert "per-key" in result.suggestion["research_sources"][0]["pain_point"]
    assert result.suggestion["inspired_by"] == [
        {"feature": "per-key rate limiting", "source_ids": ["s1"]},
    ]
    assert result.model == project_suggester.MODEL
    assert result.elapsed_ms >= 0
    # Aggregate usage = research (20+30) + main (50+200)
    assert result.usage.prompt_tokens == 70
    assert result.usage.completion_tokens == 230
    # Only the main Layer 4 call goes through this client (research stubbed)
    assert fake_client.chat.completions.create.await_count == 1


def test_inspired_by_drops_hallucinated_source_ids():
    """LLM cites s99 (does not exist) — merger should drop it but keep valid ones."""
    suggestion_with_bad_id = dict(SAMPLE_SUGGESTION)
    suggestion_with_bad_id["inspired_by"] = [
        {"feature": "real one", "source_ids": ["s1", "s99"]},
        {"feature": "fully fake", "source_ids": ["s42"]},
    ]

    async def _fake_research(jd, **_):
        return _fake_research_result()

    fake_client = _multi_call_stub_client([suggestion_with_bad_id])
    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=fake_client), \
         patch.object(project_suggester, "research_jd", _fake_research):
        result = project_suggester.run_layer4_sync("anything non-empty")

    inspired = result.suggestion["inspired_by"]
    assert inspired == [{"feature": "real one", "source_ids": ["s1"]}]


def test_research_failure_does_not_break_layer4():
    """If research_jd raises, Layer 4 should still produce a suggestion with empty sources."""
    async def _broken_research(jd, **_):
        raise RuntimeError("boom")

    fake_client = _multi_call_stub_client([SAMPLE_SUGGESTION])
    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=fake_client), \
         patch.object(project_suggester, "research_jd", _broken_research):
        result = project_suggester.run_layer4_sync("JD text")

    assert result.suggestion["project_title"] == "Rate-Limited Public API"
    assert result.research.sources == []
    # research_sources field forced to [] when no sources fetched
    assert result.suggestion["research_sources"] == []
    assert result.suggestion["inspired_by"] == []


def test_log_run_writes_input_output_research_and_index(tmp_path):
    result = project_suggester.Layer4Result(
        suggestion=SAMPLE_SUGGESTION,
        model="test-model",
        elapsed_ms=987,
        usage=project_suggester.CallUsage(prompt_tokens=11, completion_tokens=22),
        research=_fake_research_result(),
    )

    rec = project_suggester.log_run(
        jd="Some JD text", result=result, log_root=tmp_path,
    )

    assert (tmp_path / "index.jsonl").exists()
    run_dir = tmp_path / rec["run_id"]
    assert (run_dir / "input.json").exists()
    assert (run_dir / "output.json").exists()
    assert (run_dir / "research.json").exists()

    saved_in = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
    saved_out = json.loads((run_dir / "output.json").read_text(encoding="utf-8"))
    saved_research = json.loads((run_dir / "research.json").read_text(encoding="utf-8"))
    assert saved_in["jd"] == "Some JD text"
    assert saved_out["model"] == "test-model"
    assert saved_out["suggestion"]["project_title"] == "Rate-Limited Public API"
    assert saved_research["queries"] == ["q1", "q2", "q3"]
    assert saved_research["sources"][0]["url"] == "https://www.reddit.com/r/foo/1"

    line = (tmp_path / "index.jsonl").read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["run_id"] == rec["run_id"]
    assert parsed["model"] == "test-model"
    assert parsed["jd_chars"] == len("Some JD text")
    assert parsed["output_summary"]["project_title"] == "Rate-Limited Public API"
    assert parsed["output_summary"]["must_have_count"] == 3
    assert parsed["output_summary"]["stretch_count"] == 2
    assert parsed["output_summary"]["skills_count"] == 2
    assert parsed["output_summary"]["rubric_count"] == 2
    assert parsed["output_summary"]["sources_count"] == 1
    assert parsed["output_summary"]["source_platforms"] == ["reddit"]
    assert parsed["output_summary"]["queries"] == ["q1", "q2", "q3"]
    assert parsed["output_summary"]["inspired_by_count"] == 1
    assert parsed["output_summary"]["is_error"] is False
    assert "research_path" in parsed


def test_log_run_appends_multiple_lines(tmp_path):
    result = project_suggester.Layer4Result(
        suggestion={"project_title": "x"},
        model="m",
        elapsed_ms=1,
        usage=project_suggester.CallUsage(),
        research=research.ResearchResult(),
    )
    project_suggester.log_run("jd1", result, log_root=tmp_path)
    project_suggester.log_run("jd2", result, log_root=tmp_path)

    lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["model"] == "m" for line in lines)
