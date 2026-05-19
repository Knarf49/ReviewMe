from pathlib import Path
from .base import BaseRunner, Finding, SKIP_DIRS, parse_json, rel_path, run_cmd, resolve_tool

_SEV = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


class BanditRunner(BaseRunner):
    name = "bandit"
    binary = "bandit"
    category = "security"
    languages = {"python"}

    def run(self, root: Path) -> list[Finding]:
        tool = resolve_tool(self.binary)
        if not tool:
            return []
        rc, out, err = run_cmd(
            [tool, "-r", str(root), "-f", "json", "-q",
             "--exclude", ",".join(SKIP_DIRS)],
            cwd=root,
        )
        data = parse_json(out) or {}
        findings: list[Finding] = []
        for r in data.get("results", []):
            findings.append(Finding(
                tool=self.name,
                category=self.category,
                severity=_SEV.get(r.get("issue_severity", "LOW"), "low"),
                file=rel_path(r.get("filename", ""), root),
                line=r.get("line_number"),
                rule_id=r.get("test_id", ""),
                message=r.get("issue_text", ""),
                confidence=(r.get("issue_confidence") or "").lower() or None,
            ))
        return findings
