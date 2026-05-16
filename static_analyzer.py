"""
Layer 1 — Static Analysis orchestrator.

Iterates the runner registry in runners/__init__.py, filters by
detected languages, runs each tool, normalizes findings.

Usage:
    from static_analyzer import run_static
    report = run_static("path/to/repo")

CLI:
    python static_analyzer.py <path> [--lang python,js]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from runners import ALL_RUNNERS, Finding, SEVERITY_RANK

# Map file extensions → canonical language keys used by runners
EXT_TO_LANG = {
    ".py": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".scala": "scala",
    ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang",
    ".lua": "lua",
    ".dart": "dart",
    ".r": "r",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".sql": "sql",
    ".sol": "solidity",
    ".tf": "terraform",
    ".yml": "yaml", ".yaml": "yaml",
}

from runners.base import SKIP_DIRS


def detect_languages(root: Path) -> set[str]:
    langs: set[str] = set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        lang = EXT_TO_LANG.get(p.suffix.lower())
        if lang:
            langs.add(lang)
    return langs


@dataclass
class StaticReport:
    root: str
    languages: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    tool_status: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "languages": sorted(self.languages),
            "tool_status": self.tool_status,
            "summary": self.summary(),
            "findings": [asdict(f) for f in self.findings],
        }

    def summary(self) -> dict:
        by_sev: dict[str, int] = {}
        by_cat: dict[str, int] = {}
        by_tool: dict[str, int] = {}
        for f in self.findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
            by_cat[f.category] = by_cat.get(f.category, 0) + 1
            by_tool[f.tool] = by_tool.get(f.tool, 0) + 1
        return {
            "total": len(self.findings),
            "by_severity": by_sev,
            "by_category": by_cat,
            "by_tool": by_tool,
        }


def run_static(root: str | Path, languages: set[str] | None = None) -> StaticReport:
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(root_path)

    detected = languages if languages else detect_languages(root_path)
    report = StaticReport(root=str(root_path), languages=list(detected))

    for runner in ALL_RUNNERS:
        if not runner.matches(detected):
            report.tool_status[runner.name] = "skipped (lang mismatch)"
            continue
        if not runner.available():
            report.tool_status[runner.name] = "skipped (binary not found)"
            continue
        try:
            found = runner.run(root_path)
            report.findings.extend(found)
            report.tool_status[runner.name] = f"ok ({len(found)} findings)"
        except Exception as e:
            report.tool_status[runner.name] = f"error: {type(e).__name__}: {e}"

    report.findings.sort(
        key=lambda x: (-SEVERITY_RANK.get(x.severity, 0), x.file, x.line or 0)
    )
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--lang", help="comma-separated language hints (override auto-detect)")
    args = ap.parse_args()
    langs = set(args.lang.split(",")) if args.lang else None
    report = run_static(args.path, langs)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
