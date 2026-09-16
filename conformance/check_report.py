"""Phase-4 P4.4 orchestrator: the agentce-check-report skill's script tests and composite refusal.

Runs the skill's script tests in the skill's own environment and confirms that a composite-score
request is refused (DC-4). ``python check_report.py --json`` prints
``{"tests_pass": bool, "composite_refused": bool}`` — the shape the phase-4 eval's P4.4 check reads.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SKILL = _REPO / "skills" / "agentce-check-report"


def _tests_pass() -> bool:
    result = subprocess.run(
        ["uv", "run", "--project", str(_SKILL), "pytest", "-q"],
        cwd=_SKILL,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


@contextlib.contextmanager
def _empty_report() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report"
        report.mkdir()
        (report / "assertions.json").write_text("[]", encoding="utf-8")
        yield report


def _composite_refused() -> bool:
    with _empty_report() as report:
        result = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(_SKILL),
                "python",
                str(_SKILL / "scripts" / "read_outcomes.py"),
                "--report",
                str(report),
                "--score",
                "--json",
            ],
            cwd=_SKILL,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode == 0:
        return False
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    return bool(payload.get("status") == "refused")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase-4 P4.4 check-report skill gate."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)
    result = {"tests_pass": _tests_pass(), "composite_refused": _composite_refused()}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"tests_pass={result['tests_pass']} composite_refused={result['composite_refused']}"
        )
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
