"""
Layer 4 — Project Suggestion from a Job Description (grounded in web research).

Two-step pipeline:
  1. Layer 4a (research_jd): hits Reddit + Product Hunt for related pain
     points / products, returns a list of Source records.
  2. Layer 4 main call: feeds JD + sources into the LLM, asks for one
     buildable portfolio project that cites those sources via `inspired_by`.

Reuses LLM plumbing from `ai_reviewer` (chat helper, prompt loader, model +
usage dataclass) — no Layers 0/1/2/3 rewrite.

Usage:
    from project_suggester import run_layer4_sync
    result = run_layer4_sync(jd_text)
    print(result.suggestion["project_title"])
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from ai_reviewer import (
    MODEL,
    CallUsage,
    _build_async_client,
    _chat_json,
    _load_prompt,
    _resolve_provider_model,
)
from research import ResearchResult, Source, research_jd

load_dotenv()


@dataclass
class Layer4Result:
    suggestion: dict
    model: str
    elapsed_ms: int
    usage: CallUsage                                  # aggregate: research + main
    research: ResearchResult = field(default_factory=ResearchResult)

    def to_dict(self) -> dict:
        return {
            "suggestion": self.suggestion,
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "usage": asdict(self.usage),
            "research": self.research.to_dict(),
        }


def _format_sources_block(sources: list[Source]) -> str:
    if not sources:
        return ""
    payload = []
    for s in sources:
        entry: dict = {
            "id": s.id,
            "platform": s.platform,
            "url": s.url,
            "title": s.title,
            "snippet": s.snippet,
        }
        if s.github_stack:
            entry["github_stack"] = s.github_stack
        payload.append(entry)
    return (
        "\n\n### Research Sources\n"
        "Use these to ground the project in real observed user pain. "
        "Echo each source unchanged into `research_sources` and fill `pain_point` "
        "as a short paraphrase. Cite ids in `inspired_by`. Do NOT invent sources. "
        "When a source has `github_stack`, that is the real detected tech of the "
        "linked open-source product — treat it as evidence when picking "
        "`recommended_stack` if it fits the JD. Do NOT echo `github_stack` back; "
        "host code preserves it deterministically.\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```"
    )


def _merge_sources_into_suggestion(
    suggestion: dict, sources: list[Source]
) -> dict:
    """Trust the deterministic source records over whatever the LLM echoed.

    Keep LLM-derived `pain_point` per id, but force id/platform/url/title/snippet
    back to what we fetched. Drop hallucinated ids in `inspired_by`.
    """
    if not isinstance(suggestion, dict):
        return suggestion
    if not sources:
        # No sources fetched → wipe any hallucinated content the LLM produced
        suggestion["research_sources"] = []
        suggestion["inspired_by"] = []
        return suggestion

    by_id = {s.id: s for s in sources}
    llm_sources = suggestion.get("research_sources") or []
    pain_by_id: dict[str, str] = {}
    if isinstance(llm_sources, list):
        for row in llm_sources:
            if isinstance(row, dict):
                rid = row.get("id")
                pp = row.get("pain_point")
                if isinstance(rid, str) and isinstance(pp, str):
                    pain_by_id[rid] = pp.strip()

    suggestion["research_sources"] = [
        {
            "id": s.id,
            "platform": s.platform,
            "url": s.url,
            "title": s.title,
            "snippet": s.snippet,
            "pain_point": pain_by_id.get(s.id, ""),
            **({"github_stack": s.github_stack} if s.github_stack else {}),
        }
        for s in sources
    ]

    valid_ids = set(by_id.keys())
    cleaned_inspired: list[dict] = []
    for row in suggestion.get("inspired_by") or []:
        if not isinstance(row, dict):
            continue
        feature = row.get("feature")
        ids = row.get("source_ids") or []
        if not isinstance(feature, str) or not isinstance(ids, list):
            continue
        kept_ids = [i for i in ids if isinstance(i, str) and i in valid_ids]
        if kept_ids:
            cleaned_inspired.append({"feature": feature, "source_ids": kept_ids})
    suggestion["inspired_by"] = cleaned_inspired
    return suggestion


async def run_layer4(
    jd: str,
    provider: str | None = None,
    model: str | None = None,
) -> Layer4Result:
    jd = (jd or "").strip()
    if not jd:
        raise ValueError("JD is required for Layer 4 project suggestion.")

    eff_provider, eff_model = _resolve_provider_model(provider, model)

    start = time.time()
    try:
        research = await research_jd(jd, provider=eff_provider, model=eff_model)
    except Exception as e:
        import sys
        sys.stderr.write(f"[layer4] research step failed: {e}\n")
        research = ResearchResult()

    client = _build_async_client(eff_provider)
    system = _load_prompt("layer4_project_suggestion.md")
    user = (
        "Suggest one portfolio project for the candidate based on the JD below. "
        "Return JSON only.\n\n"
        "### Job Description\n"
        f"{jd}"
        f"{_format_sources_block(research.sources)}"
    )

    payload, main_usage = await _chat_json(
        client, system, user, "Layer 4", max_tokens=4500, model=eff_model,
    )
    suggestion = _merge_sources_into_suggestion(payload, research.sources)
    elapsed_ms = int((time.time() - start) * 1000)

    total_usage = CallUsage(
        prompt_tokens=research.usage.prompt_tokens + main_usage.prompt_tokens,
        completion_tokens=research.usage.completion_tokens + main_usage.completion_tokens,
    )
    return Layer4Result(
        suggestion=suggestion,
        model=eff_model,
        elapsed_ms=elapsed_ms,
        usage=total_usage,
        research=research,
    )


def run_layer4_sync(
    jd: str,
    provider: str | None = None,
    model: str | None = None,
) -> Layer4Result:
    return asyncio.run(run_layer4(jd, provider=provider, model=model))


def log_run(
    jd: str,
    result: Layer4Result,
    log_root: str | Path = "results/layer4_logs",
) -> dict:
    """Persist one Layer 4 run for future model-comparison evals.

    Layout:
      <log_root>/
        index.jsonl                — one line per run
        <run_id>/input.json        — {jd}
        <run_id>/output.json       — full Layer 4 result (incl. research)
        <run_id>/research.json     — research-step artifact (queries + sources)
    """
    log_root = Path(log_root)
    log_root.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc)
    run_id = ts.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = log_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    input_path = run_dir / "input.json"
    output_path = run_dir / "output.json"
    research_path = run_dir / "research.json"
    input_path.write_text(
        json.dumps({"jd": jd}, indent=2),
        encoding="utf-8",
    )
    output_path.write_text(
        json.dumps(result.to_dict(), indent=2),
        encoding="utf-8",
    )
    research_path.write_text(
        json.dumps(result.research.to_dict(), indent=2),
        encoding="utf-8",
    )

    s = result.suggestion or {}
    platforms = sorted({src.platform for src in result.research.sources if src.platform})
    record = {
        "run_id": run_id,
        "timestamp_utc": ts.isoformat(),
        "model": result.model,
        "elapsed_ms": result.elapsed_ms,
        "jd_chars": len(jd or ""),
        "usage": asdict(result.usage),
        "output_summary": {
            "project_title": s.get("project_title", ""),
            "must_have_count": len(s.get("must_have_features", []) or []),
            "stretch_count": len(s.get("stretch_goals", []) or []),
            "skills_count": len(s.get("skills_demonstrated", []) or []),
            "rubric_count": len(s.get("success_rubric", []) or []),
            "sources_count": len(result.research.sources),
            "source_platforms": platforms,
            "inspired_by_count": len(s.get("inspired_by", []) or []),
            "queries": list(result.research.queries),
            "is_error": "error" in s,
        },
        "input_path": str(input_path).replace("\\", "/"),
        "output_path": str(output_path).replace("\\", "/"),
        "research_path": str(research_path).replace("\\", "/"),
    }
    with (log_root / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record
