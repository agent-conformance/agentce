"""Validate the AgentCE OSCAL component definition (SPEC §2.1, §7.3, item 5.5).

Checks that ``oscal-component-definition.json``:

1. validates against the bounded profile schema ``oscal-component-definition.schema.json``;
2. is byte-identical to a fresh generation from the base catalog (no hand-edit, no drift);
3. names only real base-catalog controls, one implemented requirement per control; and
4. carries unique, well-formed identifiers.

Prints ``OSCAL OK`` and exits 0 on success; prints ``OSCAL FAILED: <reason>`` and exits 1 otherwise.
Run in place::

    cd spec/report && uv run python validate_oscal.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_oscal_component as builder  # noqa: E402

HERE = Path(__file__).resolve().parent
INSTANCE = HERE / "oscal-component-definition.json"
SCHEMA = HERE / "oscal-component-definition.schema.json"


def _fail(message: str) -> int:
    print(f"OSCAL FAILED: {message}")
    return 1


def validate() -> int:
    if not INSTANCE.exists():
        return _fail(f"{INSTANCE.name} is missing; run build_oscal_component.py")

    committed = INSTANCE.read_text("utf-8")
    document = json.loads(committed)

    # (1) Schema conformance.
    schema = json.loads(SCHEMA.read_text("utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda e: list(e.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(p) for p in first.absolute_path) or "<root>"
        return _fail(f"schema: {location}: {first.message}")

    # (2) No drift: the committed file equals a fresh generation from the catalog.
    if committed != builder.render(builder.build()):
        return _fail("committed file differs from a fresh generation; run build_oscal_component.py")

    # (3) One implemented requirement per real base-catalog control.
    _, titles = builder._catalog()
    catalog_ids = set(titles)
    requirements = document["component-definition"]["components"][0][
        "control-implementations"
    ][0]["implemented-requirements"]
    covered = [r["control-id"] for r in requirements]
    unknown = sorted(set(covered) - catalog_ids)
    if unknown:
        return _fail(f"implemented requirements name non-catalog controls: {', '.join(unknown)}")
    missing = sorted(catalog_ids - set(covered))
    if missing:
        return _fail(f"controls without an implemented requirement: {', '.join(missing)}")
    if len(covered) != len(set(covered)):
        return _fail("duplicate control-id among implemented requirements")

    # (4) Identifiers are unique across the document.
    uuids = _all_uuids(document)
    if len(uuids) != len(set(uuids)):
        return _fail("duplicate uuid in the document")

    print("OSCAL OK")
    return 0


def _all_uuids(value: object) -> list[str]:
    """Collect every ``uuid`` string value anywhere in the document."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "uuid" and isinstance(item, str):
                found.append(item)
            else:
                found.extend(_all_uuids(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_all_uuids(item))
    return found


if __name__ == "__main__":
    raise SystemExit(validate())
