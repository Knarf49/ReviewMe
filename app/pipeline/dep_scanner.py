"""
Layer 0 — Supply Chain / dependency vulnerability scan.

Wraps OSV-Scanner (universal: npm, pip, cargo, go, maven, ...).
Gracefully skips if binary missing.

Usage:
    from dep_scanner import run_deps
    report = run_deps("path/to/repo")
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from runners.base import (
    Finding, SEVERITY_RANK, SKIP_DIRS,
    parse_json, rel_path, resolve_tool, run_cmd,
)


def _osv_severity(vuln: dict) -> str:
    """OSV severity comes either as numeric CVSS score or a label."""
    # `severity` array entries: {"type":"CVSS_V3","score":"CVSS:3.1/.../H/.../"}
    for s in vuln.get("severity") or []:
        score = s.get("score", "")
        # crude parse: trailing /H, /M, /L if present
        if "/H/" in score or score.endswith("/H"):
            return "high"
        if "/M/" in score or score.endswith("/M"):
            return "medium"
        if "/L/" in score or score.endswith("/L"):
            return "low"
    # database_specific.severity is sometimes a plain label
    ds = (vuln.get("database_specific") or {}).get("severity", "").upper()
    return {"CRITICAL": "critical", "HIGH": "high",
            "MEDIUM": "medium", "MODERATE": "medium",
            "LOW": "low"}.get(ds, "medium")


@dataclass
class DepReport:
    root: str
    tool_status: str = "not run"
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "tool_status": self.tool_status,
            "summary": self.summary(),
            "findings": [asdict(f) for f in self.findings],
        }

    def summary(self) -> dict:
        by_sev: dict[str, int] = {}
        by_pkg: dict[str, int] = {}
        for f in self.findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
            by_pkg[f.file] = by_pkg.get(f.file, 0) + 1
        return {
            "total_vulns": len(self.findings),
            "by_severity": by_sev,
            "affected_manifests": len(by_pkg),
        }


def run_deps(root: str | Path) -> DepReport:
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(root_path)
    report = DepReport(root=str(root_path))

    tool = resolve_tool("osv-scanner")
    if not tool:
        report.tool_status = "skipped (osv-scanner not on PATH)"
        return report

    excludes: list[str] = []
    for d in SKIP_DIRS:
        excludes.extend(["--skip-git", str(root_path / d)])  # osv-scanner doesn't
        # support --exclude; rely on filtering after parse instead

    rc, out, err = run_cmd(
        [tool, "--format", "json", "--recursive", str(root_path)],
        cwd=root_path,
        timeout=300,
    )
    # osv-scanner exits 1 when vulns found — that's normal
    data = parse_json(out)
    if data is None:
        err_tail = (err or "").strip().splitlines()[-1] if err else "no output"
        if "No package sources found" in (err or ""):
            report.tool_status = "no manifests (npm/pip/cargo/go/...) found"
        else:
            report.tool_status = f"error: {err_tail}"
        return report

    # OSV-Scanner JSON: {results: [{source:{path}, packages:[{package:{name,version}, vulnerabilities:[...]}]}]}
    for src in data.get("results", []):
        src_path = (src.get("source") or {}).get("path", "")
        if any(part in SKIP_DIRS for part in Path(src_path).parts):
            continue
        for pkg in src.get("packages", []):
            pkg_info = pkg.get("package") or {}
            pkg_name = pkg_info.get("name", "?")
            pkg_ver = pkg_info.get("version", "?")
            for vuln in pkg.get("vulnerabilities", []):
                report.findings.append(Finding(
                    tool="osv-scanner",
                    category="dep",
                    severity=_osv_severity(vuln),
                    file=rel_path(src_path, root_path),
                    line=None,
                    rule_id=vuln.get("id", ""),
                    message=f"{pkg_name}@{pkg_ver}: "
                            f"{(vuln.get('summary') or '').strip()[:200]}",
                ))
    report.findings.sort(
        key=lambda x: (-SEVERITY_RANK.get(x.severity, 0), x.file, x.rule_id)
    )
    report.tool_status = f"ok ({len(report.findings)} vulns)"
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    args = ap.parse_args()
    report = run_deps(args.path)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
