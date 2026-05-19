"""ReviewMe local CLI — thin wrapper around the pipeline package.

Pipeline modules under `app/pipeline/` are import-only. This script exposes
their entry points to humans for local debugging.

Usage:
    python -m scripts.cli analyze <path> [--out results/] [--no-deps] [--with-ai] [--jd path/to/jd.txt]
    python -m scripts.cli layer3 <analysis.json> [--jd path/to/jd.txt] [--out layer3.json]
    python -m scripts.cli layer4 [--jd-file path/to/jd.txt | --jd-text "..."] [--out layer4.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.pipeline.analyze import analyze, print_summary


def _cmd_analyze(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"path not found: {root}", file=sys.stderr)
        return 2

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    report = analyze(root, include_deps=not args.no_deps)

    out_file = out_dir / f"analysis_{root.name}.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_summary(report)
    print(f"\nWrote {out_file}")

    if args.with_ai:
        from app.pipeline.ai_reviewer import run_layer3_sync

        jd = ""
        if args.jd:
            jd_path = Path(args.jd).resolve()
            if not jd_path.exists():
                print(f"JD not found: {jd_path}", file=sys.stderr)
                return 2
            jd = jd_path.read_text(encoding="utf-8")

        print("\n  Layer 3 — AI Context Review (running…)")
        layer3 = run_layer3_sync(report, jd)
        report["layer3_ai"] = layer3.to_dict()
        l3_file = out_dir / f"layer3_{root.name}.json"
        l3_file.write_text(json.dumps(layer3.to_dict(), indent=2), encoding="utf-8")

        calls = ["A"] + (["B"] if layer3.call_b is not None else []) + ["C"]
        explanations = (
            (layer3.call_c.get("explanations") or [])
            if isinstance(layer3.call_c, dict)
            else []
        )
        print(f"    Model: {layer3.model}  Elapsed: {layer3.elapsed_ms} ms")
        print(
            f"    Calls: {'/'.join(calls)}  Explanations: {len(explanations)}  "
            f"Dropped bogus finding_ids: {len(layer3.dropped_finding_ids)}"
        )
        print(f"\nWrote {l3_file}")
    return 0


def _cmd_layer3(args: argparse.Namespace) -> int:
    from app.pipeline.ai_reviewer import run_layer3_sync

    analysis_path = Path(args.analysis_json).resolve()
    if not analysis_path.exists():
        print(f"not found: {analysis_path}", file=sys.stderr)
        return 2
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

    jd = ""
    if args.jd:
        jd_path = Path(args.jd).resolve()
        if not jd_path.exists():
            print(f"JD not found: {jd_path}", file=sys.stderr)
            return 2
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
    return 0


def _cmd_layer4(args: argparse.Namespace) -> int:
    from app.pipeline.project_suggester import run_layer4_sync

    if args.jd_file:
        jd_path = Path(args.jd_file).resolve()
        if not jd_path.exists():
            print(f"JD not found: {jd_path}", file=sys.stderr)
            return 2
        jd = jd_path.read_text(encoding="utf-8")
    elif args.jd_text:
        jd = args.jd_text
    else:
        print("provide --jd-file or --jd-text", file=sys.stderr)
        return 2

    result = run_layer4_sync(jd)
    out_json = json.dumps(result.to_dict(), indent=2)

    if args.out:
        Path(args.out).write_text(out_json, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(out_json)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ReviewMe local CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="Run L0+L1+L2 on a path")
    a.add_argument("path")
    a.add_argument("--out", default="results")
    a.add_argument("--no-deps", action="store_true", help="skip Layer 0 dep scan")
    a.add_argument("--with-ai", action="store_true", help="also run Layer 3")
    a.add_argument("--jd", default="", help="path to JD text file (enables Call B)")
    a.set_defaults(func=_cmd_analyze)

    l3 = sub.add_parser("layer3", help="Run Layer 3 on an existing analysis.json")
    l3.add_argument("analysis_json")
    l3.add_argument("--jd", default="")
    l3.add_argument("--out", default="")
    l3.set_defaults(func=_cmd_layer3)

    l4 = sub.add_parser("layer4", help="Generate a project suggestion from a JD")
    l4.add_argument("--jd-file", default="", help="path to JD text file")
    l4.add_argument("--jd-text", default="", help="JD text inline")
    l4.add_argument("--out", default="")
    l4.set_defaults(func=_cmd_layer4)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
