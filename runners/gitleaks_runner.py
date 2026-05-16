import tempfile
from pathlib import Path
from .base import BaseRunner, Finding, SKIP_DIRS, parse_json, rel_path, run_cmd, resolve_tool


def _write_config(skip_dirs: set[str]) -> str:
    """Gitleaks has no --exclude flag; supply a config with allowlist.paths.
    `useDefault = true` keeps all built-in rules; we only add path exclusions.
    """
    paths = ",\n".join(f"  '''{d}/'''" for d in sorted(skip_dirs))
    toml = (
        "[extend]\nuseDefault = true\n\n"
        "[allowlist]\npaths = [\n" + paths + "\n]\n"
    )
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".toml", delete=False, encoding="utf-8"
    )
    f.write(toml)
    f.close()
    return f.name


class GitleaksRunner(BaseRunner):
    name = "gitleaks"
    binary = "gitleaks"
    category = "secret"
    languages = {"*"}

    def run(self, root: Path) -> list[Finding]:
        tool = resolve_tool(self.binary)
        if not tool:
            return []
        config_path = _write_config(SKIP_DIRS)
        report_fd, report_path = tempfile.mkstemp(suffix=".json")
        Path(report_path).write_text("")  # gitleaks overwrites; ensure exists
        import os as _os
        _os.close(report_fd)
        try:
            rc, out, err = run_cmd(
                [tool, "detect", "--source", str(root),
                 "--no-git", "--no-banner",
                 "--config", config_path,
                 "-r", report_path, "-f", "json"],
                cwd=root,
                timeout=300,
            )
            try:
                data = parse_json(Path(report_path).read_text(encoding="utf-8")) or []
            except FileNotFoundError:
                data = []
        finally:
            Path(report_path).unlink(missing_ok=True)
            Path(config_path).unlink(missing_ok=True)
        findings: list[Finding] = []
        for r in data:
            findings.append(Finding(
                tool=self.name,
                category=self.category,
                severity="high",
                file=rel_path(r.get("File", ""), root),
                line=r.get("StartLine"),
                rule_id=r.get("RuleID", "secret"),
                message=r.get("Description", "Secret detected"),
            ))
        return findings
