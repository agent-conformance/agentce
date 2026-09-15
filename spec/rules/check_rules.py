#!/usr/bin/env python3
"""Validate the rule-format examples against the schemas of this directory (SPEC 7.1-7.2 support).

Checks that the worked control, metric, checklist, and probe examples validate against
control.schema.json, metric.schema.json, checklist.schema.json, and probe.schema.json, and that the
Appendix B shape is accepted by the Portable Shape Profile checker. The control schema references the
metric, probe, and checklist schemas, so all four are loaded into one resolver registry.

This is the local proof that the schemas of item 0.5 accept the specification's own examples; the
phase-0 evaluation exercises the same schemas through the repository-wide example validator. Prints
"RULES OK" on success. No network and no learned component.
"""

from __future__ import annotations

import json
from pathlib import Path

import psp_check
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE / "examples"
SCHEMAS = [
    "control.schema.json",
    "metric.schema.json",
    "probe.schema.json",
    "checklist.schema.json",
]

# example file -> schema that must accept it
CASES = [
    ("ovs-03.control.yaml", "control.schema.json"),
    ("automation-bias-latency.metric.yaml", "metric.schema.json"),
    ("prompt-injection-resistance.probe.yaml", "probe.schema.json"),
    ("trn-03-m.checklist.yaml", "checklist.schema.json"),
]


def _registry() -> Registry:
    resources = []
    for name in SCHEMAS:
        schema = json.loads((HERE / name).read_text(encoding="utf-8"))
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    registry = _registry()
    by_name = {
        name: json.loads((HERE / name).read_text(encoding="utf-8")) for name in SCHEMAS
    }

    for example, schema_name in CASES:
        instance = yaml.safe_load((EXAMPLES / example).read_text(encoding="utf-8"))
        validator = Draft202012Validator(by_name[schema_name], registry=registry)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            loc = "/".join(str(p) for p in first.path) or "<root>"
            fail(f"{example} does not satisfy {schema_name}: at {loc}: {first.message}")

    shape = EXAMPLES / "OVS-03.ttl"
    try:
        psp_check.check_file(shape)
    except psp_check.Refused as e:
        fail(f"{shape.name} is not within the Portable Shape Profile: {e.feature}")
    except ValueError as e:
        fail(f"{shape.name} does not parse: {e}")

    print("RULES OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
