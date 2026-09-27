"""Blind spots: the one missing record type that would unlock the most checks (Hill 2).

A run's assertions already say some controls are ``insufficient_evidence``, and the remediation
package already computes, per subject and control, which ``minimum_evidence`` entries a subject's
own events did and did not satisfy (:func:`agentce.assess.requirement_met`). ``compute_blind_spots``
groups that across every subject and catalog to answer a sharper question: of everything the records
can't show yet, which single missing requirement, if supplied, would flip the most checks -- who
usually owns supplying it, and is doing so a same-sitting code change or a request to another team.

Read-only over ``(assertions, profile, catalogs, events)``: it never affects an outcome or the
verdict.
"""

from __future__ import annotations

import functools
import json
from importlib import resources
from typing import Any

from .assess import (
    SELF_REPORT_EQUIVALENT_CLASSES,
    _class_ok,
    index_by_subject,
    requirement_met,
)
from .assertions import Assertion
from .catalog import Catalog, ControlSpec
from .profile import Profile

#: The seven OpenTelemetry-GenAI-shaped trace events (RFC 0008 Sec.3): a self-report requirement for
#: one of these is rung 1 ("what happened"); every other self-report requirement is rung 2 (the
#: agent's own account of a decision, approval, or similar).
_OTEL_SHAPED_EVENTS = frozenset(
    {
        "ModelCall",
        "ToolCall",
        "ResourceAccess",
        "MemoryRead",
        "MemoryWrite",
        "SessionStart",
        "SessionEnd",
    }
)

#: (owner_key, step_kind) by rung (RFC 0008 Sec.3; ``VALUE-PROP.md``'s evidence-ladder table verbatim).
_OWNER_AND_STEP_BY_RUNG = {
    1: ("agent_team", "code_change"),
    2: ("agent_team", "code_change"),
    3: ("platform_or_security", "request"),
    4: ("ticketing_or_iam", "request"),
}

CheckRef = dict[str, str]


def _normalize_class(cls: str) -> str:
    """``assess._class_ok`` treats a required class of ``any`` and ``self_report`` identically, so
    they group as one key (RFC 0008 Sec.2) -- otherwise one real fix (e.g. "emit Decision events")
    would split into two undercounted blind spots."""
    return "self_report" if cls in SELF_REPORT_EQUIVALENT_CLASSES else cls


@functools.cache
def _event_producers() -> dict[str, dict[str, str]]:
    """The vendored, per-event map of adapter name to that adapter's default trust class (RFC 0008
    Sec.5), kept in sync with every ``adapters/*/support-matrix.yaml`` by
    ``tools/support_matrix_sync_check.py``."""
    text = (
        resources.files("agentce.data.support_matrices")
        .joinpath("event_producers.json")
        .read_text(encoding="utf-8")
    )
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _ladder_rung(event: str, normalized_class: str) -> int:
    """The evidence-ladder rung for a grouping key: by who can actually supply ``event`` today
    (RFC 0008 Sec.3), not only the catalog's literal required class."""
    if normalized_class == "independent_system":
        return 4
    if normalized_class == "enforcement_point":
        return 3
    producers = _event_producers().get(event, {})
    if not producers:
        # No adapter declares this event at all: a generic, adapter-free event (Decision/Outcome/
        # Incident) any agent can emit directly -- not a vacuous rung 3 (round 2's fix).
        return 2
    if all(source_class == "enforcement_point" for source_class in producers.values()):
        # The catalog would accept self-reported evidence in principle, but today only a gateway,
        # policy engine, identity provider, or supply-chain verifier ever produces this event.
        return 3
    return 1 if event in _OTEL_SHAPED_EVENTS else 2


def _supplying_adapters(event: str, normalized_class: str) -> list[str]:
    """Only the adapters whose default class actually satisfies this key (RFC 0008 Sec.3), reusing
    ``assess._class_ok`` -- never every adapter that merely declares the event."""
    producers = _event_producers().get(event, {})
    return sorted(
        adapter
        for adapter, source_class in producers.items()
        if _class_ok(source_class, normalized_class)
    )


def _check_sort_key(ref: CheckRef) -> tuple[str, str, str, str]:
    return (ref["subject"], ref["catalog"], ref["control"], ref["control_version"])


def _replay_triples(
    profile: Profile, catalogs: list[Catalog]
) -> list[tuple[str, str, ControlSpec]]:
    """The exact ``(subject.id, catalog.id, control)`` sequence ``assess.assess_subjects`` builds, in
    its own iteration order (RFC 0008 Sec.6) -- so ``assertions`` (that same loop's own output) can be
    paired with the ``(catalog, control)`` it came from by position, without a ``catalog`` field on
    ``Assertion`` itself."""
    return [
        (subject.id, catalog.id, control)
        for subject in profile.subjects
        for catalog in catalogs
        for control in catalog.controls
    ]


def compute_blind_spots(
    assertions: list[Assertion],
    profile: Profile,
    catalogs: list[Catalog],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return ``{"blind_spots": [...], "no_population": [...]}`` for ``assertions``.

    ``assertions`` must be :func:`agentce.assess.assess_subjects`'s own output, in its own order --
    that order is meaningful input here (RFC 0008 Sec.6), not incidental: it is replayed positionally
    against ``profile``/``catalogs`` to recover each assertion's ``(catalog, control)`` origin without
    a schema change to ``Assertion``. A pure function otherwise: no I/O, no network, no clock: reads
    only the vendored ``event_producers.json`` besides its arguments."""
    triples = _replay_triples(profile, catalogs)
    if len(triples) != len(assertions):
        raise AssertionError(
            f"compute_blind_spots: {len(assertions)} assertions but {len(triples)} (subject, "
            "catalog, control) triples replayed from profile/catalogs -- assertions must be "
            "assess_subjects' own output."
        )

    events_by_subject = index_by_subject(events)
    # Memoized per (subject, event, class): many controls share the same requirement, so without this
    # a subject's events would be rescanned once per control that names it.
    met_cache: dict[tuple[str, str, str], bool] = {}

    def _met(subject_id: str, requirement: dict[str, str]) -> bool:
        event = str(requirement.get("event", ""))
        cls = str(requirement.get("class", "any"))
        key = (subject_id, event, cls)
        if key not in met_cache:
            met_cache[key] = requirement_met(
                events_by_subject.get(subject_id, []), requirement
            )
        return met_cache[key]

    # (event, normalized_class) -> ([unlocked check refs], [needed_by check refs]).
    groups: dict[tuple[str, str], tuple[list[CheckRef], list[CheckRef]]] = {}
    no_population: list[CheckRef] = []

    for (subject_id, catalog_id, control), a in zip(triples, assertions, strict=True):
        if (
            a.subject != subject_id
            or a.control != control.id
            or a.control_version != control.version
        ):
            raise AssertionError(
                f"compute_blind_spots: assertion is ({a.subject!r}, {a.control!r}, "
                f"{a.control_version!r}) but the replayed sequence expects ({subject_id!r}, "
                f"{catalog_id!r}, {control.id!r}, {control.version!r}) -- assertions is not "
                "assess_subjects' own order."
            )
        if a.outcome != "insufficient_evidence":
            continue
        missing = [r for r in control.minimum_evidence if not _met(subject_id, r)]
        normalized_keys = sorted(
            {
                (str(r.get("event", "")), _normalize_class(str(r.get("class", "any"))))
                for r in missing
            }
        )
        check_ref: CheckRef = {
            "subject": subject_id,
            "catalog": catalog_id,
            "control": control.id,
            "control_version": control.version,
        }
        if not normalized_keys:
            # Every minimum_evidence entry is actually satisfied, yet the shape's own structural
            # population was still empty -- a narrower, rarer condition than "which branch fired"
            # (RFC 0008 Sec.4); this holds regardless of population[0], so it is checked before the
            # branch distinction below even matters.
            no_population.append(check_ref)
            continue
        # `population[0] > 0` (the minimum-evidence branch) reached `insufficient_evidence` only
        # because `_has_minimum_evidence` failed, so supplying every missing entry is provably
        # sufficient -- but only when exactly one key is missing (RFC 0008 Sec.2: no partial credit).
        # The empty-population branch's non-empty missing set is informative but never provably
        # sufficient on its own (round 3), so it always lands in `needed_by`, never `unlocked`.
        unlocked = a.population[0] > 0 and len(normalized_keys) == 1
        for key in normalized_keys:
            unlocked_list, needed_list = groups.setdefault(key, ([], []))
            (unlocked_list if unlocked else needed_list).append(check_ref)

    blind_spots: list[dict[str, Any]] = []
    for (event, cls), (unlocked_checks, needed_by_checks) in groups.items():
        rung = _ladder_rung(event, cls)
        owner_key, step_kind = _OWNER_AND_STEP_BY_RUNG[rung]
        unlocked_list = sorted(unlocked_checks, key=_check_sort_key)
        needed_list = sorted(needed_by_checks, key=_check_sort_key)
        blind_spots.append(
            {
                "event": event,
                "class": cls,
                "ladder_rung": rung,
                "owner_key": owner_key,
                "step_kind": step_kind,
                "supplying_adapters": _supplying_adapters(event, cls),
                "checks_unlocked": len(unlocked_list),
                "unlocked_checks": unlocked_list,
                "needed_by": len(needed_list),
                "needed_by_checks": needed_list,
            }
        )
    blind_spots.sort(
        key=lambda b: (
            -b["checks_unlocked"],
            -b["needed_by"],
            b["ladder_rung"],
            b["event"],
            b["class"],
        )
    )
    no_population.sort(key=_check_sort_key)
    return {"blind_spots": blind_spots, "no_population": no_population}
