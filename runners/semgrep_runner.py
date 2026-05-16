from pathlib import Path
from .base import BaseRunner, Finding, SKIP_DIRS, parse_json, rel_path, run_cmd, resolve_tool

_SEV = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}


class SemgrepRunner(BaseRunner):
    """Universal SAST. Uses the free `p/default` Semgrep ruleset."""
    name = "semgrep"
    binary = "semgrep"
    category = "security"
    languages = {"*"}

    def run(self, root: Path) -> list[Finding]:
        tool = resolve_tool(self.binary)
        if not tool:
            return []
        excludes: list[str] = []
        for d in SKIP_DIRS:
            excludes.extend(["--exclude", d])
        rc, out, err = run_cmd(
            [tool, "scan", "--config", "p/default",
             "--json", "--quiet", "--disable-version-check",
             "--metrics=off", "--no-git-ignore",
             *excludes, str(root)],
            cwd=root,
            timeout=300,
        )
        data = parse_json(out) or {}
        findings: list[Finding] = []
        for r in data.get("results", []):
            extra = r.get("extra") or {}
            sev_raw = (extra.get("severity") or "INFO").upper()
            check_id = r.get("check_id", "")
            findings.append(Finding(
                tool=self.name,
                category="security",
                severity=_SEV.get(sev_raw, "low"),
                file=rel_path(r.get("path", ""), root),
                line=(r.get("start") or {}).get("line"),
                rule_id=check_id,
                message=extra.get("message", "").strip().splitlines()[0]
                        if extra.get("message") else "",
            ))
        return findings
