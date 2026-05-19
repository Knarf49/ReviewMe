"""
Layer 3 — AI Context Review.

Three LLM calls (parallel via asyncio.gather):
  - Call A: Architectural judgement
  - Call B: Job-fit gap analysis (only if JD provided)
  - Call C: Teaching feedback for Layer 0/1 findings

Hard rule: AI never decides severity. Layer 1 is the deterministic
authority. Call C output has its `severity` field overwritten with the
Layer 1 value before persisting, and any finding_id not present in the
Layer 1/0 input is dropped.

Usage:
    from ai_reviewer import run_layer3_sync
    layer3 = run_layer3_sync(analysis_dict, jd="")

CLI smoke test:
    python ai_reviewer.py path/to/analysis.json [path/to/jd.txt]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

from llm_client import DEFAULT_MODELS, OLLAMA_API_KEY, OLLAMA_BASE_URL

load_dotenv()

MODEL = "gpt-5.4-mini"
TEMPERATURE = 0
MAX_TOKENS = 2000
PROMPTS_DIR = Path(__file__).parent / "prompts"


def _resolve_provider_model(
    provider: str | None, model: str | None
) -> tuple[str, str]:
    p = (provider or os.environ.get("LLM_PROVIDER") or "openai").lower().strip()
    if p not in ("openai", "ollama"):
        raise ValueError(f"Unknown LLM provider {p!r}")
    m = (model or "").strip()
    if not m:
        env_key = "OLLAMA_MODEL" if p == "ollama" else "OPENAI_MODEL"
        m = os.environ.get(env_key) or DEFAULT_MODELS[p]
    return p, m


def _build_async_client(provider: str) -> AsyncOpenAI:
    if provider == "ollama":
        return AsyncOpenAI(api_key=OLLAMA_API_KEY, base_url=OLLAMA_BASE_URL)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY missing. Add it to .env or pick the ollama provider."
        )
    return AsyncOpenAI(api_key=api_key)


# ── Schema ───────────────────────────────────────────────────

@dataclass
class CallUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class Layer3Result:
    call_a: dict
    call_b: dict | None        # None when no JD was provided
    call_c: dict
    model: str
    elapsed_ms: int
    usage: dict[str, CallUsage] = field(default_factory=dict)
    dropped_finding_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "usage": {k: {"prompt_tokens": v.prompt_tokens,
                          "completion_tokens": v.completion_tokens}
                      for k, v in self.usage.items()},
            "call_a_architecture": self.call_a,
            "call_b_jobfit": self.call_b,
            "call_c_teaching": self.call_c,
            "dropped_finding_ids": self.dropped_finding_ids,
        }


# ── Prompts ──────────────────────────────────────────────────

def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


# ── Finding-id helpers ───────────────────────────────────────

def _finding_id(f: dict) -> str:
    return f"{f.get('tool', '?')}:{f.get('rule_id', '?')}:{f.get('file', '?')}:{f.get('line', '?')}"


def _collect_input_findings(analysis: dict) -> dict[str, dict]:
    """Return {finding_id: finding_dict} for all Layer 0 + Layer 1 findings."""
    out: dict[str, dict] = {}
    for layer in ("layer0_deps", "layer1_static"):
        for f in (analysis.get(layer) or {}).get("findings", []) or []:
            out[_finding_id(f)] = f
    return out


# ── LLM call helpers ─────────────────────────────────────────

async def _chat_json(
    client: AsyncOpenAI,
    system: str,
    user: str,
    label: str,
    max_tokens: int | None = None,
    model: str | None = None,
) -> tuple[dict, CallUsage]:
    """One JSON-mode chat completion with one retry on invalid JSON."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    cap = max_tokens or MAX_TOKENS
    eff_model = model or MODEL
    for attempt in (1, 2):
        resp = await client.chat.completions.create(
            model=eff_model,
            temperature=TEMPERATURE,
            max_completion_tokens=cap,
            response_format={"type": "json_object"},
            messages=messages,
        )
        raw = resp.choices[0].message.content or ""
        usage = CallUsage(
            prompt_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            completion_tokens=resp.usage.completion_tokens if resp.usage else 0,
        )
        try:
            return json.loads(raw), usage
        except json.JSONDecodeError:
            if attempt == 2:
                return ({"error": "model returned invalid JSON",
                         "raw": raw[:500]}, usage)
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    f"Your previous reply for {label} was not valid JSON. "
                    "Return ONLY a single JSON object matching the schema."
                ),
            })
    # Unreachable, but keeps type checker happy.
    return ({"error": "unreachable"}, CallUsage())


# ── Three calls ──────────────────────────────────────────────

async def _call_a_architecture(
    client: AsyncOpenAI, analysis: dict, model: str | None = None
) -> tuple[dict, CallUsage]:
    system = _load_prompt("layer3_call_a.md")
    l2 = analysis.get("layer2_ast") or {}
    payload = {
        "languages": (analysis.get("layer1_static") or {}).get("languages", []),
        "ast_summary": l2.get("summary", {}),
        "module_graph": l2.get("module_graph", {}),
        "entry_points": l2.get("entry_points", []),
        "complexity_hotspots": l2.get("complexity_hotspots", []),
    }
    user = (
        "Analyse this project's architecture using the deterministic AST data below. "
        "Return JSON only.\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```"
    )
    return await _chat_json(client, system, user, "Call A", model=model)


async def _call_b_jobfit(
    client: AsyncOpenAI, analysis: dict, jd: str, model: str | None = None
) -> tuple[dict, CallUsage]:
    system = _load_prompt("layer3_call_b.md")
    user = (
        "Compare the project against the job description below. Return JSON only.\n\n"
        "### Job Description\n"
        f"{jd.strip()}\n\n"
        "### Analysis (Layers 0/1/2)\n"
        f"```json\n{json.dumps(analysis, indent=2)}\n```"
    )
    return await _chat_json(client, system, user, "Call B", model=model)


async def _call_c_teaching(
    client: AsyncOpenAI, analysis: dict, model: str | None = None
) -> tuple[dict, CallUsage]:
    system = _load_prompt("layer3_call_c.md")
    findings_in: list[dict] = []
    for layer in ("layer0_deps", "layer1_static"):
        for f in (analysis.get(layer) or {}).get("findings", []) or []:
            findings_in.append({
                "finding_id": _finding_id(f),
                "tool": f.get("tool"),
                "category": f.get("category"),
                "severity": f.get("severity"),
                "file": f.get("file"),
                "line": f.get("line"),
                "rule_id": f.get("rule_id"),
                "message": f.get("message"),
            })
    hotspots = (analysis.get("layer2_ast") or {}).get("complexity_hotspots", [])[:5]
    payload = {"findings": findings_in, "complexity_hotspots_for_context": hotspots}
    user = (
        "Explain the findings below to a junior developer. Return JSON only. "
        "Do NOT change severity — host code will overwrite it from Layer 1.\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```"
    )
    return await _chat_json(client, system, user, "Call C", model=model)


# ── Guardrail: post-process Call C ───────────────────────────

def _enforce_severity_and_filter(
    call_c: dict, input_findings: dict[str, dict]
) -> tuple[dict, list[str]]:
    """Drop explanations whose finding_id is not in Layer 0/1 input.
    Overwrite each kept explanation's severity with the authoritative
    Layer 1 (or Layer 0) value.
    """
    if "error" in call_c:
        return call_c, []
    explanations = call_c.get("explanations") or []
    if not isinstance(explanations, list):
        return call_c, []
    kept: list[dict] = []
    dropped: list[str] = []
    for e in explanations:
        if not isinstance(e, dict):
            continue
        fid = e.get("finding_id", "")
        src = input_findings.get(fid)
        if src is None:
            dropped.append(fid)
            continue
        e["severity"] = src.get("severity")  # authoritative copy
        kept.append(e)
    call_c["explanations"] = kept
    return call_c, dropped


# ── Public entry points ──────────────────────────────────────

async def run_layer3(
    analysis: dict,
    jd: str = "",
    provider: str | None = None,
    model: str | None = None,
) -> Layer3Result:
    eff_provider, eff_model = _resolve_provider_model(provider, model)
    client = _build_async_client(eff_provider)
    start = time.time()

    has_jd = bool(jd and jd.strip())
    tasks: list[Any] = [_call_a_architecture(client, analysis, model=eff_model)]
    if has_jd:
        tasks.append(_call_b_jobfit(client, analysis, jd, model=eff_model))
    tasks.append(_call_c_teaching(client, analysis, model=eff_model))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    def _unpack(idx: int, label: str) -> tuple[dict, CallUsage]:
        r = results[idx]
        if isinstance(r, BaseException):
            return ({"error": f"{type(r).__name__}: {r}"}, CallUsage())
        return r

    a_idx = 0
    b_idx = 1 if has_jd else None
    c_idx = 2 if has_jd else 1

    call_a, usage_a = _unpack(a_idx, "Call A")
    call_c, usage_c = _unpack(c_idx, "Call C")
    if b_idx is not None:
        call_b, usage_b = _unpack(b_idx, "Call B")
    else:
        call_b = None
        usage_b = CallUsage()

    input_findings = _collect_input_findings(analysis)
    call_c, dropped = _enforce_severity_and_filter(call_c, input_findings)

    elapsed_ms = int((time.time() - start) * 1000)
    usage = {"call_a": usage_a, "call_c": usage_c}
    if call_b is not None:
        usage["call_b"] = usage_b

    return Layer3Result(
        call_a=call_a,
        call_b=call_b,
        call_c=call_c,
        model=eff_model,
        elapsed_ms=elapsed_ms,
        usage=usage,
        dropped_finding_ids=dropped,
    )


def run_layer3_sync(
    analysis: dict,
    jd: str = "",
    provider: str | None = None,
    model: str | None = None,
) -> Layer3Result:
    return asyncio.run(run_layer3(analysis, jd, provider=provider, model=model))


# ── Eval logging ─────────────────────────────────────────────

def log_run(
    analysis: dict,
    jd: str,
    result: Layer3Result,
    target: str,
    log_root: str | Path = "results/layer3_logs",
) -> dict:
    """Persist one Layer 3 run for future model-comparison evals.

    Layout:
      <log_root>/
        index.jsonl                       — one line per run (metadata + paths)
        <run_id>/input.json               — {analysis, jd}  (replayable input)
        <run_id>/output.json              — full Layer 3 result
    Returns the index record so callers can show the run_id to the user.
    """
    log_root = Path(log_root)
    log_root.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc)
    run_id = ts.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = log_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    input_path = run_dir / "input.json"
    output_path = run_dir / "output.json"
    input_path.write_text(
        json.dumps({"analysis": analysis, "jd": jd}, indent=2),
        encoding="utf-8",
    )
    output_path.write_text(
        json.dumps(result.to_dict(), indent=2),
        encoding="utf-8",
    )

    l1 = (analysis.get("layer1_static") or {}).get("summary", {})
    l2 = (analysis.get("layer2_ast") or {}).get("summary", {})
    l0 = (analysis.get("layer0_deps") or {}).get("summary", {})
    input_finding_count = len(_collect_input_findings(analysis))
    explanations = (result.call_c or {}).get("explanations") or []

    record = {
        "run_id": run_id,
        "timestamp_utc": ts.isoformat(),
        "target": str(target),
        "model": result.model,
        "elapsed_ms": result.elapsed_ms,
        "has_jd": bool(jd and jd.strip()),
        "jd_chars": len(jd or ""),
        "usage": {k: {"prompt_tokens": v.prompt_tokens,
                      "completion_tokens": v.completion_tokens}
                  for k, v in result.usage.items()},
        "dropped_finding_ids_count": len(result.dropped_finding_ids),
        "input_summary": {
            "layer0": l0,
            "layer1": l1,
            "layer2": l2,
            "input_finding_count": input_finding_count,
        },
        "output_summary": {
            "explanations_count": len(explanations),
            "call_a_error": "error" in (result.call_a or {}),
            "call_b_present": result.call_b is not None,
            "call_b_error": isinstance(result.call_b, dict) and "error" in result.call_b,
            "call_c_error": "error" in (result.call_c or {}),
        },
        "input_path": str(input_path).replace("\\", "/"),
        "output_path": str(output_path).replace("\\", "/"),
    }
    with (log_root / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


# ── CLI smoke test ───────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Layer 3 — AI context review")
    ap.add_argument("analysis_json", help="path to results/analysis_<name>.json")
    ap.add_argument("--jd", default="", help="path to a JD text file (optional)")
    ap.add_argument("--out", default="", help="path to write layer3 JSON (optional)")
    args = ap.parse_args()

    analysis_path = Path(args.analysis_json).resolve()
    if not analysis_path.exists():
        print(f"not found: {analysis_path}", file=sys.stderr)
        sys.exit(2)
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

    jd = ""
    if args.jd:
        jd_path = Path(args.jd).resolve()
        if not jd_path.exists():
            print(f"JD not found: {jd_path}", file=sys.stderr)
            sys.exit(2)
        jd = jd_path.read_text(encoding="utf-8")

    result = run_layer3_sync(analysis, jd)
    out_json = json.dumps(result.to_dict(), indent=2)

    if args.out:
        Path(args.out).write_text(out_json, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(out_json)

    print(
        f"\nLayer 3 done in {result.elapsed_ms} ms · "
        f"calls: A{'/B' if result.call_b is not None else ''}/C · "
        f"dropped {len(result.dropped_finding_ids)} bogus finding_ids",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
