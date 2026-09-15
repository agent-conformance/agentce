#!/usr/bin/env python3
"""Validate an applicability profile against the published schema (SPEC §6.5, item 1.15).

The adopter's `agentce init` writes a starter applicability profile; this driver checks a profile file
against `model/generated/json-schema/applicability-profile.schema.json`, the schema generated from the
LinkML model. It is the spec-level counterpart of the engine's `agentce.tools.validate_profile`, used
by the adopter-experience acceptance.

    uv run python validate_profile.py <profile.yaml>

Prints "PROFILE OK" and exits 0 when the profile validates; prints the first error and exits 1
otherwise. No network and no learned component.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

SPEC = Path(__file__).resolve().parent
SCHEMA = (
    SPEC / "model" / "generated" / "json-schema" / "applicability-profile.schema.json"
)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: validate_profile.py <profile.yaml>", file=sys.stderr)
        return 2
    profile_path = Path(argv[0])
    if not profile_path.is_file():
        print(f"PROFILE INVALID: {profile_path} is not a file", file=sys.stderr)
        return 1
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    try:
        instance = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"PROFILE INVALID: not well-formed YAML: {exc}", file=sys.stderr)
        return 1
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "<root>"
        print(f"PROFILE INVALID: at {loc}: {first.message}", file=sys.stderr)
        return 1
    print("PROFILE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
