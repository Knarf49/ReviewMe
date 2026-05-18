"""
Tests for Layer 3 (ai_reviewer).

Key invariants validated:
  1. AI cannot change severity — Layer 1's value is always copied back.
  2. AI cannot invent findings — any finding_id not in Layer 0/1 input is dropped.
  3. Missing JD skips Call B; result.call_b is None.
  4. With JD, gather is called with 3 coroutines; without JD, with 2.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_reviewer  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────

def _mk_completion(payload: dict, prompt_tokens: int = 10, completion_tokens: int = 20):
    """Build a fake OpenAI ChatCompletion response."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(payload)),
        )],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def _analysis_with_findings() -> dict:
    return {
        "layer0_deps": {"findings": []},
        "layer1_static": {
            "languages": ["python"],
            "findings": [
                {
                    "tool": "bandit",
                    "rule_id": "B105",
                    "file": "app.py",
                    "line": 42,
                    "severity": "high",
                    "category": "security",
                    "message": "hardcoded password",
                },
                {
                    "tool": "ruff",
                    "rule_id": "E501",
                    "file": "app.py",
                    "line": 10,
                    "severity": "low",
                    "category": "quality",
                    "message": "line too long",
                },
            ],
        },
        "layer2_ast": {
            "summary": {"max_cyclomatic": 4},
            "module_graph": {"app": []},
            "entry_points": ["app.py"],
            "complexity_hotspots": [],
        },
    }


def _stub_client(call_a_json: dict, call_c_json: dict, call_b_json: dict | None = None):
    """Build a fake AsyncOpenAI whose `chat.completions.create` returns
    the right payload based on which prompt landed in the system message."""

    async def _create(**kwargs):
        sys_msg = kwargs["messages"][0]["content"]
        if "Call A" in sys_msg or "Architectural Judgement" in sys_msg:
            return _mk_completion(call_a_json)
        if "Call B" in sys_msg or "Job-Fit Gap" in sys_msg:
            assert call_b_json is not None, "Call B fired but no JD-mode JSON given"
            return _mk_completion(call_b_json)
        if "Call C" in sys_msg or "Teaching Feedback" in sys_msg:
            return _mk_completion(call_c_json)
        raise AssertionError(f"unexpected system prompt: {sys_msg[:80]}")

    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)
    return client


# ── Tests ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_severity_is_overwritten_with_layer1_value(monkeypatch):
    """Even if AI tries to downgrade severity, host code restores Layer 1."""
    analysis = _analysis_with_findings()
    call_a = {"scale_assessment": "ok", "patterns_observed": [],
              "anti_patterns": [], "recommendations": []}
    call_c = {
        "explanations": [
            {
                "finding_id": "bandit:B105:app.py:42",
                "severity": "low",  # AI lies — should be overwritten to "high"
                "why_it_matters": "...",
                "trade_off": "...",
                "reference": "CWE-798",
            },
            {
                "finding_id": "ruff:E501:app.py:10",
                "severity": "critical",  # AI lies — should be overwritten to "low"
                "why_it_matters": "...",
                "trade_off": "...",
                "reference": "n/a",
            },
        ],
        "overall_tone_note": "good start",
    }
    fake = _stub_client(call_a, call_c)
    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=fake):
        result = ai_reviewer.run_layer3_sync(analysis, jd="")

    by_id = {e["finding_id"]: e for e in result.call_c["explanations"]}
    assert by_id["bandit:B105:app.py:42"]["severity"] == "high"
    assert by_id["ruff:E501:app.py:10"]["severity"] == "low"


def test_bogus_finding_id_is_dropped(monkeypatch):
    """AI invents a finding the static analyzers never produced → dropped."""
    analysis = _analysis_with_findings()
    call_a = {"scale_assessment": "", "patterns_observed": [],
              "anti_patterns": [], "recommendations": []}
    call_c = {
        "explanations": [
            {
                "finding_id": "bandit:B105:app.py:42",  # real
                "severity": "high",
                "why_it_matters": "...",
                "trade_off": "...",
                "reference": "CWE-798",
            },
            {
                "finding_id": "bandit:B999:app.py:1",  # fake — not in input
                "severity": "critical",
                "why_it_matters": "AI hallucinated this",
                "trade_off": "...",
                "reference": "n/a",
            },
        ],
        "overall_tone_note": "",
    }
    fake = _stub_client(call_a, call_c)
    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=fake):
        result = ai_reviewer.run_layer3_sync(analysis, jd="")

    ids = [e["finding_id"] for e in result.call_c["explanations"]]
    assert ids == ["bandit:B105:app.py:42"]
    assert result.dropped_finding_ids == ["bandit:B999:app.py:1"]


def test_skip_call_b_when_no_jd(monkeypatch):
    analysis = _analysis_with_findings()
    call_a = {"scale_assessment": "", "patterns_observed": [],
              "anti_patterns": [], "recommendations": []}
    call_c = {"explanations": [], "overall_tone_note": ""}
    fake = _stub_client(call_a, call_c)
    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=fake):
        result = ai_reviewer.run_layer3_sync(analysis, jd="   ")  # whitespace = empty

    assert result.call_b is None
    # 2 LLM calls only (A and C)
    assert fake.chat.completions.create.await_count == 2


def test_call_b_runs_when_jd_present(monkeypatch):
    analysis = _analysis_with_findings()
    call_a = {"scale_assessment": "", "patterns_observed": [],
              "anti_patterns": [], "recommendations": []}
    call_b = {"covered_skills": [], "missing_skills": [],
              "next_project_suggestion": "build X"}
    call_c = {"explanations": [], "overall_tone_note": ""}
    fake = _stub_client(call_a, call_c, call_b_json=call_b)
    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=fake):
        result = ai_reviewer.run_layer3_sync(
            analysis, jd="We need a Python backend dev with FastAPI experience."
        )

    assert result.call_b == call_b
    assert fake.chat.completions.create.await_count == 3


def test_three_calls_run_in_parallel(monkeypatch):
    """If gather is truly parallel, total wall time ≈ slowest single call,
    not the sum. We give each fake call a 200 ms sleep and assert the run
    finishes well under 3 × 200 ms."""
    import time

    analysis = _analysis_with_findings()
    call_a = {"scale_assessment": "", "patterns_observed": [],
              "anti_patterns": [], "recommendations": []}
    call_b = {"covered_skills": [], "missing_skills": [],
              "next_project_suggestion": ""}
    call_c = {"explanations": [], "overall_tone_note": ""}

    async def _slow_create(**kwargs):
        await asyncio.sleep(0.2)
        sys_msg = kwargs["messages"][0]["content"]
        if "Architectural" in sys_msg:
            return _mk_completion(call_a)
        if "Job-Fit" in sys_msg:
            return _mk_completion(call_b)
        return _mk_completion(call_c)

    fake = MagicMock()
    fake.chat = MagicMock()
    fake.chat.completions = MagicMock()
    fake.chat.completions.create = AsyncMock(side_effect=_slow_create)

    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=fake):
        start = time.time()
        result = ai_reviewer.run_layer3_sync(analysis, jd="some JD")
        elapsed = time.time() - start

    # 3 × 0.2 s sequentially = 0.6 s; parallel should be ~0.2 s. Cap at 0.45 s.
    assert elapsed < 0.45, f"calls appear sequential: {elapsed:.3f}s"
    assert result.call_b is not None


def test_invalid_json_falls_back_to_error(monkeypatch):
    """If model emits junk twice, Call A becomes an `error` blob; rest still ships."""
    analysis = _analysis_with_findings()
    call_c = {"explanations": [], "overall_tone_note": ""}

    async def _create(**kwargs):
        sys_msg = kwargs["messages"][0]["content"]
        if "Architectural" in sys_msg:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )
        return _mk_completion(call_c)

    fake = MagicMock()
    fake.chat = MagicMock()
    fake.chat.completions = MagicMock()
    fake.chat.completions.create = AsyncMock(side_effect=_create)

    with patch.object(ai_reviewer, "AsyncOpenAI", return_value=fake):
        result = ai_reviewer.run_layer3_sync(analysis, jd="")

    assert "error" in result.call_a
    assert result.call_c == {"explanations": [], "overall_tone_note": ""}


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        ai_reviewer.run_layer3_sync(_analysis_with_findings(), jd="")


def test_log_run_writes_input_output_and_index(tmp_path):
    analysis = _analysis_with_findings()
    result = ai_reviewer.Layer3Result(
        call_a={"scale_assessment": "ok"},
        call_b=None,
        call_c={"explanations": [
            {"finding_id": "bandit:B105:app.py:42", "severity": "high",
             "why_it_matters": "x", "trade_off": "y", "reference": "CWE-798"},
        ], "overall_tone_note": "good"},
        model="test-model",
        elapsed_ms=1234,
        usage={"call_a": ai_reviewer.CallUsage(10, 20),
               "call_c": ai_reviewer.CallUsage(30, 40)},
        dropped_finding_ids=[],
    )

    rec = ai_reviewer.log_run(
        analysis, jd="", result=result,
        target="C:\\fake\\repo", log_root=tmp_path,
    )

    assert (tmp_path / "index.jsonl").exists()
    run_dir = tmp_path / rec["run_id"]
    assert (run_dir / "input.json").exists()
    assert (run_dir / "output.json").exists()

    saved_in = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
    saved_out = json.loads((run_dir / "output.json").read_text(encoding="utf-8"))
    assert saved_in["analysis"] == analysis
    assert saved_in["jd"] == ""
    assert saved_out["model"] == "test-model"

    # index line is appendable, valid JSON, with all expected fields
    line = (tmp_path / "index.jsonl").read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["run_id"] == rec["run_id"]
    assert parsed["has_jd"] is False
    assert parsed["output_summary"]["explanations_count"] == 1
    assert parsed["input_summary"]["input_finding_count"] == 2
