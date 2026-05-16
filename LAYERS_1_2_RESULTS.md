# Layers 0 + 1 + 2 — Pipeline Status

**Status:** ✅ Working end-to-end (8 L1 runners + L0 OSV + L2 Python AST)
**Last update:** 2026-05-16
**Scope:** Python source (Layer 1/2) + universal deps (Layer 0). Multi-lang Layer 1 via Semgrep/Gitleaks. Per-lang Python runners + pluggable architecture for adding more languages.

---

## File layout

| File | Role |
|---|---|
| `runners/` | Pluggable runner package — one module per tool |
| `runners/base.py` | `BaseRunner` contract + `Finding` schema + helpers |
| `runners/__init__.py` | Registry — `ALL_RUNNERS` list iterated by orchestrator |
| `runners/bandit_runner.py` | Python security (B-codes) |
| `runners/ruff_runner.py` | Python quality + light security |
| `runners/detect_secrets_runner.py` | Universal secrets (Yelp, entropy + keywords) |
| `runners/semgrep_runner.py` | Universal SAST (20+ langs, `p/default` ruleset) |
| `runners/mypy_runner.py` | Python type errors |
| `runners/sqlfluff_runner.py` | SQL lint (auto-skips if no `.sql` files) |
| `runners/gitleaks_runner.py` | Universal secrets (Go binary, faster) |
| `runners/checkov_runner.py` | IaC scan (Terraform/K8s/CloudFormation/Docker) |
| `static_analyzer.py` | Layer 1 orchestrator — iterates registry, filters by detected langs |
| `ast_analyzer.py` | Layer 2 — stdlib `ast` + radon |
| `dep_scanner.py` | Layer 0 — OSV-Scanner wrapper |
| `analyze.py` | Top-level CLI: L0 + L1 + L2 → `results/analysis_<name>.json` |

---

## Architecture

```
                       +---------------------+
   directory  --->     |   analyze.py CLI    |
                       +---------+-----------+
                                 |
              +------------------+------------------+
              |                  |                  |
              v                  v                  v
       +-------------+   +---------------+   +-------------+
       |  Layer 0    |   |   Layer 1     |   |  Layer 2    |
       | dep_scanner |   | static_analy. |   | ast_analy.  |
       | (OSV)       |   |               |   | (stdlib ast |
       |             |   |               |   |  + radon)   |
       +------+------+   +-------+-------+   +------+------+
              |                  |                  |
              |          +-------+-------+          |
              |          |   runners/    |          |
              |          | bandit  ruff  |          |
              |          | detect-secrets|          |
              |          | semgrep  mypy |          |
              |          | sqlfluff      |          |
              |          | gitleaks      |          |
              |          | checkov       |          |
              |          +-------+-------+          |
              v                  v                  v
        dep vulns           findings[]      FileInfo/graph
                                 |
                          +------+------+
                          | JSON report |
                          +-------------+
```

**Rule (from CodeAnt pattern):** all 3 layers are deterministic — zero LLM calls. Output feeds Layer 3 (AI) later. Layer 3 must NOT override severities from L0/L1.

---

## Layer 0 — Supply Chain

| Tool | Covers | License |
|---|---|---|
| **OSV-Scanner** | npm, pip, cargo, go, maven, rubygems, packagist, conan, hex | Apache 2.0 |

Reads manifests (`requirements.txt`, `package-lock.json`, `Cargo.lock`, `go.mod`, ...) and queries the OSV database. Outputs `Finding`s with `category=dep`, `rule_id=GHSA-*`.

Severity = parsed from CVSS string or `database_specific.severity`.

Status when no manifests present: graceful — reports `"no manifests found"` rather than error.

---

## Layer 1 — Static (8 runners)

| Runner | Category | Languages | Tool license |
|---|---|---|---|
| bandit | security | python | Apache 2.0 |
| ruff | quality | python | MIT |
| detect-secrets | secret | * | Apache 2.0 |
| **semgrep** | security | * (20+ langs) | LGPL-2.1 + Semgrep Rules License — CE free for commercial |
| **mypy** | type | python | MIT |
| **sqlfluff** | sql | sql | MIT |
| **gitleaks** | secret | * | MIT |
| **checkov** | iac | * (TF/K8s/Docker) | Apache 2.0 |

### Runner contract
```python
class BaseRunner:
    name: str               # display name
    binary: str             # CLI to look up
    category: str           # security|quality|secret|type|iac|sql|dep
    languages: set[str]     # {"python"}, {"sql"}, {"*"}

    def available(self) -> bool: ...   # tool installed?
    def matches(self, langs) -> bool: ...  # lang filter
    def run(self, root) -> list[Finding]: ...
```

### Adding a new tool
1. Drop `runners/<name>_runner.py` subclassing `BaseRunner`
2. Append instance to `ALL_RUNNERS` in `runners/__init__.py`
3. Done — orchestrator auto-detects + skips if binary missing

### Severity normalization
Each runner maps tool-native severity → `critical/high/medium/low/info`. See per-runner `_SEV` dicts.

### Language detection
`detect_languages(root)` walks files, maps suffix → canonical lang key, returns set. Runners with `languages={"*"}` always match; lang-specific runners auto-skip if their lang not present.

---

## Layer 2 — AST / Semantic

Unchanged from prior MVP. Python-only:
- `stdlib ast` for parse → `FunctionInfo`, `ClassInfo`, imports, main guard
- `radon` for cyclomatic complexity + raw LOC
- Derived: module graph, entry points, complexity hotspots (CC ≥ 10), heuristic unused functions

Add other languages via `tree-sitter` later (separate file: `ast_analyzer_ts.py`, etc.) — registry pattern recommended.

---

## Self-test (latest run on ReviewMe + fixture)

### `python analyze.py .` (ReviewMe)
```
Layer 1 — Static (langs: ['python', 'sql'])
  bandit         ok (11)    ruff           ok (249)
  detect-secrets ok (3)     semgrep        ok (2)
  mypy           ok (27)    sqlfluff       ok (47)
  gitleaks       ok (3)     checkov        ok (0)
  Total: 342  high=17 medium=37 low=288

Layer 2 — AST
  23 files, 3617 LOC, 95 fns, 21 classes
  Hotspots: detect cc=31, compute_metrics cc=27, analyze_file cc=18
```

### `python analyze.py tests/fixture_project`
```
Layer 0 — Supply Chain  (ok, 13 vulns)
  3 critical, 1 high, 9 medium  ← from pyyaml@5.1 + requests@2.19.0

Layer 1 — Static (langs: ['python'])
  bandit 4   ruff 5   detect-secrets 3   semgrep 0   mypy 0
  sqlfluff skipped (lang mismatch)   gitleaks 1   checkov 0

Layer 2 — AST
  2 files, 54 LOC, 6 fns
```

Cross-tool agreement on secrets (bandit B105 + ruff S105 + detect-secrets + gitleaks all flag `main.py:6 PASSWORD = "..."`) — exactly the confidence signal Layer 3 will use.

---

## How to run

```powershell
# install once (Python tools)
.venv\Scripts\pip install bandit ruff radon detect-secrets semgrep mypy sqlfluff checkov

# Go binaries (need Go in PATH or download from GitHub releases)
go install github.com/zricethezav/gitleaks/v8@latest
go install github.com/google/osv-scanner/cmd/osv-scanner@v1

# full pipeline
.venv\Scripts\python analyze.py <path>

# skip Layer 0
.venv\Scripts\python analyze.py <path> --no-deps

# override language detection
.venv\Scripts\python static_analyzer.py <path> --lang python,sql
```

Programmatic:
```python
from dep_scanner    import run_deps
from static_analyzer import run_static
from ast_analyzer    import run_ast

l0 = run_deps("path").to_dict()
l1 = run_static("path").to_dict()
l2 = run_ast("path").to_dict()
```

---

## Gotchas hit + fixed

| Issue | Fix |
|---|---|
| `detect-secrets scan` defaults to git-tracked files only | Added `--all-files` |
| `semgrep` skips files under `tests/` by default `.semgrepignore` | Documented; users must move fixtures or run on explicit paths |
| `semgrep` honors `.gitignore` by default | Added `--no-git-ignore` |
| `checkov.cmd` shim on Windows resolves wrong python | Bypass — call `Checkov().run()` via venv python inline |
| `gitleaks` has no `--exclude` flag, scans `.venv` (300+ false positives) | Generate temp TOML config with `[allowlist] paths=[...]` |
| OSV-Scanner errors loudly when no manifests present | Detect "No package sources found" → status="no manifests found" |
| `results/` directory contains JSON with hashed_secret strings → gitleaks false positives | Added `results/` to `SKIP_DIRS` |

---

## Tools NOT yet wrapped (per `Rule src.md`)

Future Layer 1 expansion candidates. Picked by license + cross-platform installability:

### Easy wins (binary install)
- **CodeQL** — heavy, GitHub Action only sensible deployment
- **PMD** — Java JAR, Java-family lint
- **eslint + biome** — JS/TS, npm install per project
- **gosec + staticcheck** — Go binaries via `go install`
- **Clippy + cargo-audit + cargo-deny** — Rust, `rustup component add`
- **detekt + ktlint** — Kotlin JARs
- **RuboCop + Brakeman** — Ruby gems
- **PHPStan + Psalm** — PHP composer
- **SwiftLint** — Swift, macOS-only
- **Solhint + Mythril** — Solidity, npm/pip

### Per-lang Layer 2 (structure)
- **tree-sitter** — universal AST, replace per-lang stdlib parsers
- **JavaParser** — Java AST
- **libclang** — C/C++ AST
- **dart_code_metrics** — already gives Layer-2-shape output

### Skip (license risk)
TruffleHog, Slither, ShellCheck, Hadolint, Cppcheck, NodeJsScan, Pylint, Flawfinder, golangci-lint (GPL/AGPL).

---

## What Layer 3 (AI) will use

The combined JSON output is the Layer 3 input contract:
- **L0 findings** → AI explains *why* outdated deps matter for the target job (e.g. "Django 2.2 has 16 CVEs — recruiter will reject")
- **L1 findings** → AI translates each into junior-friendly teaching tone (no re-judging severity)
- **L1 cross-tool agreement** → confidence boost for "definitely a problem"
- **L2 module graph + hotspots** → AI judges architecture fit vs. job description
- **L2 entry points + unused** → AI infers project shape (CLI / web / library)

AI never overrides deterministic verdicts. It contextualizes.
