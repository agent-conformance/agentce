"""Assemble assertions by evaluating a catalog against each subject (SPEC §7, §9).

For every subject the engine builds that subject's graph, then for every control in every applied
catalog it decides the outcome: ``not_applicable`` when the control's role does not match or the shape
targets nothing, ``insufficient_evidence`` when the control's minimum evidence (event type and source
class) is absent, and otherwise the structural verdict against the control's tolerance. Supporting
verdicts carry evidence pointers to the focus nodes so DC-5 holds. Only rung-2 structural controls are
evaluated in Phase 1; other rungs become ``not_assessed`` until their evaluators land.
"""

from __future__ import annotations

from typing import Any

from .applicability import effective_roles
from .assertions import Assertion, EvidencePointer
from .canonical import sha256_hex
from .catalog import Catalog, ControlSpec
from .domain import DomainBinding
from .graph import build_graph
from .iri import event_iri
from .profile import Profile, Subject
from .store import GraphStore
from .structural import evaluate_shape, within_tolerance

_EVIDENCE_CAP = 20


def _role_applies(roles: set[str], applies_to_roles: list[str]) -> bool:
    targets = set(applies_to_roles)
    if "both" in targets:
        targets |= {"deployer", "provider"}
    return bool(roles & targets)


def _class_ok(observed: str, required: str) -> bool:
    return required in ("any", "self_report") or observed == required


def requirement_met(events: list[dict[str, Any]], requirement: dict[str, str]) -> bool:
    """Whether ``events`` carries at least one event satisfying one ``minimum_evidence`` entry --
    factored out of :func:`_has_minimum_evidence` so the remediation renderer (SPEC §7) can report,
    per requirement, which of a control's minimum-evidence entries this subject's own events did and
    did not satisfy, using the exact same rule the assessment itself used to reach its outcome."""
    event_type = requirement.get("event")
    required_class = requirement.get("class", "any")
    return any(
        _event_type(e) == event_type
        and _class_ok(str(e.get("agentcesourceclass", "")), required_class)
        for e in events
    )


def _has_minimum_evidence(
    events: list[dict[str, Any]], minimum: list[dict[str, str]]
) -> bool:
    return all(requirement_met(events, requirement) for requirement in minimum)


def _event_type(event: dict[str, Any]) -> str:
    data = event.get("data")
    return (
        str(data["@type"])
        if isinstance(data, dict) and isinstance(data.get("@type"), str)
        else ""
    )


def _window(profile: Profile, events: list[dict[str, Any]]) -> tuple[str, str]:
    window = profile.observation_window
    if "start" in window and "end" in window:
        return window["start"], window["end"]
    times = sorted(str(e.get("time", "")) for e in events if e.get("time"))
    if times:
        return times[0], times[-1]
    return "1970-01-01T00:00:00Z", "1970-01-01T00:00:00Z"


def _evidence(
    events_by_iri: dict[str, dict[str, Any]], focus_nodes: list[str]
) -> list[EvidencePointer]:
    pointers: list[EvidencePointer] = []
    for focus in focus_nodes[:_EVIDENCE_CAP]:
        event = events_by_iri.get(focus)
        if event is None:
            continue
        pointers.append(
            EvidencePointer(
                ref=focus,
                digest="sha256:" + sha256_hex(event),
                source_class=str(event.get("agentcesourceclass") or "self_report"),
            )
        )
    return pointers


def _family(control_id: str) -> str:
    """The control's family: the letter prefix of its own id (SPEC §7.1; ``control.schema.json``
    documents the id pattern ``^[A-Z]{2,4}-[0-9]{2}$`` as "family prefix and two-digit number")."""
    return control_id.split("-", 1)[0] if "-" in control_id else control_id


def _crosswalk(control: ControlSpec) -> list[dict[str, Any]]:
    """Carry each control's own crosswalk entries into every assertion built for it (SPEC §7.3).

    ``verified`` is read from the control's ``verified_against_text`` and coerced to a real
    ``bool`` -- never invented, and never set ``True`` by the engine itself; only a human with
    access to the licensed standard text may flip that flag in the catalog source (human action H4).
    """
    return [
        {
            "framework": entry.get("framework"),
            "clause": entry.get("clause"),
            "verified": bool(entry.get("verified_against_text", False)),
        }
        for entry in control.raw.get("crosswalk", []) or []
    ]


def _assert_control(
    store: GraphStore,
    catalog: Catalog,
    control: ControlSpec,
    subject: Subject,
    roles: set[str],
    events: list[dict[str, Any]],
    events_by_iri: dict[str, dict[str, Any]],
    window: tuple[str, str],
) -> Assertion:
    base: dict[str, Any] = {
        "control": control.id,
        "control_version": control.version,
        "subject": subject.id,
        "rung": control.rung,
        "mode": control.mode,
        "window": window,
        "severity": control.severity,
        "family": _family(control.id),
        "crosswalk": _crosswalk(control),
    }
    if not _role_applies(roles, control.applies_to_roles):
        return Assertion(outcome="not_applicable", population=(0, 0), **base)

    shape = catalog.shape_for(control)
    if control.rung != 2 or shape is None:
        return Assertion(outcome="not_assessed", population=(0, 0), **base)

    applicable, failing, violations = evaluate_shape(
        store, shape, catalog.shapes, control.id
    )
    if not applicable:
        return Assertion(outcome="not_applicable", population=(0, 0), **base)
    if not _has_minimum_evidence(events, control.minimum_evidence):
        return Assertion(
            outcome="insufficient_evidence", population=(len(applicable), 0), **base
        )

    conformant = within_tolerance(len(applicable), len(failing), control.tolerance)
    outcome = "conformant" if conformant else "non-conformant"
    focus_for_evidence = sorted(failing) if failing else applicable
    return Assertion(
        outcome=outcome,
        population=(len(applicable), len(failing)),
        violations=[v.to_json() for v in violations],
        evidence=_evidence(events_by_iri, focus_for_evidence),
        source_class_satisfied=True,
        evidence_strength=control.raw.get("evidence_strength"),
        **base,
    )


#: The outcomes that mean a control was actually judged. A run with none of them evaluated nothing:
#: every pair was inapplicable or beyond the engine's rungs, so its clean tally says nothing.
VERDICT_OUTCOMES = frozenset({"conformant", "non-conformant", "insufficient_evidence"})


def evaluated_nothing(assertions: list[Assertion]) -> bool:
    """True when no assertion reached a verdict (see :data:`VERDICT_OUTCOMES`)."""
    return not any(a.outcome in VERDICT_OUTCOMES for a in assertions)


def index_by_subject(
    accepted: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group ``accepted`` by subject id in one pass over the list (also used by the remediation
    renderer, SPEC §7, to recover the same per-subject event list an assessment run judged)."""
    index: dict[str, list[dict[str, Any]]] = {}
    for event in accepted:
        index.setdefault(str(event.get("subject", "")), []).append(event)
    return index


def assess_subjects(
    accepted: list[dict[str, Any]],
    profile: Profile,
    catalogs: list[Catalog],
    domain: DomainBinding,
) -> list[Assertion]:
    """Evaluate every catalog control against every subject and return the assertions."""
    assertions: list[Assertion] = []
    events_by_subject = index_by_subject(accepted)
    for subject in profile.subjects:
        subject_events = events_by_subject.get(subject.id, [])
        store = build_graph(subject_events, domain=domain)
        events_by_iri = {
            event_iri(str(e["id"])): e for e in subject_events if "id" in e
        }
        roles = set(effective_roles(subject.role))
        window = _window(profile, subject_events)
        for catalog in catalogs:
            for control in catalog.controls:
                assertions.append(
                    _assert_control(
                        store,
                        catalog,
                        control,
                        subject,
                        roles,
                        subject_events,
                        events_by_iri,
                        window,
                    )
                )
    return assertions
