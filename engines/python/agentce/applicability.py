"""Applicability resolution (SPEC §6.5, §7.3, IR-11).

For each subject the resolver derives the enterprise roles (IR-11: an in-house or substantially
modified system is ``both`` provider and deployer; an unmodified third-party system is ``deployer``),
selects the controls that apply to those roles (``applies_to_roles``) from the declared catalogs and
overlays, and records why. It reconciles the declared scope against what the evidence shows and
raises drift findings -- an observed decision type that was never declared, a bundle component that
was never declared, or an event from a subject the profile does not name -- and carries the
trust-class justifications so an assessor can challenge them. The output is the applicability
statement (applicability-statement.schema.json).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .profile import Profile, Subject

ROLES = ("deployer", "provider")


@dataclass
class ControlMeta:
    """The applicability-relevant metadata of a catalog control (the catalog lands in item 1.9)."""

    id: str
    applies_to_roles: list[str] = field(default_factory=lambda: ["both"])
    family: str = ""


def effective_roles(role: str | None) -> list[str]:
    """Expand a declared role to concrete roles (``both`` -> deployer and provider), per IR-11."""
    if role == "both":
        return list(ROLES)
    if role in ROLES:
        return [role]
    return [
        "deployer"
    ]  # the conservative default when a subject does not declare its role


def _role_applies(subject_roles: set[str], applies_to_roles: list[str]) -> bool:
    targets = set(applies_to_roles)
    if "both" in targets:
        targets |= set(ROLES)
    return bool(subject_roles & targets)


def _event_type(event: dict[str, Any]) -> str:
    data = event.get("data")
    return (
        str(data["@type"])
        if isinstance(data, dict) and isinstance(data.get("@type"), str)
        else ""
    )


def _observed_decision_types(
    subject_id: str, events: list[dict[str, Any]]
) -> list[str]:
    observed: set[str] = set()
    for event in events:
        if (
            str(event.get("subject", "")) != subject_id
            or _event_type(event) != "Decision"
        ):
            continue
        data = event.get("data")
        if isinstance(data, dict) and isinstance(data.get("decision_type"), str):
            observed.add(str(data["decision_type"]))
    return sorted(observed)


def _observed_components(subject_id: str, events: list[dict[str, Any]]) -> set[str]:
    observed: set[str] = set()
    for event in events:
        if str(event.get("subject", "")) != subject_id:
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        if _event_type(event) == "Component":
            for key in ("name", "id"):
                if isinstance(data.get(key), str):
                    observed.add(str(data[key]))
        if _event_type(event) == "BundleLoaded":
            for component in data.get("components", []) or []:
                if isinstance(component, str):
                    observed.add(component)
                elif isinstance(component, dict) and isinstance(
                    component.get("name"), str
                ):
                    observed.add(str(component["name"]))
    return observed


def _class_justifications(subject: Subject) -> list[dict[str, str]]:
    out = []
    for source in subject.evidence_sources:
        if source.cls:
            out.append(
                {
                    "source": source.source,
                    "class": source.cls,
                    "justification": _justification(subject, source.source),
                }
            )
    return out


def _justification(subject: Subject, source_id: str) -> str:
    return f"declared for {source_id} in the applicability profile of {subject.id}"


def resolve_subject(
    subject: Subject,
    events: list[dict[str, Any]],
    controls: list[ControlMeta],
    catalogs: list[str],
) -> dict[str, Any]:
    """Resolve the applicability statement for one subject."""
    roles = effective_roles(subject.role)
    role_set = set(roles)
    control_rows = []
    for control in sorted(controls, key=lambda c: c.id):
        applicable = _role_applies(role_set, control.applies_to_roles)
        control_rows.append(
            {
                "id": control.id,
                "applicable": applicable,
                "reason_code": "role_match" if applicable else "role_mismatch",
                "reason_refs": sorted(role_set & _expanded(control.applies_to_roles)),
            }
        )

    observed = _observed_decision_types(subject.id, events)
    declared = sorted(set(subject.declared_decision_types))
    drift: list[dict[str, str]] = []
    for decision_type in observed:
        if decision_type not in declared:
            drift.append({"kind": "undeclared_decision_type", "ref": decision_type})
    for component in sorted(
        _observed_components(subject.id, events) - set(subject.declared_components)
    ):
        drift.append({"kind": "undeclared_component", "ref": component})

    return {
        "subject": subject.id,
        "roles": roles,
        "catalogs": list(catalogs),
        "controls": control_rows,
        "observed_decision_types": observed,
        "declared_decision_types": declared,
        "drift": drift,
        "class_justifications": _class_justifications(subject),
    }


def _expanded(applies_to_roles: list[str]) -> set[str]:
    targets = set(applies_to_roles)
    if "both" in targets:
        targets |= set(ROLES)
    return targets & set(ROLES)


def resolve(
    profile: Profile,
    events: list[dict[str, Any]],
    controls: list[ControlMeta] | None = None,
) -> list[dict[str, Any]]:
    """Resolve one applicability statement per declared subject.

    An event whose ``subject`` names no declared subject is an ``undeclared_subject`` drift finding,
    recorded on the first subject's statement (a bundle-level drift with no subject of its own).
    """
    controls = controls or []
    statements = [
        resolve_subject(s, events, controls, profile.catalogs) for s in profile.subjects
    ]

    declared_ids = {s.id for s in profile.subjects}
    observed_subjects = {str(e.get("subject", "")) for e in events} - {""}
    undeclared = sorted(observed_subjects - declared_ids)
    if undeclared and statements:
        statements[0]["drift"].extend(
            {"kind": "undeclared_subject", "ref": s} for s in undeclared
        )
    return statements
