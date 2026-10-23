"""Validate an applicability profile against the vendored schema (SPEC §6.5, §13.4).

The adopter's ``agentce init`` writes a starter profile; this tool checks a profile file against the
engine's vendored copy of ``applicability-profile.schema.json`` (kept byte-identical to the generated
spec artifact by a sync test). It is the engine counterpart of ``spec/validate_profile.py`` and backs
the adopter-experience check.

    python -m agentce.tools.validate_profile <profile.yaml>

Exits 0 with ``PROFILE OK`` when the profile validates, 1 with the first error otherwise, and 2 on a
usage error. No network, no learned component.
"""

from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from .. import signing
from ..errors import InputError
from ..safe_json import read_bundled
from ..safe_yaml import load_untrusted_yaml


def _schema() -> dict[str, Any]:
    text = (
        resources.files("agentce.data.schemas")
        .joinpath("applicability-profile.schema.json")
        .read_bytes()
    )
    parsed: dict[str, Any] = read_bundled(text)
    return parsed


def validate_profile(profile: Any) -> list[str]:
    """Return the profile's schema-validation error messages (empty when it is valid)."""
    schema = _schema()
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    return [
        f"at {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(profile), key=lambda e: list(e.path))
    ]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(
            "usage: python -m agentce.tools.validate_profile <profile.yaml>",
            file=sys.stderr,
        )
        return 2
    path = Path(argv[0])
    try:
        if not signing.is_regular_file(path):
            print(f"PROFILE INVALID: {path} is not a file", file=sys.stderr)
            return 1
        # Read as the engine reads it: under the SPEC 8.7 YAML profile (18.184u).
        instance = load_untrusted_yaml(
            path, key="input.profile_invalid", what="the applicability profile"
        )
    except PermissionError:  # the same answer on every Python, no traceback (18.169)
        print(f"PROFILE INVALID: {path} could not be read", file=sys.stderr)
        return 1
    except InputError as exc:
        print(f"PROFILE INVALID: {exc.cause}", file=sys.stderr)
        return 1
    errors = validate_profile(instance)
    if errors:
        print(f"PROFILE INVALID: {errors[0]}", file=sys.stderr)
        return 1
    print("PROFILE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
