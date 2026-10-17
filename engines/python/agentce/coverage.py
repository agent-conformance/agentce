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

from . import signing
from .bundle import confine_to_root, safe_is_file
from .errors import InputError
from .profile import CoverageDenominator, Profile, Subject

STATUS_COVERED = "covered"
STATUS_BELOW = "below_threshold"
STATUS_UNKNOWN = "unknown"


def event_type_of(event: dict[str, Any]) -> str:
    """The type a coverage count keys a record by: its data.@type, else ``Unknown`` (also used by the corpus
    generator and the gate fixtures, so their denominators count as the engine does)."""
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


def _independent(denominator: CoverageDenominator, subject: Subject) -> bool:
    """A denominator counts only when it is independent (SPEC §6.5): never one of the subject's own declared
    evidence sources, and never an admission attestation, whose signature and policy digest no engine verifies."""
    return (
        denominator.kind != "admission_attestation"
        and denominator.source not in subject.source_ids
    )


def _denominator_counts(
    denominator: CoverageDenominator,
    subject: Subject,
    by_source_type: dict[tuple[str, str], int],
    bundle_root: Path | None,
    listed: frozenset[str],
) -> dict[str, int]:
    """The denominator's counts for the types it covers (every type it counts when ``covers`` is empty): both its
    yardstick and its expected counts (SPEC §6.5)."""
    counts = _declared_counts(denominator, subject, by_source_type, bundle_root, listed)
    return {
        t: n
        for t, n in counts.items()
        if not denominator.covers or t in denominator.covers
    }


def _declared_counts(
    denominator: CoverageDenominator,
    subject: Subject,
    by_source_type: dict[tuple[str, str], int],
    bundle_root: Path | None,
    listed: frozenset[str],
) -> dict[str, int]:
    if not _independent(denominator, subject):
        return {}
    if denominator.manifest:
        if denominator.manifest not in listed:
            # A file the bundle manifest does not list is outside the bundle digest: never read (SPEC §6.6).
            return {}
        if bundle_root is not None:
            path = confine_to_root(bundle_root, denominator.manifest)
            if path is not None and safe_is_file(path):
                raw = path.read_bytes()
                # Counted on the bytes, not left to json's recursion limit, which differs by Python.
                if signing._too_deep(raw):
                    raise InputError(
                        "input.coverage_denominator_manifest_invalid",
                        f"the coverage denominator manifest at {str(path)!r} is nested too deeply "
                        "to parse safely.",
                        "flatten the denominator manifest's structure; it exceeds the engine's safe "
                        "nesting depth.",
                    )
                data = json.loads(raw.decode("utf-8"))
                # A root that is not an object declares no counts: fall through, as TypeScript and Java do.
                if isinstance(data, dict):
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
    *,
    listed: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Return the coverage.json structure for ``events`` under ``profile``. ``listed`` is the bundle manifest's file
    paths: a denominator manifest is read only when listed there."""
    by_source_type: dict[tuple[str, str], int] = defaultdict(int)
    for event in events:
        by_source_type[(str(event.get("source", "")), event_type_of(event))] += 1

    subjects_out: dict[str, Any] = {}
    for subject in profile.subjects:
        counts_by_denominator = [
            (d, _denominator_counts(d, subject, by_source_type, bundle_root, listed))
            for d in subject.coverage_denominators
        ]
        # A denominator's own records of a type it covers and counts are the yardstick, not captured operations. One
        # that is not independent, or does not count a type, hides none of that type's events (SPEC §6.5).
        yardstick = {
            (d.source, t) for d, counts in counts_by_denominator for t in counts
        }
        observed: dict[str, int] = defaultdict(int)
        types: set[str] = set()
        for event in events:
            if str(event.get("subject", "")) != subject.id:
                continue
            event_type = event_type_of(event)
            if (str(event.get("source", "")), event_type) in yardstick:
                continue
            observed[event_type] += 1
            types.add(event_type)

        expected: dict[str, dict[str, Any]] = {}
        for denominator, counts in counts_by_denominator:
            for event_type in counts:
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
