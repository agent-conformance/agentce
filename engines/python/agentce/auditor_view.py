"""The auditor view (18.17): "can I rely on this, clause by clause?"

``compute_auditor_view`` adds no new rollup and re-derives no verdict: one ``Assertion`` already is
one clause's full record (control, version, subject, mode, outcome, evidence, crosswalk, and, when one
applies, its deviation), so ``clauses`` selects that record as-is, one entry per assertion. ``by_clause``
is a purely-derived navigation index built from the same entries' own ``crosswalk`` field -- an
auditor checking one external clause looks it up here, gets the control ids, and reads each one's full
record inline in ``clauses``; no outcome or verdict is synthesized at this level (SPEC §9.2 forbids a
composite score), and a control with no crosswalk entry is still fully present in ``clauses``, simply
absent from ``by_clause``.

Manual-checklist *application* is out of scope here (``assess.py``'s own documented rung-2-only
boundary): a ``manual``/``semi-automated`` control still ``not_assessed`` carries a disclosed,
not-yet-evaluated note instead of a fabricated result.
"""

from __future__ import annotations

from typing import Any

from . import messages
from .assertions import Assertion, aggregate
from .assess import deviations_by_control

#: Modes whose ``not_assessed`` outcome gets the disclosed not-yet-evaluated note: a checklist answer
#: cannot be recorded as a live outcome yet (``readiness.checklist_lint``'s own docstring), so this
#: view states that honestly rather than staying silent.
_MANUAL_MODES = frozenset({"manual", "semi-automated"})

#: The register record's own six required fields (deviation-register.schema.json), copied verbatim.
_DEVIATION_FIELDS = (
    "rationale",
    "compensating_control",
    "owner",
    "approver",
    "granted",
    "expiry",
)


def _deviation_detail(
    deviation: str, by_control: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """The matching register entry for ``deviation`` (an assertion's own ``deviation`` field, its
    control id), verbatim -- or, when no register was passed (a report re-rendered from
    ``assertions.json`` alone, with no live ``--deviations`` file available), a minimal record naming
    only the control id. Never re-validated here: the register was already linted by
    :func:`agentce.readiness.deviation_lint` before :func:`agentce.assess.apply_deviations` used it;
    this function only reads it."""
    entry = by_control.get(deviation)
    if entry is None:
        return {"control": deviation}
    detail: dict[str, Any] = {
        field: str(entry.get(field, "")) for field in _DEVIATION_FIELDS
    }
    if entry.get("evidence_refs"):
        detail["evidence_refs"] = [str(ref) for ref in entry["evidence_refs"]]
    return detail


def compute_auditor_view(
    assertions: list[Assertion],
    deviations: list[dict[str, Any]] | None = None,
    *,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return the clause-by-clause selection of ``assertions`` for a whole run.

    ``deviations`` is the same already-linted register :func:`agentce.assess.apply_deviations` applied
    (never re-validated here). ``counts`` lets a caller that already holds
    :func:`agentce.assertions.aggregate`'s result over the same ``assertions`` (``write_report`` always
    does) pass it straight through instead of a second full pass; omitting it recomputes it here, so
    every other/test caller is unaffected. Deterministic: no wall-clock, no locale, no assertion-order
    dependency (``clauses`` is sorted by ``(control, subject)``, ``by_clause`` by ``(framework,
    clause)`` with its control ids deduplicated and sorted)."""
    by_control = deviations_by_control(deviations)
    cat = messages.catalogue()
    note = cat["report.manual_checklist_not_yet_evaluated"]

    clauses: list[dict[str, Any]] = []
    by_clause: dict[str, dict[str, set[str]]] = {}
    for a in sorted(assertions, key=lambda x: (x.control, x.subject)):
        entry: dict[str, Any] = {
            "control": a.control,
            "control_version": a.control_version,
            "subject": a.subject,
            "outcome": a.outcome,
            "mode": a.mode,
            "rung": a.rung,
            "evidence": [e.to_json() for e in a.evidence],
            "crosswalk": a.crosswalk,
        }
        if a.deviation:
            entry["deviation"] = _deviation_detail(a.deviation, by_control)
        if a.mode in _MANUAL_MODES and a.outcome == "not_assessed":
            entry["manual_checklist_note"] = note
        clauses.append(entry)
        for xw in a.crosswalk:
            framework = str(xw.get("framework", ""))
            clause = str(xw.get("clause", ""))
            by_clause.setdefault(framework, {}).setdefault(clause, set()).add(a.control)

    return {
        "clauses": clauses,
        "by_clause": {
            framework: {
                clause: sorted(control_ids)
                for clause, control_ids in sorted(clause_map.items())
            }
            for framework, clause_map in sorted(by_clause.items())
        },
        "counts": counts if counts is not None else aggregate(assertions),
    }
