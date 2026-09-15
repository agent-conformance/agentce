#!/usr/bin/env python3
"""Validate the specification's worked examples against the published schemas (eval P0.6).

The specification carries a worked example for each machine-readable artifact; a schema that does not
accept its own example is wrong. This driver loads every schema under spec/ into one resolver registry
(so the control schema's references to the metric, probe, and checklist schemas resolve) and validates
each example against its schema. It covers the P0.6 set -- applicability profile, control, metric,
claim, manifest -- and the remaining secondary-input and report schemas of item 0.7.

Prints "EXAMPLES OK" on success; on failure prints the first error per example and exits 1. No network
and no learned component.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SPEC = Path(__file__).resolve().parent

# Every schema loaded into the registry by its $id, so cross-file references resolve.
SCHEMAS = [
    SPEC / "rules" / "control.schema.json",
    SPEC / "rules" / "metric.schema.json",
    SPEC / "rules" / "probe.schema.json",
    SPEC / "rules" / "checklist.schema.json",
    SPEC / "model" / "generated" / "json-schema" / "applicability-profile.schema.json",
    SPEC / "report" / "claim.schema.json",
    SPEC / "report" / "assertions.schema.json",
    SPEC / "report" / "manifest.schema.json",
    SPEC / "report" / "deviation-register.schema.json",
    SPEC / "report" / "applicability-statement.schema.json",
    SPEC / "report" / "quarantine.schema.json",
    SPEC / "report" / "integrity-result.schema.json",
]

# (schema file, example file); the example must validate against the schema.
CASES = [
    (
        "model/generated/json-schema/applicability-profile.schema.json",
        "model/examples/applicability-profile.example.yaml",
    ),
    ("rules/control.schema.json", "rules/examples/ovs-03.control.yaml"),
    ("rules/metric.schema.json", "rules/examples/automation-bias-latency.metric.yaml"),
    ("rules/checklist.schema.json", "rules/examples/trn-03-m.checklist.yaml"),
    ("report/claim.schema.json", "report/examples/claim.example.json"),
    ("report/assertions.schema.json", "report/examples/assertions.example.json"),
    ("report/manifest.schema.json", "report/examples/manifest.example.json"),
    (
        "report/deviation-register.schema.json",
        "report/examples/deviation-register.example.yaml",
    ),
    (
        "report/applicability-statement.schema.json",
        "report/examples/applicability-statement.example.json",
    ),
    ("report/quarantine.schema.json", "report/examples/quarantine.example.json"),
    (
        "report/integrity-result.schema.json",
        "report/examples/integrity-result.example.json",
    ),
]


def _load(path: Path) -> object:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    schema_by_id: dict[str, dict] = {}
    resources = []
    for path in SCHEMAS:
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema_by_id[schema["$id"]] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    registry = Registry().with_resources(resources)

    for schema_rel, example_rel in CASES:
        schema = json.loads((SPEC / schema_rel).read_text(encoding="utf-8"))
        instance = _load(SPEC / example_rel)
        validator = Draft202012Validator(schema, registry=registry)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            loc = "/".join(str(p) for p in first.path) or "<root>"
            fail(
                f"{example_rel} does not satisfy {schema_rel}: at {loc}: {first.message}"
            )

    print(f"EXAMPLES OK ({len(CASES)} examples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
