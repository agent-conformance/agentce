"""Coverage and reconciliation with independent denominators (SPEC §6.5, §8.3 rung 0).

Every subject must declare at least one coverage denominator whose source is independent of both the
agent and the enforcement point (a provider usage export, an egress/firewall log, a system-of-record
export, or an admission attestation). For each subject and event type the engine compares the events
it captured (``observed``) against the independent denominator's count (``expected``) using integer
arithmetic, and reports the ratio and a status: ``covered``, ``below_threshold`` (a capture gap), or
``unknown`` when no independent denominator covers the type. ``unknown`` coverage is what later makes
INT and REC controls ``insufficient_evidence`` for that subject; a source's own counters are never
used as its own denominator.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from .bundle import confine_to_root, safe_is_file
from .profile import CoverageDenominator, Profile

STATUS_COVERED = "covered"
STATUS_BELOW = "below_threshold"
STATUS_UNKNOWN = "unknown"


def _event_type(event: dict[str, Any]) -> str:
    data = event.get("data")
    if isinstance(data, dict) and isinstance(data.get("@type"), str):
        return str(data["@type"])
    return "Unknown"


def _ratio(observed: int, expected: int) -> str | None:
    if expected <= 0:
        return None
    value = (Decimal(observed) / Decimal(expected)).quantize(
        Decimal("0.0001"), ROUND_HALF_EVEN
    )
    return str(value)


def _denominator_counts(
    denominator: CoverageDenominator,
    by_source_type: dict[tuple[str, str], int],
    bundle_root: Path | None,
) -> dict[str, int]:
    if denominator.manifest and bundle_root is not None:
        path = confine_to_root(bundle_root, denominator.manifest)
        if path is not None and safe_is_file(path):
            data = json.loads(path.read_text(encoding="utf-8"))
            declared = data.get("counts") or data.get("expected") or {}
            if isinstance(declared, dict):
                return {str(k): int(v) for k, v in declared.items()}
    # Otherwise count the denominator source's own ingested events, per type.
    counts: dict[str, int] = {}
    for (source, event_type), count in by_source_type.items():
        if source == denominator.source:
            counts[event_type] = counts.get(event_type, 0) + count
    return counts


def compute_coverage(
    events: list[dict[str, Any]],
    profile: Profile,
    bundle_root: Path | None = None,
) -> dict[str, Any]:
    """Return the coverage.json structure for ``events`` under ``profile``."""
    by_source_type: dict[tuple[str, str], int] = defaultdict(int)
    for event in events:
        by_source_type[(str(event.get("source", "")), _event_type(event))] += 1

    subjects_out: dict[str, Any] = {}
    for subject in profile.subjects:
        denominator_ids = subject.denominator_ids
        observed: dict[str, int] = defaultdict(int)
        types: set[str] = set()
        for event in events:
            if str(event.get("subject", "")) != subject.id:
                continue
            if str(event.get("source", "")) in denominator_ids:
                continue  # a denominator's own events are the yardstick, not captured operations
            event_type = _event_type(event)
            observed[event_type] += 1
            types.add(event_type)

        expected: dict[str, dict[str, Any]] = {}
        for denominator in subject.coverage_denominators:
            counts = _denominator_counts(denominator, by_source_type, bundle_root)
            covered = denominator.covers or list(counts)
            for event_type in covered:
                if event_type not in counts:
                    continue
                types.add(event_type)
                entry = expected.setdefault(
                    event_type, {"count": -1, "denominators": []}
                )
                if counts[event_type] > entry["count"]:
                    entry["count"] = counts[event_type]
                    entry["denominators"] = [denominator.source]
                elif counts[event_type] == entry["count"]:
                    entry["denominators"].append(denominator.source)

        by_type: dict[str, Any] = {}
        for event_type in sorted(types):
            obs = observed.get(event_type, 0)
            exp_entry = expected.get(event_type)
            if exp_entry is None:
                by_type[event_type] = {
                    "observed": obs,
                    "expected": None,
                    "ratio": None,
                    "status": STATUS_UNKNOWN,
                    "denominators": [],
                }
            else:
                exp = int(exp_entry["count"])
                by_type[event_type] = {
                    "observed": obs,
                    "expected": exp,
                    "ratio": _ratio(obs, exp),
                    "status": STATUS_COVERED if obs >= exp else STATUS_BELOW,
                    "denominators": sorted(exp_entry["denominators"]),
                }

        subjects_out[subject.id] = {
            "event_types": by_type,
            "coverage_status": _rollup(subject.coverage_denominators, by_type),
            "has_independent_denominator": bool(subject.coverage_denominators),
        }

    return {"subjects": subjects_out}


def _rollup(denominators: list[CoverageDenominator], by_type: dict[str, Any]) -> str:
    if not denominators:
        return STATUS_UNKNOWN
    statuses = {entry["status"] for entry in by_type.values()}
    if STATUS_UNKNOWN in statuses:
        return STATUS_UNKNOWN
    if STATUS_BELOW in statuses:
        return "gap"
    return "ok"
