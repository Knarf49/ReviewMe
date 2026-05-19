from pathlib import Path
from .base import BaseRunner, Finding, parse_json, rel_path, run_cmd, resolve_tool


class SqlfluffRunner(BaseRunner):
    name = "sqlfluff"
    binary = "sqlfluff"
    category = "sql"
    languages = {"sql"}

    def run(self, root: Path) -> list[Finding]:
        tool = resolve_tool(self.binary)
        if not tool:
            return []
        # only run if there's at least one .sql file
        if not any(root.rglob("*.sql")):
            return []
        rc, out, err = run_cmd(
            [tool, "lint", "--format", "json",
             "--dialect", "ansi", str(root)],
            cwd=root,
            timeout=120,
        )
        data = parse_json(out) or []
        findings: list[Finding] = []
        for file_entry in data:
            fp = file_entry.get("filepath", "")
            for v in file_entry.get("violations", []):
                findings.append(Finding(
                    tool=self.name,
                    category="sql",
                    severity="low",
                    file=rel_path(fp, root),
                    line=v.get("line_no") or v.get("start_line_no"),
                    rule_id=v.get("code", ""),
                    message=v.get("description", ""),
                ))
        return findings
