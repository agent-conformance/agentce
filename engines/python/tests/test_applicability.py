"""Applicability resolution: roles (IR-11), applies_to_roles, drift, and the statement schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from agentce.applicability import ControlMeta, effective_roles, resolve
from agentce.profile import Profile

_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "spec"
        / "report"
        / "applicability-statement.schema.json"
    ).read_text(encoding="utf-8")
)

SUBJECT = "spiffe://corp/agents/a"


def _profile(role: str = "both") -> Profile:
    return Profile.from_dict(
        {
            "catalogs": ["eu-ai-act@2026.09"],
            "subjects": [
                {
                    "id": SUBJECT,
                    "role": role,
                    "declared_decision_types": ["dom:D1"],
                    "third_party_components": [{"name": "comp-a"}],
                    "evidence_sources": [
                        {
                            "adapter": "gw",
                            "source": "urn:src:gw",
                            "class": "enforcement_point",
                        }
                    ],
                }
            ],
        }
    )


def _event(event_type: str, subject: str = SUBJECT, **data: Any) -> dict[str, Any]:
    return {
        "id": f"e-{event_type}-{subject}",
        "subject": subject,
        "source": "urn:src:gw",
        "time": "2026-01-01T00:00:00Z",
        "data": {"@type": event_type, **data},
    }


def test_effective_roles_ir11() -> None:
    assert effective_roles("both") == ["deployer", "provider"]
    assert effective_roles("deployer") == ["deployer"]
    assert effective_roles("provider") == ["provider"]
    assert effective_roles(None) == ["deployer"]


def test_applies_to_roles_filtering() -> None:
    controls = [
        ControlMeta("REC-01", ["deployer"]),
        ControlMeta("DOC-01", ["provider"]),
        ControlMeta("INT-01", ["both"]),
    ]
    deployer = resolve(_profile("deployer"), [], controls)[0]
    applicable = {row["id"]: row["applicable"] for row in deployer["controls"]}
    assert applicable == {"REC-01": True, "DOC-01": False, "INT-01": True}


def test_both_role_gets_provider_and_deployer_controls() -> None:
    controls = [
        ControlMeta("DOC-01", ["provider"]),
        ControlMeta("REC-01", ["deployer"]),
    ]
    both = resolve(_profile("both"), [], controls)[0]
    assert all(row["applicable"] for row in both["controls"])


def test_drift_findings() -> None:
    events = [
        _event("Decision", decision_type="dom:D2"),  # undeclared decision type
        _event("BundleLoaded", components=["comp-b"]),  # undeclared component
        _event("ToolCall", subject="spiffe://corp/agents/rogue"),  # undeclared subject
    ]
    statement = resolve(_profile(), events)[0]
    drift = {(d["kind"], d["ref"]) for d in statement["drift"]}
    assert ("undeclared_decision_type", "dom:D2") in drift
    assert ("undeclared_component", "comp-b") in drift
    assert ("undeclared_subject", "spiffe://corp/agents/rogue") in drift


def test_no_drift_when_declared() -> None:
    events = [_event("Decision", decision_type="dom:D1")]
    statement = resolve(_profile(), events)[0]
    assert statement["drift"] == []
    assert statement["observed_decision_types"] == ["dom:D1"]


def test_statement_validates_against_schema() -> None:
    events = [_event("Decision", decision_type="dom:D2")]
    statement = resolve(_profile(), events, [ControlMeta("REC-01", ["deployer"])])[0]
    jsonschema.validate(statement, _SCHEMA)
    assert statement["class_justifications"][0]["class"] == "enforcement_point"
    assert statement["roles"] == ["deployer", "provider"]
