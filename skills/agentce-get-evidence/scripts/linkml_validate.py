"""linkml_validate (SPEC 13.3.3 step A.5): validate artifacts against the LinkML-derived schemas.

Validates evidence events (``--events <file.jsonl>``) against the generated evidence JSON Schema, or an
applicability profile (``--profile <file>``) against the generated applicability-profile schema -- the
same schemas the engine generates from the LinkML model, read from the installed engine so the skill
never drifts from it. Deterministic, offline, model-free (rule S-4). Exit 0 when valid, 1 on findings,
2 on input error, 3 on version mismatch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from _common import FINDINGS, INPUT_ERROR, OK, Finding, arg_value, emit, run_guarded


def _validate_events(path: Path) -> list[Finding]:
    from agentce.schema import validate_event

    findings: list[Finding] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            findings.append(
                Finding(
                    "linkml.invalid_json", f"line {lineno}: {exc.msg}", {"line": lineno}
                )
            )
            continue
        errors = validate_event(event)
        if errors:
            findings.append(
                Finding(
                    "linkml.schema_invalid",
                    f"line {lineno}: {errors[0]}",
                    {"line": lineno, "errors": errors},
                )
            )
    return findings


def _validate_profile(path: Path) -> list[Finding]:
    import jsonschema
    from agentce.schema import evidence_schema  # noqa: F401  (ensures the engine is importable)
    from importlib import resources

    schema_text = (
        resources.files("agentce.data.schemas")
        .joinpath("applicability-profile.schema.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    validator = jsonschema.validators.validator_for(schema)(schema)
    return [
        Finding(
            "linkml.profile_invalid", err.message, {"path": list(err.absolute_path)}
        )
        for err in sorted(validator.iter_errors(profile), key=str)
    ]


def _body(argv: list[str]) -> int:
    want_json = "--json" in argv
    events = arg_value(argv, "--events")
    profile = arg_value(argv, "--profile")
    if bool(events) == bool(profile):
        emit(
            {
                "status": "input_error",
                "message": "pass exactly one of --events or --profile",
            },
            want_json=want_json,
            human="INPUT ERROR: pass exactly one of --events <file.jsonl> or --profile <file>",
        )
        return INPUT_ERROR
    target = Path(events or profile or "")
    if not target.is_file():
        emit(
            {"status": "input_error", "message": f"{target} is not a file"},
            want_json=want_json,
            human=f"INPUT ERROR: {target} is not a file",
        )
        return INPUT_ERROR
    findings = _validate_events(target) if events else _validate_profile(target)
    payload = {
        "target": str(target),
        "valid": not findings,
        "findings": [f.to_json() for f in findings],
    }
    emit(
        payload,
        want_json=want_json,
        human="LINKML VALIDATE OK"
        if not findings
        else f"LINKML VALIDATE: {len(findings)} finding(s)",
    )
    return OK if not findings else FINDINGS


def main(argv: list[str] | None = None) -> int:
    return run_guarded(list(sys.argv[1:] if argv is None else argv), _body)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
