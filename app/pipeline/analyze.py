"""
Layer 0 + 1 + 2 pipeline.

Runs:
  - Layer 0 (dep_scanner)  — supply chain via OSV-Scanner
  - Layer 1 (static_analyzer) — pluggable runners (bandit, ruff,
    semgrep, mypy, sqlfluff, gitleaks, detect-secrets, checkov, ...)
  - Layer 2 (ast_analyzer) — Python AST + radon metrics

Writes results/analysis_<name>.json. Prints summary.

Usage:
    python analyze.py <path> [--out results/] [--no-deps]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from static_analyzer import run_static
from ast_analyzer import run_ast
from dep_scanner import run_deps


def analyze(root: str | Path, include_deps: bool = True) -> dict:
    static = run_static(root)
    ast_rep = run_ast(root)
    out = {
        "root": str(Path(root).resolve()),
        "layer1_static": static.to_dict(),
        "layer2_ast": ast_rep.to_dict(),
    }
    if include_deps:
        out["layer0_deps"] = run_deps(root).to_dict()
    return out


def print_summary(report: dict) -> None:
    root = Path(report["root"]).name
    print(f"\n=== Analysis: {root} ===")

    if "layer0_deps" in report:
        d0 = report["layer0_deps"]
        print(f"\n  Layer 0 — Supply Chain ({d0['tool_status']})")
        if d0["findings"]:
            print(f"    Vulns: {d0['summary']['total_vulns']}  "
                  f"by sev: {d0['summary']['by_severity']}")

    s1 = report["layer1_static"]
    print(f"\n  Layer 1 — Static (langs: {s1['languages']})")
    for tool, status in s1["tool_status"].items():
        print(f"    {tool:<20} {status}")
    print(f"    Total: {s1['summary']['total']}  "
          f"by sev: {s1['summary']['by_severity']}  "
          f"by cat: {s1['summary']['by_category']}")

    s2 = report["layer2_ast"]
    print(f"\n  Layer 2 — AST (Python)")
    print(f"    Files: {s2['file_count']}  LOC: {s2['summary']['total_loc']}  "
          f"Fns: {s2['summary']['total_functions']}  Classes: {s2['summary']['total_classes']}")
    print(f"    Avg CC: {s2['summary']['avg_cyclomatic']}  "
          f"Max CC: {s2['summary']['max_cyclomatic']}")
    if s2["complexity_hotspots"]:
        print("    Top hotspots:")
        for fn in s2["complexity_hotspots"][:3]:
            print(f"      {fn['qualname']:<35} cc={fn['complexity']:<3} "
                  f"{fn['body_lines']} lines")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--out", default="results")
    ap.add_argument("--no-deps", action="store_true",
                    help="skip Layer 0 (dep scan)")
    ap.add_argument("--with-ai", action="store_true",
                    help="run Layer 3 (AI context review) after Layers 0-2")
    ap.add_argument("--jd", default="",
                    help="path to job description text file (enables Call B)")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"path not found: {root}", file=sys.stderr)
        sys.exit(2)

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    report = analyze(root, include_deps=not args.no_deps)

    out_file = out_dir / f"analysis_{root.name}.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_summary(report)
    print(f"\nWrote {out_file}")

    if args.with_ai:
        from ai_reviewer import run_layer3_sync
        jd = ""
        if args.jd:
            jd_path = Path(args.jd).resolve()
            if not jd_path.exists():
                print(f"JD not found: {jd_path}", file=sys.stderr)
                sys.exit(2)
            jd = jd_path.read_text(encoding="utf-8")

        print("\n  Layer 3 — AI Context Review (running…)")
        layer3 = run_layer3_sync(report, jd)
        report["layer3_ai"] = layer3.to_dict()
        l3_file = out_dir / f"layer3_{root.name}.json"
        l3_file.write_text(json.dumps(layer3.to_dict(), indent=2), encoding="utf-8")

        calls = ["A"] + (["B"] if layer3.call_b is not None else []) + ["C"]
        explanations = (layer3.call_c.get("explanations") or []) if isinstance(layer3.call_c, dict) else []
        print(f"    Model: {layer3.model}  Elapsed: {layer3.elapsed_ms} ms")
        print(f"    Calls: {'/'.join(calls)}  Explanations: {len(explanations)}  "
              f"Dropped bogus finding_ids: {len(layer3.dropped_finding_ids)}")
        print(f"\nWrote {l3_file}")


if __name__ == "__main__":
    main()
