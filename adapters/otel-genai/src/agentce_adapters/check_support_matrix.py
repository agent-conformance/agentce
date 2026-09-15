"""Prove the support matrix stays consistent with what the adapter emits (SPEC 12.3).

Run as ``python -m agentce_adapters.check_support_matrix <adapter-dir>`` (the item-2.1 acceptance uses
``.``). The check runs the adapter over every fixture and holds ``support-matrix.yaml`` to the
evidence the fixtures actually demonstrate:

* the matrix ``adapter`` name matches the adapter;
* the event types the matrix declares are exactly those the fixtures emit -- no type is claimed
  without a fixture, and no emitted type is undocumented;
* for each event type, the declared ``members`` are exactly the members the adapter populates across
  the fixtures -- neither over- nor under-claimed;
* nothing a type lists under ``missing`` is ever populated; and
* the declared ``conventions`` are exactly those the fixtures exercise.

The matrix is what the engine reads to explain ``insufficient_evidence`` outcomes, so an inconsistent
matrix is a defect. Prints ``SUPPORT MATRIX OK`` on success. Standard library plus PyYAML; no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .fixtures import discover_fixtures
from .members import members_of

ADAPTER_NAME = "otel-genai"


def _load_matrix(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} is not a YAML mapping")
    return parsed


def _observed(adapter_root: Path) -> tuple[dict[str, set[str]], set[str], list[str]]:
    """Return (members per event type, conventions, ordering problems) observed across fixtures."""
    members: dict[str, set[str]] = {}
    conventions: set[str] = set()
    problems: list[str] = []
    for fixture in discover_fixtures(adapter_root / "fixtures"):
        result = fixture.run()
        conventions.update(result.report.conventions)
        canonical = [json.dumps(e, sort_keys=True) for e in result.events]
        expected = [json.dumps(e, sort_keys=True) for e in fixture.expected]
        if canonical != expected:
            problems.append(
                f"fixture {fixture.name}: adapter output does not match expected.jsonl"
            )
        for event in result.events:
            event_type = event["data"]["@type"]
            members.setdefault(event_type, set()).update(members_of(event))
    return members, conventions, problems


def check(adapter_root: Path) -> list[str]:
    """Return a list of inconsistency messages; an empty list means the matrix is consistent."""
    matrix = _load_matrix(adapter_root / "support-matrix.yaml")
    failures: list[str] = []

    if matrix.get("adapter") != ADAPTER_NAME:
        failures.append(
            f"support matrix adapter is {matrix.get('adapter')!r}, expected {ADAPTER_NAME!r}"
        )

    observed_members, observed_conventions, problems = _observed(adapter_root)
    failures.extend(problems)

    declared_conventions = set(matrix.get("conventions") or [])
    if declared_conventions != observed_conventions:
        failures.append(
            f"conventions mismatch: matrix {sorted(declared_conventions)} "
            f"!= observed {sorted(observed_conventions)}"
        )

    events = matrix.get("events")
    declared_events = set(events) if isinstance(events, dict) else set()
    if declared_events != set(observed_members):
        failures.append(
            f"event types mismatch: matrix {sorted(declared_events)} "
            f"!= observed {sorted(observed_members)}"
        )

    if isinstance(events, dict):
        for event_type, spec in events.items():
            observed = observed_members.get(event_type, set())
            declared_members = set((spec or {}).get("members") or [])
            declared_missing = set((spec or {}).get("missing") or [])
            if declared_members != observed:
                failures.append(
                    f"{event_type}.members mismatch: matrix {sorted(declared_members)} "
                    f"!= observed {sorted(observed)}"
                )
            overlap = declared_missing & observed
            if overlap:
                failures.append(
                    f"{event_type}.missing lists populated members {sorted(overlap)}"
                )
    return failures


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    adapter_root = Path(args[0]) if args else Path(".")
    try:
        failures = check(adapter_root)
    except (OSError, ValueError) as exc:
        print(f"SUPPORT MATRIX FAILED: {exc}")
        return 1
    if failures:
        for failure in failures:
            print(f"SUPPORT MATRIX FAILED: {failure}")
        return 1
    print("SUPPORT MATRIX OK")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
