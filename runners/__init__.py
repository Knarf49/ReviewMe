"""Runner registry. Each module exports one Runner subclass.

Add new tools by writing a runner that subclasses BaseRunner
and appending its instance to ALL_RUNNERS.
"""

from .base import BaseRunner, Finding, SEVERITY_RANK
from .bandit_runner import BanditRunner
from .ruff_runner import RuffRunner
from .detect_secrets_runner import DetectSecretsRunner
from .semgrep_runner import SemgrepRunner
from .mypy_runner import MypyRunner
from .sqlfluff_runner import SqlfluffRunner
from .gitleaks_runner import GitleaksRunner
from .checkov_runner import CheckovRunner

ALL_RUNNERS: list[BaseRunner] = [
    BanditRunner(),
    RuffRunner(),
    DetectSecretsRunner(),
    SemgrepRunner(),
    MypyRunner(),
    SqlfluffRunner(),
    GitleaksRunner(),
    CheckovRunner(),
]

__all__ = ["BaseRunner", "Finding", "SEVERITY_RANK", "ALL_RUNNERS"]
