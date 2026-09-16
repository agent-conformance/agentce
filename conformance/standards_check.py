"""The standards-alignment gate (SPEC §2.1, §7.3, P5.4).

Emits ``{"artifacts_valid": bool, "refs_flagged": bool}`` for the phase-5 eval's P5.4 check:

* ``artifacts_valid`` -- the OSCAL component definition validates against its profile schema, is a
  faithful (non-drifted) generation of the base catalog, and covers exactly the catalog's controls;
  and every obligation crosswalk beside the base catalog is structurally sound and names only real
  evidence event types and base-catalog controls.
* ``refs_flagged`` -- every crosswalk obligation carries ``verified_against_text: false``, so no
  clause mapping is presented as verified before a human with the source confirms it (SPEC §7.3).

Run it as::

    cd conformance && uv run python standards_check.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
CROSSWALK_DIR = CATALOG_DIR / "crosswalk"
REPORT_DIR = REPO_ROOT / "spec" / "report"
OSCAL_INSTANCE = REPORT_DIR / "oscal-component-definition.json"
OSCAL_SCHEMA = REPORT_DIR / "oscal-component-definition.schema.json"
CONTEXT = REPO_ROOT / "spec" / "model" / "generated" / "jsonld-context.json"

VALID_RUNGS = {0, 1, 2, 3, 4}

sys.path.insert(0, str(REPORT_DIR))
import build_oscal_component as oscal_builder  # type: ignore[import-not-found]  # noqa: E402


def _event_types() -> set[str]:
    """The evidence event-type names (the valid ``schema_elements`` values)."""
    context = json.loads(CONTEXT.read_text("utf-8")).get("@context", {})
    return {
        key
        for key, value in context.items()
        if isinstance(value, str) and value.startswith("agentce:") and key[:1].isupper()
    }


def _control_ids() -> set[str]:
    """The control ids of the base catalog."""
    ids: set[str] = set()
    for control_file in sorted((CATALOG_DIR / "controls").glob("*.yaml")):
        data = yaml.safe_load(control_file.read_text("utf-8")) or {}
        if "id" in data:
            ids.add(str(data["id"]))
    return ids


def _check_oscal(problems: list[str]) -> None:
    """Validate the OSCAL component definition; append any problems."""
    if not OSCAL_INSTANCE.exists():
        problems.append("oscal-component-definition.json is missing")
        return
    committed = OSCAL_INSTANCE.read_text("utf-8")
    document = json.loads(committed)
    schema = json.loads(OSCAL_SCHEMA.read_text("utf-8"))
    for error in sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda e: list(e.absolute_path),
    ):
        location = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"oscal: {location}: {error.message}")
    if committed != oscal_builder.render(oscal_builder.build()):
        problems.append("oscal: committed file differs from a fresh generation (drift)")
    requirements = document["component-definition"]["components"][0][
        "control-implementations"
    ][0]["implemented-requirements"]
    covered = {str(r["control-id"]) for r in requirements}
    catalog_ids = _control_ids()
    for unknown in sorted(covered - catalog_ids):
        problems.append(
            f"oscal: implemented requirement names non-catalog control {unknown}"
        )
    for missing in sorted(catalog_ids - covered):
        problems.append(f"oscal: control {missing} has no implemented requirement")


def _check_crosswalk(
    path: Path, event_types: set[str], control_ids: set[str]
) -> list[str]:
    """Validate one crosswalk file's structure and references; return its problems."""
    problems: list[str] = []
    name = path.name
    data = yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(data, dict):
        return [f"{name}: not a mapping"]
    if not isinstance(data.get("framework"), str) or not data["framework"].strip():
        problems.append(f"{name}: missing framework")
    if "version" not in data:
        problems.append(f"{name}: missing version")
    obligations = data.get("obligations")
    if not isinstance(obligations, list) or not obligations:
        problems.append(f"{name}: obligations must be a non-empty list")
        return problems
    for index, entry in enumerate(obligations):
        where = f"{name}: obligation[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: not a mapping")
            continue
        if not isinstance(entry.get("ref"), str) or not entry["ref"].strip():
            problems.append(f"{where}: missing ref")
        if (
            not isinstance(entry.get("obligation"), str)
            or not entry["obligation"].strip()
        ):
            problems.append(f"{where}: missing obligation statement")
        if not isinstance(entry.get("verified_against_text"), bool):
            problems.append(f"{where}: verified_against_text must be a boolean")
        for element in entry.get("schema_elements", []) or []:
            if element not in event_types:
                problems.append(f"{where}: unknown schema element {element}")
        for control in entry.get("controls", []) or []:
            if control not in control_ids:
                problems.append(f"{where}: unknown control {control}")
        for rung in entry.get("rungs", []) or []:
            if rung not in VALID_RUNGS:
                problems.append(f"{where}: invalid rung {rung}")
    return problems


def _all_flagged(path: Path) -> bool:
    """True when every obligation in ``path`` is marked unverified (``verified_against_text: false``)."""
    data = yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(data, dict):
        return False
    for entry in data.get("obligations", []) or []:
        if (
            not isinstance(entry, dict)
            or entry.get("verified_against_text") is not False
        ):
            return False
    return True


def run_standards_check() -> dict[str, Any]:
    """Validate every standards-alignment artifact and return the P5.4 result."""
    problems: list[str] = []
    _check_oscal(problems)

    crosswalks = sorted(CROSSWALK_DIR.glob("*.yaml"))
    if not crosswalks:
        problems.append("no crosswalk files found")
    event_types = _event_types()
    control_ids = _control_ids()
    flagged = True
    for path in crosswalks:
        problems.extend(_check_crosswalk(path, event_types, control_ids))
        flagged = flagged and _all_flagged(path)

    return {
        "artifacts_valid": not problems,
        "refs_flagged": flagged,
        "crosswalks": len(crosswalks),
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="standards_check", description="Standards-alignment artifact gate (P5.4)."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    result = run_standards_check()
    ok = bool(result["artifacts_valid"]) and bool(result["refs_flagged"])
    if args.json:
        print(
            json.dumps(
                {k: result[k] for k in ("artifacts_valid", "refs_flagged")},
                sort_keys=True,
            )
        )
    else:
        print(
            f"artifacts_valid={result['artifacts_valid']} refs_flagged={result['refs_flagged']} "
            f"crosswalks={result['crosswalks']}"
        )
        for problem in result["problems"]:
            print(f"  - {problem}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
