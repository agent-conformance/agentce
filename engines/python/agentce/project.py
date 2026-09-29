"""The project view: every agent's records in one run, side by side (Hill 7).

A multi-subject run already assesses every subject; this module rolls that up into one ordered,
per-agent view -- a summary row per subject (declared or not), the undeclared agents nobody's
profile named, and the top blind spots across the whole project, each naming which agents it
touches. Read-only over ``assess_subjects``'/``summarize_activity``'s/``compute_blind_spots``'s own
outputs: it never re-derives a verdict or re-scans events.
"""

from __future__ import annotations

from typing import Any

from .assertions import Assertion
from .assess import index_by_subject
from .profile import Profile
from .verdict import summarize

#: How many blind spots ``compute_project_view`` lists at the top level.
MAX_TOP_GAPS = 5


def _entry_subjects(entry: dict[str, Any]) -> list[str]:
    """The distinct subjects a global blind-spot entry's own check-refs name, byte-sorted."""
    return sorted(
        {cr["subject"] for cr in entry["unlocked_checks"] + entry["needed_by_checks"]}
    )


def blind_spots_by_subject(
    blind_spots: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Group ``compute_blind_spots``'s global ``blind_spots`` list by the subjects each entry
    actually names, RE-SCOPING each entry's ``unlocked_checks``/``needed_by_checks``/
    ``checks_unlocked``/``needed_by`` to that one subject rather than copying the global counts
    (a global entry spanning two subjects must not report the other subject's checks or count under
    either one)."""
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for entry in blind_spots.get("blind_spots", []):
        unlocked = entry["unlocked_checks"]
        needed = entry["needed_by_checks"]
        for subject in _entry_subjects(entry):
            own_unlocked = [cr for cr in unlocked if cr["subject"] == subject]
            own_needed = [cr for cr in needed if cr["subject"] == subject]
            own_entry = dict(entry)
            own_entry["unlocked_checks"] = own_unlocked
            own_entry["needed_by_checks"] = own_needed
            own_entry["checks_unlocked"] = len(own_unlocked)
            own_entry["needed_by"] = len(own_needed)
            by_subject.setdefault(subject, []).append(own_entry)
    return by_subject


def no_population_by_subject(
    no_population: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """Group ``compute_blind_spots``'s global ``no_population`` list by each entry's own ``subject``
    field: unlike ``blind_spots``, every ``no_population`` entry already names exactly one subject, so
    no re-scoping is needed, only grouping -- exactly what ``index_by_subject`` (``assess.py``)
    already does for events."""
    return index_by_subject(no_population)


def compute_project_view(
    assertions: list[Assertion],
    profile: Profile,
    declared_subject_ids: frozenset[str],
    activity_by_subject: dict[str, dict[str, Any]],
    blind_spots: dict[str, Any],
) -> dict[str, Any]:
    """Return ``{"agents": [...], "undeclared_agents": [...], "top_gaps": [...]}`` for a multi-agent
    run.

    ``blind_spots`` is ``compute_blind_spots``'s own single, global return value (called once,
    unchanged) -- this function derives every per-agent view from it, it never re-computes blind
    spots per subject. Deterministic: no wall-clock, no locale, no filesystem-order dependency.
    """
    by_subject = blind_spots_by_subject(blind_spots)
    assertions_by_subject: dict[str, list[Assertion]] = {}
    for assertion in assertions:
        assertions_by_subject.setdefault(assertion.subject, []).append(assertion)
    subject_ids = sorted(
        {a.subject for a in assertions} | {s.id for s in profile.subjects}
    )

    agents: list[dict[str, Any]] = []
    for subject_id in subject_ids:
        subject_assertions = assertions_by_subject.get(subject_id, [])
        agents.append(
            {
                "id": subject_id,
                "declared": subject_id in declared_subject_ids,
                "summary": summarize(subject_assertions),
                "blind_spots_count": len(by_subject.get(subject_id, [])),
                "agents_observed": activity_by_subject.get(subject_id, {}).get(
                    "agents", []
                ),
            }
        )

    undeclared_agents = sorted(
        {agent_id for row in agents for agent_id in row["agents_observed"]}
        - declared_subject_ids
    )

    top_gaps = [
        {**entry, "agents": _entry_subjects(entry)}
        for entry in blind_spots.get("blind_spots", [])[:MAX_TOP_GAPS]
    ]

    return {
        "agents": agents,
        "undeclared_agents": undeclared_agents,
        "top_gaps": top_gaps,
    }
