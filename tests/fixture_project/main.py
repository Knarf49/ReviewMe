"""Fixture: intentional issues for Layer 1+2 validation."""

import os
import subprocess  # noqa: F401  (intentionally unused for ruff F401)

PASSWORD = "hunter2supersecret_abc123!"  # secret keyword

API_TOKEN = "sk-proj-abcdef1234567890abcdef1234567890abcdefXYZ987654321QWERTYUIOP"  # high entropy


def run_user_cmd(cmd):
    # Bandit: subprocess with shell=True
    return os.system(cmd)  # noqa: B007


def deeply_nested(items):
    # High cyclomatic complexity
    total = 0
    for i in items:
        if i > 0:
            if i < 100:
                if i % 2 == 0:
                    if i % 3 == 0:
                        total += i
                    else:
                        total -= i
                else:
                    total += 1
            else:
                total += 2
        else:
            if i < -100:
                total -= 10
            else:
                total -= 1
    return total


def never_called():
    return 42


def helper():
    return deeply_nested([1, 2, 3])


if __name__ == "__main__":
    print(helper())
