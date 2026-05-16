from pathlib import Path
from .base import BaseRunner, Finding, SKIP_DIRS, parse_json, rel_path, run_cmd, resolve_tool


class DetectSecretsRunner(BaseRunner):
    name = "detect-secrets"
    binary = "detect-secrets"
    category = "secret"
    languages = {"*"}

    def run(self, root: Path) -> list[Finding]:
        tool = resolve_tool(self.binary)
        if not tool:
            return []
        exclude_re = "|".join(SKIP_DIRS)
        rc, out, err = run_cmd(
            [tool, "scan", "--all-files",
             "--exclude-files", exclude_re, str(root)],
            cwd=root,
        )
        data = parse_json(out) or {}
        findings: list[Finding] = []
        for filename, items in (data.get("results") or {}).items():
            for it in items:
                findings.append(Finding(
                    tool=self.name,
                    category=self.category,
                    severity="high",
                    file=rel_path(filename, root),
                    line=it.get("line_number"),
                    rule_id=it.get("type", "secret"),
                    message=f"Possible secret of type {it.get('type','?')}",
                    confidence=str(it.get("is_verified", False)).lower(),
                ))
        return findings
