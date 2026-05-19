import re
from pathlib import Path
from .base import BaseRunner, Finding, SKIP_DIRS, rel_path, run_cmd, resolve_tool

# mypy text line:  path:line: severity: message  [rule]
_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:\d+:)?\s*"
    r"(?P<sev>error|warning|note):\s*(?P<msg>.+?)(?:\s+\[(?P<code>[^\]]+)\])?$"
)


class MypyRunner(BaseRunner):
    name = "mypy"
    binary = "mypy"
    category = "type"
    languages = {"python"}

    def run(self, root: Path) -> list[Finding]:
        tool = resolve_tool(self.binary)
        if not tool:
            return []
        # ignore-missing-imports keeps noise low when stubs not installed
        excludes: list[str] = []
        for d in SKIP_DIRS:
            excludes.extend(["--exclude", d])
        rc, out, err = run_cmd(
            [tool, "--ignore-missing-imports", "--no-error-summary",
             "--show-error-codes", "--no-color-output", "--no-pretty",
             "--hide-error-context", "--no-incremental",
             *excludes, str(root)],
            cwd=root,
            timeout=180,
        )
        findings: list[Finding] = []
        for line in (out or "").splitlines():
            m = _LINE_RE.match(line.strip())
            if not m:
                continue
            sev_raw = m.group("sev")
            if sev_raw == "note":
                continue
            findings.append(Finding(
                tool=self.name,
                category=self.category,
                severity="medium" if sev_raw == "error" else "low",
                file=rel_path(m.group("file"), root),
                line=int(m.group("line")),
                rule_id=m.group("code") or "type-error",
                message=m.group("msg").strip(),
            ))
        return findings
