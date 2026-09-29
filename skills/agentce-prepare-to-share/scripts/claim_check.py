"""claim_check — cross-check a claim against the profile before signing (SPEC §13.3.4).

Wraps the engine's ``agentce.readiness.claim_check``: the claim's subjects, observation window, and
catalogs must match what was actually assessed. It never sets ``approved_by`` or signs anything (S-8).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from _common import FINDINGS, INPUT_ERROR, OK, arg_value, emit, run_guarded

from agentce import readiness


def _load(path_arg: str | None) -> dict:
    if not path_arg:
        return {}
    text = Path(path_arg).read_text("utf-8")
    data = (
        yaml.safe_load(text)
        if path_arg.endswith((".yaml", ".yml"))
        else json.loads(text)
    )
    return data if isinstance(data, dict) else {}


def body(argv: list[str]) -> int:
    want_json = "--json" in argv
    claim_arg, profile_arg = arg_value(argv, "--claim"), arg_value(argv, "--profile")
    if not claim_arg or not profile_arg:
        emit(
            {
                "status": "input_error",
                "message": "pass --claim <file> --profile <file>",
            },
            want_json=want_json,
            human="INPUT ERROR: pass --claim <file> --profile <file>",
        )
        return INPUT_ERROR
    problems = readiness.claim_check(
        _load(claim_arg), _load(arg_value(argv, "--manifest")), _load(profile_arg)
    )
    emit(
        {"problems": problems, "clean": not problems},
        want_json=want_json,
        human="CLAIM OK" if not problems else "\n".join(problems),
    )
    return OK if not problems else FINDINGS


if __name__ == "__main__":
    raise SystemExit(run_guarded(sys.argv[1:], body))
