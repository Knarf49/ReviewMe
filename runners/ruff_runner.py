from pathlib import Path
from .base import BaseRunner, Finding, SKIP_DIRS, parse_json, rel_path, run_cmd, resolve_tool

_SEV_BY_PREFIX = {
    "S": "high", "B": "medium", "F": "medium",
    "E": "low", "W": "low", "C": "low", "N": "low",
    "UP": "low", "SIM": "low", "RUF": "low",
}


def _severity(code: str) -> str:
    for prefix, sev in sorted(_SEV_BY_PREFIX.items(), key=lambda x: -len(x[0])):
        if code.startswith(prefix):
            return sev
    return "low"


class RuffRunner(BaseRunner):
    name = "ruff"
    binary = "ruff"
    category = "quality"
    languages = {"python"}

    def run(self, root: Path) -> list[Finding]:
        tool = resolve_tool(self.binary)
        if not tool:
            return []
        rc, out, err = run_cmd(
            [tool, "check", "--output-format", "json",
             "--select", "E,F,W,B,S,SIM,C90,N,UP,RUF",
             "--exclude", ",".join(SKIP_DIRS),
             str(root)],
            cwd=root,
        )
        data = parse_json(out) or []
        findings: list[Finding] = []
        for r in data:
            code = r.get("code") or ""
            findings.append(Finding(
                tool=self.name,
                category="security" if code.startswith("S") else "quality",
                severity=_severity(code),
                file=rel_path(r.get("filename", ""), root),
                line=(r.get("location") or {}).get("row"),
                rule_id=code,
                message=r.get("message", ""),
            ))
        return findings
