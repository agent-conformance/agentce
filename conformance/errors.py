"""Phase-4 P4.8 orchestrator: configuration and error UX (SPEC §13.4).

``python errors.py --json`` prints ``{"doctor_match", "errors_check", "no_stack_trace"}``:
``agentce doctor`` gives the right verdict on a healthy and a broken project; the message-key
catalogue has a cause and a fix for every key; and a failing run emits no stack trace without
``--debug``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engines" / "python"
sys.path.insert(0, str(_ENGINE))

from agentce.error_catalogue import catalogue_gaps  # noqa: E402


def _agentce(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "agentce", *args],
        cwd=_ENGINE,
        capture_output=True,
        text=True,
        check=False,
    )


def _doctor_match() -> bool:
    healthy = _agentce(
        "doctor", "--project", str(_REPO / "corpus" / "quickstart"), "--json"
    )
    if healthy.returncode != 0 or not json.loads(healthy.stdout).get("healthy"):
        return False
    with (
        tempfile.TemporaryDirectory() as tmp
    ):  # an empty project: no profile, no bundle, no domain
        broken = _agentce("doctor", "--project", tmp, "--json")
        if broken.returncode == 0:
            return False
        keys = {p["key"] for p in json.loads(broken.stdout).get("problems", [])}
    return "input.profile_missing" in keys


def _no_stack_trace() -> bool:
    run = _agentce(
        "assess",
        "--bundle",
        str(_REPO / "no-such-bundle"),
        "--catalog",
        "eu-ai-act@2026.09",
        "--profile",
        str(_REPO / "no-such-profile.yaml"),
        "--domain",
        str(_REPO / "no-such-domain.yaml"),
    )
    output = run.stdout + run.stderr
    return run.returncode != 0 and "Traceback (most recent call last)" not in output


def evaluate() -> dict[str, bool]:
    return {
        "doctor_match": _doctor_match(),
        "errors_check": not catalogue_gaps(),
        "no_stack_trace": _no_stack_trace(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase-4 P4.8 configuration/errors gate."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.parse_args(argv)
    result = evaluate()
    print(json.dumps(result, sort_keys=True))
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
