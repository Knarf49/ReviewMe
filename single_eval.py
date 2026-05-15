import json
from openai import OpenAI

import os
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

If the code is clean and has no significant issues, set has_issue to false and all other fields to null."""

CODE = """
func TestGetLastN(t *testing.T) {
    tests := []struct {
        items    []string
        n        int
        expected []string
    }{
        {[]string{"a", "b", "c"}, 2, []string{"b", "c"}},
        {[]string{"a"}, 1, []string{"a"}},
        {[]string{"a", "b"}, 5, []string{"a", "b"}},
        {[]string{}, 1, []string{}},
    }
    for _, tt := range tests {
        result := getLastN(tt.items, tt.n)
        if !reflect.DeepEqual(result, tt.expected) {
            t.Errorf("got %v, want %v", result, tt.expected)
        }
    }
}
"""

USER_PROMPT = f"""Review this go code snippet:

```go
{CODE.strip()}
```

Focus on dimension: Idiomatic
Identify any issues or confirm the code is clean."""

# ── Call model ───────────────────────────────────────────────
response = client.chat.completions.create(
    model="gpt-5.4-mini",
    temperature=0,
    max_completion_tokens=800,
    response_format={"type": "json_object"},
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": USER_PROMPT},
    ],
)

print("── Usage ─────────────────────────────────────")
print(f"Input tokens:  {response.usage.prompt_tokens}")
print(f"Output tokens: {response.usage.completion_tokens}")
print(f"Total tokens:  {response.usage.total_tokens}")
print(f"Finish reason: {response.choices[0].finish_reason}")

raw = response.choices[0].message.content
print("── Raw response ──────────────────────────────")
print(raw)

# ── Parse ────────────────────────────────────────────────────
try:
    clean = raw.strip()
    if "<think>" in clean:
        clean = clean.split("</think>")[-1].strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.split("\n")[1:])
    if clean.endswith("```"):
        clean = "\n".join(clean.split("\n")[:-1])
    parsed = json.loads(clean.strip())
    print("\n── Parsed ────────────────────────────────────")
    print(json.dumps(parsed, indent=2))
except Exception as e:
    print(f"\n── Parse error: {e}")