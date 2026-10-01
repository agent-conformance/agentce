"""Keyword census for `report --validate`'s 12 schemas (item 18.27, verifier round 2's circuit breaker).

Verifier round 1 and round 2 each FAILed on cross-engine divergences the C3 parity check's mutation
list could not see (`harness/remediation/verdicts/P18-18.27-verifier.md`,
`harness/remediation/verdicts/P18-18.27-verifier-r2.md`, private SPECS repo): each round patched the
exact shapes the verifier happened to construct, then extended the mutation list to match -- the
shared premise `principle-attack-the-premise` names as the actual defect. This script replaces
"whatever shapes the verifier happened to try" with the real, complete inventory: every JSON Schema
keyword actually used across the 10 local-profile schemas plus the 2 real third-party standards, with
its schema file and JSON pointer, so the next fix can be checked against every keyword the validated
report artifacts actually use -- not a guess at which ones matter.

Usage: `uv run --project tools python tools/report_validate_keyword_census.py`. Prints one section per
keyword, each occurrence's schema file and pointer (and, for `format`, the declared format name).
Exit 0 always (a census, not a check); `--self-test` asserts the walker finds known keywords at known
locations.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCAL_SCHEMAS = [
    "assertions",
    "manifest",
    "activity",
    "blind-spots",
    "oscal-assessment-results",
    "results-sarif",
    "project",
    "security",
    "auditor",
    "buyer",
]
VENDOR_SCHEMAS = [
    (
        "oscal-assessment-results-nist-1.1.2",
        ROOT / "spec/report/vendor/oscal-assessment-results-nist-1.1.2.schema.json",
    ),
    ("sarif-2.1.0", ROOT / "spec/report/vendor/sarif-2.1.0.schema.json"),
]

#: Keys whose value is itself a (sub)schema or a list/map of (sub)schemas -- walked recursively.
#: Keys not in this set (`type`, `format`, `pattern`, `enum`, ...) are leaves: recorded, not descended into.
SCHEMA_VALUED = {
    "properties": "map",
    "patternProperties": "map",
    "$defs": "map",
    "definitions": "map",
    "items": "schema",
    "additionalProperties": "schema-or-bool",
    "additionalItems": "schema-or-bool",
    "contains": "schema",
    "not": "schema",
    "if": "schema",
    "then": "schema",
    "else": "schema",
    "allOf": "list",
    "anyOf": "list",
    "oneOf": "list",
    "prefixItems": "list",
}

#: Leaf keywords worth a census row (the keyword itself is the discriminator, not a schema container).
LEAF_KEYWORDS = {
    "type",
    "format",
    "pattern",
    "enum",
    "const",
    "$ref",
    "required",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "dependentRequired",
    "propertyNames",
}


def walk(
    node: Any,
    pointer: str,
    schema_file: str,
    out: dict[str, list[tuple[str, str, str]]],
) -> None:
    if isinstance(node, list):
        for i, item in enumerate(node):
            walk(item, f"{pointer}/{i}", schema_file, out)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        child_pointer = f"{pointer}/{key}"
        if key in LEAF_KEYWORDS:
            extra = (
                value
                if key in ("format", "$ref", "type") and isinstance(value, str)
                else ""
            )
            out[key].append((schema_file, pointer, str(extra)))
        if key in ("oneOf", "anyOf", "allOf", "not", "if") and isinstance(
            value, (list, dict)
        ):
            branches = len(value) if isinstance(value, list) else 1
            out[key].append((schema_file, pointer, f"{branches} branch(es)"))
        kind = SCHEMA_VALUED.get(key)
        if kind == "map" and isinstance(value, dict):
            for sub_key, sub_schema in value.items():
                walk(sub_schema, f"{child_pointer}/{sub_key}", schema_file, out)
        elif kind == "schema" and isinstance(value, dict):
            walk(value, child_pointer, schema_file, out)
        elif kind == "schema-or-bool" and isinstance(value, dict):
            walk(value, child_pointer, schema_file, out)
        elif kind == "list" and isinstance(value, list):
            for i, sub_schema in enumerate(value):
                walk(sub_schema, f"{child_pointer}/{i}", schema_file, out)
        elif key == "properties" and isinstance(value, dict):
            pass  # handled by the "map" branch above; kept for clarity, never reached twice
        # Always also recurse into any dict/list value under an unrecognized key (defensive: a
        # schema this census doesn't know how to interpret is walked anyway, not silently skipped).
        elif isinstance(value, (dict, list)) and key not in SCHEMA_VALUED:
            walk(value, child_pointer, schema_file, out)


def census() -> dict[str, list[tuple[str, str, str]]]:
    out: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for name in LOCAL_SCHEMAS:
        path = ROOT / "spec" / "report" / f"{name}.schema.json"
        walk(json.loads(path.read_text()), "", name, out)
    for name, path in VENDOR_SCHEMAS:
        walk(json.loads(path.read_text()), "", name, out)
    return out


def render(out: dict[str, list[tuple[str, str, str]]]) -> str:
    lines = []
    for keyword in sorted(out):
        occurrences = out[keyword]
        lines.append(f"## {keyword} ({len(occurrences)} occurrence(s))")
        for schema_file, pointer, extra in occurrences:
            suffix = f" = {extra}" if extra else ""
            lines.append(f"  {schema_file}{pointer or '/'}{suffix}")
    return "\n".join(lines)


def self_test() -> None:
    out = census()
    assert "format" in out, (
        "expected at least one `format` occurrence across the 12 schemas"
    )
    assert any(f == "date-time" for _, _, f in out["format"]), (
        "expected a date-time format occurrence"
    )
    assert "oneOf" in out or "anyOf" in out, (
        "expected at least one combinator in the 12 schemas"
    )
    assert any(
        s == "oscal-assessment-results-nist-1.1.2"
        for s, _, _ in out.get("anyOf", []) + out.get("oneOf", [])
    ), "expected a combinator in the vendored NIST OSCAL schema (round 2's D1/D2)"
    print(
        f"report_validate_keyword_census self-test: {len(out)} distinct keywords discriminate"
    )


def main() -> int:
    if "--self-test" in sys.argv:
        self_test()
        return 0
    print(render(census()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
