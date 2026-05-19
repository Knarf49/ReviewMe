import json
import sys
from pathlib import Path
from .base import BaseRunner, Finding, parse_json, rel_path, run_cmd

_SEV = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low"}

# Tiny in-process wrapper — the shipped checkov.cmd resolves the wrong python
# on Windows ("ModuleNotFoundError: No module named 'checkov'"). Calling
# Checkov().run() in this venv's interpreter bypasses the broken shim.
_WRAPPER = """
import json, sys
from checkov.main import Checkov
sys.argv = ['checkov', '--quiet', '-o', 'json', '-d', sys.argv[1]]
sys.exit(Checkov().run())
"""


class CheckovRunner(BaseRunner):
    name = "checkov"
    binary = "checkov"
    category = "iac"
    languages = {"*"}

    def available(self) -> bool:
        try:
            import checkov  # noqa: F401
            return True
        except ImportError:
            return False

    def run(self, root: Path) -> list[Finding]:
        if not self.available():
            return []
        rc, out, err = run_cmd(
            [sys.executable, "-c", _WRAPPER, str(root)],
            cwd=root,
            timeout=180,
        )
        data = parse_json(out)
        if data is None:
            return []
        # checkov returns either a dict (single framework) or list of dicts
        blocks = data if isinstance(data, list) else [data]
        findings: list[Finding] = []
        for block in blocks:
            results = (block.get("results") or {}) if isinstance(block, dict) else {}
            for check in results.get("failed_checks") or []:
                sev_raw = (check.get("severity") or "MEDIUM").upper()
                file_path = check.get("file_path") or ""
                line_range = check.get("file_line_range") or [None]
                findings.append(Finding(
                    tool=self.name,
                    category=self.category,
                    severity=_SEV.get(sev_raw, "medium"),
                    file=rel_path(file_path, root),
                    line=line_range[0] if line_range else None,
                    rule_id=check.get("check_id", ""),
                    message=check.get("check_name", ""),
                ))
        return findings
