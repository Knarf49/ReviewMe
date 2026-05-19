"""Shared runner contract + helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

SKIP_DIRS = {
    ".venv", "venv", "env", "__pycache__", ".git",
    "node_modules", "dist", "build", "out", ".next",
    "target", "vendor", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".semgrep_cache",
    "skills_cache", "results",
}


@dataclass
class Finding:
    tool: str
    category: str          # security | quality | secret | type | iac | sql | dep
    severity: str          # critical | high | medium | low | info
    file: str
    line: int | None
    rule_id: str
    message: str
    confidence: str | None = None


class BaseRunner:
    """Override class attrs + run() in subclasses."""
    name: str = ""
    binary: str = ""           # name to look up on PATH / venv Scripts
    category: str = "quality"
    languages: set[str] = {"*"}  # use {"*"} for universal

    def available(self) -> bool:
        return resolve_tool(self.binary) is not None

    def matches(self, project_languages: set[str] | None) -> bool:
        if not project_languages or "*" in self.languages:
            return True
        return bool(self.languages & project_languages)

    def run(self, root: Path) -> list[Finding]:  # pragma: no cover - abstract
        raise NotImplementedError


# ── Helpers ──────────────────────────────────────────────────

def resolve_tool(name: str) -> str | None:
    """Find a CLI on PATH or inside this venv's Scripts dir."""
    if not name:
        return None
    p = shutil.which(name)
    if p:
        return p
    venv_scripts = Path(sys.executable).parent
    for ext in ("", ".exe", ".cmd", ".bat"):
        cand = venv_scripts / f"{name}{ext}"
        if cand.exists():
            return str(cand)
    # also check ~/go/bin for Go tools
    go_bin = Path.home() / "go" / "bin"
    for ext in ("", ".exe"):
        cand = go_bin / f"{name}{ext}"
        if cand.exists():
            return str(cand)
    return None


def run_cmd(cmd: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def rel_path(path_str: str, root: Path) -> str:
    if not path_str:
        return ""
    try:
        return str(Path(path_str).resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return path_str.replace("\\", "/")


def parse_json(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
