# ============================================================
# AI Code Reviewer — Eval Script (OpenRouter + SQLite)
# ============================================================
# Setup:
#   1. Copy .env and fill in API keys
#   2. .venv\Scripts\activate
#   3. python eval.py
# ============================================================

import json
import os
import sqlite3
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from tabulate import tabulate
from tqdm import tqdm

import skills

load_dotenv()

# ── Config ───────────────────────────────────────────────────

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENAI_API_KEY     = os.environ["OPENAI_API_KEY"]

DB_PATH      = Path(__file__).parent / "data.db"
RESULTS_DIR  = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

client = OpenAI(
    api_key=OPENAI_API_KEY,
)

judge_client = OpenAI(
    api_key=OPENAI_API_KEY,
)

MODELS = [
    "gpt-5.4-mini",
]

TEMPERATURE         = 0
RUNS_PER_CASE       = 5
DELAY_BETWEEN_CALLS = 3


# ── Load Test Cases from SQLite ──────────────────────────────

@dataclass
class TestCase:
    id: str
    dimension: str
    weight: float
    case_type: str
    language: str
    code: str
    ground_truth: dict


def load_test_cases(dimension: Optional[str] = None) -> list[TestCase]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    if dimension:
        cur.execute("SELECT * FROM test_cases WHERE dimension = ? ORDER BY id", (dimension,))
    else:
        cur.execute("SELECT * FROM test_cases ORDER BY id")

    rows = cur.fetchall()
    con.close()

    if not rows:
        raise ValueError("No rows in test_cases — run setup_db.py first")

    test_cases = []
    for row in rows:
        ground_truth = {
            "issue":    row["gt_issue"],
            "line":     row["gt_line"],
            "severity": row["gt_severity"],
            "standard": row["gt_standard"],
            "fix":      row["gt_fix"],
        }
        test_cases.append(TestCase(
            id=row["id"],
            dimension=row["dimension"],
            weight=float(row["weight"]),
            case_type=row["case_type"],
            language=row["language"],
            code=row["code"],
            ground_truth=ground_truth,
        ))

    print(f"Loaded {len(test_cases)} test cases from {DB_PATH.name}")
    return test_cases


# ── Prompt Builder ───────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior software engineer performing a code review.
Analyze the given code and identify issues across these dimensions:
- Security (OWASP standards)
- Correctness (bugs, edge cases, error handling)
- Readability & Maintainability (naming, SRP, function size)
- Design & Architecture (coupling, cohesion, complexity)
- Testability & Tests (dependency injection, coverage)
- Performance (algorithmic complexity, N+1 queries)
- Idiomatic Style (language conventions)

Respond ONLY with a JSON object. No markdown, no explanation outside JSON.

Format:
{
  "has_issue": true | false,
  "dimension": "<primary dimension>",
  "issue_summary": "<one sentence>",
  "line_number": <int or null>,
  "severity": "critical" | "high" | "medium" | "low" | null,
  "standard_reference": "<standard name>",
  "why": "<explain WHY this is a problem, not just what>",
  "fix": "<concrete fix suggestion>",
  "trade_offs": "<any trade-offs to consider>"
}

If the code is clean and has no significant issues, set has_issue to false, leave all fields null EXCEPT "why" — populate "why" with one sentence explaining why the code is clean (e.g. "Function has a single responsibility and handles all edge cases correctly.").

Dimension-specific rules:

Idiomatic — DO flag:
- panic(err) for recoverable errors (use return ..., err instead)
- new(T) followed by field assignment (use &T{field: val} struct literal)
- naked returns (bare `return` with no explicit values) in any function that has named return values AND is longer than 3 lines — these hurt readability. Example: `func divide(...) (result float64, err error) { ... return }` — flag this.
- non-standard acronym casing: Id, Url, Http (should be ID, URL, HTTP)

Idiomatic — do NOT flag:
- fmt.Errorf("...: %w", err) — correct Go error wrapping
- FindByID, GetByEmail — Go initialisms are correct
- returning (T, error) — idiomatic Go

Testability — flag when:
- function calls time.Now() directly (not injectable)
- function calls global db directly (not injectable)
- test function has ONLY one case covering the happy path with no error/edge cases

Testability — do NOT flag:
- table-driven tests with multiple cases including edge cases (even if one case is the happy path)
- functions that accept dependencies as parameters

Correctness — DO flag:
- functions that silently return a zero value (0, "", nil) when called with nil/empty input, with no error returned and no documentation — silent nil returns mask caller bugs even if the zero value is technically valid Go behavior. If the call site shows `func(nil)` this is always a flag.

Performance — DO flag:
- two separate linear passes over the same data when a single pass could compute the same result — e.g. counting in one loop then finding the max in a second loop when max could be tracked during counting

Readability — DO flag:
- functions that do more than one distinct responsibility (validate + calculate + save + notify = god function)
- variables/functions with single-letter or meaningless names (x, r, t, calc) when context is non-trivial
- deeply nested code (4+ levels of if/for/switch)
- numeric literals that require mental arithmetic to understand their meaning — always flag these regardless of function name. Example: `price * 0.85` requires computing 1-0.85=0.15 to know it is a 15% discount — flag it. `price * 0.95` requires computing 1-0.95=0.05 — flag it. `price > 1000` — 1000 is an unexplained business threshold — flag it. The test: can a reader instantly know what the number means WITHOUT arithmetic? If not, flag.
- do NOT flag numeric literals whose meaning is directly readable with zero arithmetic (e.g. `calculateDiscount` for tier "gold" returning `price * 0.20` — 20% is directly readable as twenty percent)

Readability — do NOT flag:
- short functions (under ~10 lines) with a single switch or if/else dispatching on one variable, AS LONG AS the values used are self-explanatory
- discount/rate functions where the percentage values are clear from the function name and tier/category labels

Design — DO flag:
- interfaces with more than 5 methods where callers only use a subset (ISP violation)
- factory patterns or wrapper structs used just to wrap a trivial operation (over-engineering)
- global package-level variables used as implicit dependencies

Severity calibration:
- critical: data loss, security breach, crash in production
- high: nil panic, race condition, error silently ignored, IDOR, hard-coded secret
- medium: poor naming, god function, over-engineering, missing edge case test
- low: style issues, minor idiom violations, non-blocking improvements

You have access to two tools for fetching reference knowledge:
- list_skills(): see available skill packs (e.g. framework-specific guidance).
- fetch_skill(name, file?): read a skill pack's content.
Call them ONLY when the snippet's language/framework matches a skill and the extra context would change your verdict. After any tool use, return the final JSON object as your last message — no extra prose."""


def build_user_prompt(tc: TestCase) -> str:
    return f"""Review this {tc.language} code snippet:

```{tc.language}
{tc.code.strip()}
```

Focus on dimension: {tc.dimension}
Identify any issues or confirm the code is clean."""


# ── Runner ───────────────────────────────────────────────────

@dataclass
class EvalResult:
    test_id: str
    model: str
    dimension: str
    case_type: str
    run: int
    raw_response: str
    parsed: Optional[dict]
    has_issue_predicted: bool
    severity_predicted: Optional[str]
    latency_ms: float
    parse_error: bool = False


MAX_TOOL_ITERATIONS = 4


def call_model(model: str, tc: TestCase, max_retries: int = 3) -> tuple[str, float]:
    base_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": build_user_prompt(tc)},
    ]

    for attempt in range(max_retries):
        try:
            start = time.time()
            messages = [dict(m) for m in base_messages]

            for _ in range(MAX_TOOL_ITERATIONS):
                response = client.chat.completions.create(
                    model=model,
                    temperature=TEMPERATURE,
                    max_completion_tokens=800,
                    response_format={"type": "json_object"},
                    tools=skills.TOOL_SCHEMAS,
                    messages=messages,
                )
                msg = response.choices[0].message
                tool_calls = getattr(msg, "tool_calls", None)

                if not tool_calls:
                    latency = (time.time() - start) * 1000
                    return msg.content, latency

                messages.append({
                    "role":       "assistant",
                    "content":    msg.content or "",
                    "tool_calls": [
                        {
                            "id":   tc_call.id,
                            "type": "function",
                            "function": {
                                "name":      tc_call.function.name,
                                "arguments": tc_call.function.arguments or "{}",
                            },
                        }
                        for tc_call in tool_calls
                    ],
                })

                for tc_call in tool_calls:
                    try:
                        args = json.loads(tc_call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = skills.dispatch(tc_call.function.name, args)
                    messages.append({
                        "role":         "tool",
                        "tool_call_id": tc_call.id,
                        "content":      result,
                    })

            # tool budget exhausted — force a final answer with no tools
            response = client.chat.completions.create(
                model=model,
                temperature=TEMPERATURE,
                max_completion_tokens=800,
                response_format={"type": "json_object"},
                messages=messages + [{
                    "role":    "user",
                    "content": "Return the final JSON object now. Do not call any more tools.",
                }],
            )
            latency = (time.time() - start) * 1000
            return response.choices[0].message.content, latency

        except Exception as e:
            error_str = str(e)
            retry_after = 30
            if "retry_after_seconds" in error_str:
                import re
                match = re.search(r"'retry_after_seconds': (\d+)", error_str)
                if match:
                    retry_after = int(match.group(1)) + 2

            if attempt < max_retries - 1:
                print(f"\nRate limited — retrying in {retry_after}s (attempt {attempt+1}/{max_retries})")
                time.sleep(retry_after)
            else:
                raise


def parse_response(raw: str) -> tuple[Optional[dict], bool]:
    try:
        clean = raw.strip()
        if "<think>" in clean:
            clean = clean.split("</think>")[-1].strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
        if clean.endswith("```"):
            clean = "\n".join(clean.split("\n")[:-1])
        return json.loads(clean.strip()), False
    except Exception:
        return None, True


def run_eval(models: list[str], test_suite: list[TestCase]) -> list[EvalResult]:
    results = []
    total = len(models) * len(test_suite) * RUNS_PER_CASE
    pbar = tqdm(total=total, desc="Evaluating")

    for model in models:
        for tc in test_suite:
            for run in range(RUNS_PER_CASE):
                try:
                    raw, latency = call_model(model, tc)
                    parsed, parse_error = parse_response(raw)
                    has_issue = parsed.get("has_issue", False) if parsed else False
                    severity = parsed.get("severity") if parsed else None
                except Exception as e:
                    raw = str(e)
                    parsed = None
                    parse_error = True
                    has_issue = False
                    severity = None
                    latency = 0.0

                results.append(EvalResult(
                    test_id=tc.id,
                    model=model,
                    dimension=tc.dimension,
                    case_type=tc.case_type,
                    run=run,
                    raw_response=raw,
                    parsed=parsed,
                    has_issue_predicted=has_issue,
                    severity_predicted=severity,
                    latency_ms=latency,
                    parse_error=parse_error,
                ))

                pbar.update(1)
                time.sleep(DELAY_BETWEEN_CALLS)

    pbar.close()
    return results


# ── Aggregate ────────────────────────────────────────────────

def aggregate_runs(results: list[EvalResult], test_suite: list[TestCase]) -> list[dict]:
    from collections import defaultdict

    groups: dict[tuple, list[EvalResult]] = defaultdict(list)
    for r in results:
        groups[(r.model, r.test_id)].append(r)

    aggregated = []
    for (model, test_id), runs in groups.items():
        tc = next(t for t in test_suite if t.id == test_id)

        votes = [r.has_issue_predicted for r in runs]
        majority = sum(votes) / len(votes) >= 0.6

        latencies = [r.latency_ms for r in runs]
        median_latency = statistics.median(latencies)

        severities = [r.severity_predicted for r in runs if r.severity_predicted]
        severity = severities[0] if severities else None

        aggregated.append({
            "model": model,
            "test_id": test_id,
            "dimension": tc.dimension,
            "weight": tc.weight,
            "case_type": tc.case_type,
            "ground_truth_has_issue": tc.ground_truth.get("issue") is not None,
            "ground_truth_severity": tc.ground_truth.get("severity"),
            "predicted_has_issue": majority,
            "predicted_severity": severity,
            "median_latency_ms": median_latency,
            "parse_errors": sum(r.parse_error for r in runs),
        })

    return aggregated


# ── Metrics ──────────────────────────────────────────────────

def compute_metrics(aggregated: list[dict]) -> pd.DataFrame:
    dimensions = list({r["dimension"] for r in aggregated})
    models = list({r["model"] for r in aggregated})
    rows = []

    for model in models:
        model_data = [r for r in aggregated if r["model"] == model]

        for dim in dimensions:
            dim_data = [r for r in model_data if r["dimension"] == dim]
            if not dim_data:
                continue

            tp = sum(1 for r in dim_data if r["ground_truth_has_issue"] and r["predicted_has_issue"])
            fp = sum(1 for r in dim_data if not r["ground_truth_has_issue"] and r["predicted_has_issue"])
            fn = sum(1 for r in dim_data if r["ground_truth_has_issue"] and not r["predicted_has_issue"])
            tn = sum(1 for r in dim_data if not r["ground_truth_has_issue"] and not r["predicted_has_issue"])

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            fpr       = fp / (fp + tn) if (fp + tn) > 0 else 0.0
            avg_lat   = statistics.mean(r["median_latency_ms"] for r in dim_data)
            weight    = dim_data[0]["weight"]

            rows.append({
                "Model": model.split("/")[-1],
                "Dimension": dim,
                "Weight": f"{int(weight*100)}%",
                "Precision": round(precision, 2),
                "Recall": round(recall, 2),
                "F1": round(f1, 2),
                "FP Rate": round(fpr, 2),
                "TP": tp, "FP": fp, "FN": fn, "TN": tn,
                "Avg Latency (ms)": round(avg_lat),
            })

    return pd.DataFrame(rows)


def compute_overall(metrics_df: pd.DataFrame) -> pd.DataFrame:
    weight_map = {
        "Security": 0.20, "Correctness": 0.20,
        "Readability": 0.15, "Design": 0.15, "Testability": 0.15,
        "Performance": 0.10, "Idiomatic": 0.05,
    }
    rows = []
    for model in metrics_df["Model"].unique():
        mdf = metrics_df[metrics_df["Model"] == model]
        weighted_f1 = sum(
            row["F1"] * weight_map.get(row["Dimension"], 0)
            for _, row in mdf.iterrows()
        )
        avg_fpr = mdf["FP Rate"].mean()
        avg_lat = mdf["Avg Latency (ms)"].mean()
        rows.append({
            "Model": model,
            "Weighted F1": round(weighted_f1, 3),
            "Avg FP Rate": round(avg_fpr, 2),
            "Avg Latency (ms)": round(avg_lat),
        })

    return pd.DataFrame(rows).sort_values("Weighted F1", ascending=False)


# ── LLM-as-Judge ─────────────────────────────────────────────

JUDGE_MODEL = "o3-mini"

JUDGE_PROMPT = """You are evaluating an AI code reviewer's response quality.

Test case ground truth:
{ground_truth}

AI reviewer's response:
{response}

Score the response on these criteria (1-5 each):
1. Accuracy: Did it correctly identify (or correctly pass) the issue?
2. Explanation quality: Does it explain WHY (not just what)?
3. Fix quality: Is the suggested fix concrete and correct?
4. Severity accuracy: Is the severity rating appropriate?
5. Junior-friendliness: Can a junior dev understand and act on this?

Respond ONLY with JSON:
{{
  "accuracy": <1-5>,
  "explanation_quality": <1-5>,
  "fix_quality": <1-5>,
  "severity_accuracy": <1-5>,
  "junior_friendliness": <1-5>,
  "overall": <1-5>,
  "notes": "<brief comment>"
}}"""


def judge_sample(tc: TestCase, result: EvalResult) -> Optional[dict]:
    if not result.raw_response or result.parse_error:
        return None
    prompt = JUDGE_PROMPT.format(
        ground_truth=json.dumps(tc.ground_truth, indent=2),
        response=result.raw_response[:2000],
    )
    try:
        response = judge_client.chat.completions.create(
            model=JUDGE_MODEL,
            max_completion_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content
        parsed, _ = parse_response(raw)
        return parsed
    except Exception as e:
        print(f"Judge error: {e}")
        return None


def run_judge(results: list[EvalResult], test_suite: list[TestCase], sample_rate: float = 0.25) -> pd.DataFrame:
    import random
    sampled = random.sample(results, k=max(1, int(len(results) * sample_rate)))
    rows = []
    print(f"Running LLM-as-judge on {len(sampled)} samples...")
    for r in tqdm(sampled):
        tc = next(t for t in test_suite if t.id == r.test_id)
        scores = judge_sample(tc, r)
        if scores:
            rows.append({
                "Model": r.model.split("/")[-1],
                "Test ID": r.test_id,
                "Dimension": r.dimension,
                "Run": r.run,
                **scores,
            })
        time.sleep(2)
    return pd.DataFrame(rows)


# ── Print Results ────────────────────────────────────────────

def print_results(metrics_df: pd.DataFrame, overall_df: pd.DataFrame):
    print("\n" + "="*70)
    print("OVERALL RANKING (Weighted F1)")
    print("="*70)
    print(tabulate(overall_df, headers="keys", tablefmt="rounded_outline", showindex=False))

    print("\n" + "="*70)
    print("METRICS BY DIMENSION")
    print("="*70)
    for dim in ["Security", "Correctness", "Readability", "Design",
                "Testability", "Performance", "Idiomatic"]:
        dim_df = metrics_df[metrics_df["Dimension"] == dim][
            ["Model", "Precision", "Recall", "F1", "FP Rate", "Avg Latency (ms)"]
        ]
        if not dim_df.empty:
            print(f"\n── {dim} ──")
            print(tabulate(dim_df, headers="keys", tablefmt="simple", showindex=False))


# ── Main ─────────────────────────────────────────────────────

def main():
    test_suite = load_test_cases()

    skills.init()
    catalog = skills.list_skills()
    print(f"Loaded {len(catalog)} skill pack(s): {[s['name'] for s in catalog]}")

    print(f"\nStarting eval: {len(MODELS)} models x {len(test_suite)} cases x {RUNS_PER_CASE} runs")
    print(f"Total API calls: {len(MODELS) * len(test_suite) * RUNS_PER_CASE}")
    print(f"Est. time: ~{len(MODELS) * len(test_suite) * RUNS_PER_CASE * DELAY_BETWEEN_CALLS / 60:.0f} min\n")

    results   = run_eval(MODELS, test_suite)
    aggregated = aggregate_runs(results, test_suite)
    metrics_df = compute_metrics(aggregated)
    overall_df = compute_overall(metrics_df)

    print_results(metrics_df, overall_df)

    judge_df = run_judge(results, test_suite, sample_rate=0.25)
    if not judge_df.empty:
        print("\n" + "="*70)
        print("LLM-AS-JUDGE SCORES (avg per model)")
        print("="*70)
        judge_summary = judge_df.groupby("Model")[
            ["accuracy", "explanation_quality", "fix_quality",
             "severity_accuracy", "junior_friendliness", "overall"]
        ].mean().round(2)
        print(tabulate(judge_summary, headers="keys", tablefmt="rounded_outline"))

    responses_df = pd.DataFrame([{
        "test_id":             r.test_id,
        "model":               r.model,
        "dimension":           r.dimension,
        "case_type":           r.case_type,
        "run":                 r.run,
        "has_issue_predicted": r.has_issue_predicted,
        "severity_predicted":  r.severity_predicted,
        "latency_ms":          round(r.latency_ms, 1),
        "parse_error":         r.parse_error,
        "raw_response":        r.raw_response,
    } for r in results])
    responses_df.to_csv(RESULTS_DIR / "responses.csv", index=False)

    metrics_df.to_csv(RESULTS_DIR / "eval_metrics.csv", index=False)
    overall_df.to_csv(RESULTS_DIR / "eval_overall.csv", index=False)
    if not judge_df.empty:
        judge_df.to_csv(RESULTS_DIR / "eval_judge.csv", index=False)

    print(f"\nSaved CSVs to {RESULTS_DIR}")
    return results, aggregated, metrics_df, overall_df


if __name__ == "__main__":
    main()
