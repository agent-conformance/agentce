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


def _has_minimum_evidence(
    events: list[dict[str, Any]], minimum: list[dict[str, str]]
) -> bool:
    for requirement in minimum:
        event_type = requirement.get("event")
        required_class = requirement.get("class", "any")
        if not any(
            _event_type(e) == event_type
            and _class_ok(str(e.get("agentcesourceclass", "")), required_class)
            for e in events
        ):
            return False
    return True


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


def assess_subjects(
    accepted: list[dict[str, Any]],
    profile: Profile,
    catalogs: list[Catalog],
    domain: DomainBinding,
) -> list[Assertion]:
    """Evaluate every catalog control against every subject and return the assertions."""
    assertions: list[Assertion] = []
    for subject in profile.subjects:
        subject_events = [
            e for e in accepted if str(e.get("subject", "")) == subject.id
        ]
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
