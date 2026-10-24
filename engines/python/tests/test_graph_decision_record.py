"""The eight decision-record literals DOC-02, TRN-04, OVS-05, RSK-01, REC-05, TRN-01 and TRN-05 read (18.37f)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentce.domain import DomainBinding
from agentce.graph import build_graph
from agentce.iri import event_iri

CREDIT = "agentce:CreditDecision"
DOMAIN = DomainBinding.from_dict(
    {
        "decision_types": [
            {
                "id": CREDIT,
                "subclass_of": "agentce:ConsequentialDecision",
                "consequential": True,
            }
        ]
    }
)
D1 = event_iri("d1")
DIGEST = "sha256:" + "a" * 64
T0, T1, T2 = "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "2026-01-01T00:02:00Z"


def ev(event_id: str, kind: str, time: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event_id,
        "source": "urn:agentce:source:src",
        "subject": "spiffe://corp/agents/a",
        "time": time,
        "type": f"org.agent-conformance.evidence.{kind}.v1",
        "agentcesourceclass": "independent_system",
        "data": {"@type": kind, **data},
    }


def dec(**data: Any) -> dict[str, Any]:
    return ev("d1", "Decision", T1, {"decision_type": CREDIT, **data})


def pd(event_id: str, decision: str | None = "allow", **data: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "policy_id": "p",
        "policy_version": "1",
        "policy_digest": DIGEST,
    }
    if decision is not None:
        base["decision"] = decision
    return ev(event_id, "PolicyDecision", T1, {**base, **data})


def notice(time: str = T2, **data: Any) -> dict[str, Any]:
    return ev(
        "n1",
        "Notice",
        T2,
        {
            "notice_type": "explanation",
            "person_ref": "p:1",
            "delivered_at": time,
            "refs": {"decision": D1},
            **data,
        },
    )


def disclosure(time: str = T0, **data: Any) -> dict[str, Any]:
    return ev(
        "x1",
        "Disclosure",
        T0,
        {
            "disclosure_type": "ai_interaction",
            "session_id": "s1",
            "delivered_at": time,
            **data,
        },
    )


AUTH = {"refs": {"authorization": event_iri("p1")}}

CASES: list[tuple[str, list[dict[str, Any]], str, str]] = [
    ("held", [dec(**AUTH), pd("p1")], "authorizationHeld", "true"),
    (
        "held via request",
        [dec(), pd("p1", refs={"request": D1})],
        "authorizationHeld",
        "true",
    ),
    ("deny", [dec(**AUTH), pd("p1", "deny")], "authorizationHeld", "false"),
    (
        "deny via request",
        [dec(**AUTH), pd("p1"), pd("p2", "deny", refs={"request": D1})],
        "authorizationHeld",
        "false",
    ),
    (
        "bare",
        [dec(refs={"authorization": "p1"}), pd("p1", refs={"request": D1})],
        "authorizationHeld",
        "false",
    ),
    ("unheld", [dec(**AUTH)], "authorizationHeld", "false"),
    (
        "not a string",
        [dec(refs={"authorization": 7}), pd("p1")],
        "authorizationHeld",
        "false",
    ),
    ("none", [dec(), pd("p1")], "authorizationHeld", "false"),
    ("allow clears", [dec(**AUTH), pd("p1")], "authorizationGateCleared", "true"),
    (
        "transform",
        [dec(**AUTH), pd("p1", "transform")],
        "authorizationGateCleared",
        "false",
    ),
    (
        "no decision value",
        [dec(**AUTH), pd("p1", None)],
        "authorizationGateCleared",
        "false",
    ),
    (
        "require_approval unapproved",
        [dec(**AUTH), pd("p1", "require_approval")],
        "authorizationGateCleared",
        "false",
    ),
    ("identity", [dec(**AUTH), pd("p1")], "authorityRecorded", "true"),
    (
        "digest malformed",
        [dec(**AUTH), pd("p1", policy_digest="sha256:XYZ")],
        "authorityRecorded",
        "false",
    ),
    (
        "digest not a string",
        [dec(**AUTH), pd("p1", policy_digest=1)],
        "authorityRecorded",
        "false",
    ),
    (
        "empty version",
        [dec(**AUTH), pd("p1", policy_version="")],
        "authorityRecorded",
        "false",
    ),
    (
        "chosen in options",
        [dec(options=[{"id": "a"}, {"id": "b"}], chosen="b")],
        "decisionDocumented",
        "true",
    ),
    ("chosen, no options", [dec(chosen="a")], "decisionDocumented", "true"),
    ("no chosen", [dec()], "decisionDocumented", "false"),
    ("chosen not a string", [dec(chosen=3)], "decisionDocumented", "false"),
    (
        "chosen outside",
        [dec(options=[{"id": "a"}], chosen="b")],
        "decisionDocumented",
        "false",
    ),
    (
        "options not objects",
        [dec(options=["a"], chosen="a")],
        "decisionDocumented",
        "false",
    ),
    ("explanation after", [dec(), notice()], "explanationTimed", "true"),
    ("explanation equal", [dec(), notice(T1)], "explanationTimed", "true"),
    ("explanation before", [dec(), notice(T0)], "explanationTimed", "false"),
    ("explanation unplaceable", [dec(), notice("soon")], "explanationTimed", "false"),
    (
        "other notice type",
        [dec(), notice(notice_type="adverse_action")],
        "explanationTimed",
        "false",
    ),
    ("notified", [dec(person_ref="p:1"), notice()], "affectedPersonNotified", "true"),
    (
        "other person",
        [dec(person_ref="p:2"), notice()],
        "affectedPersonNotified",
        "false",
    ),
    (
        "no person_ref",
        [dec(), notice(person_ref=None)],
        "affectedPersonNotified",
        "false",
    ),
    ("no notice", [dec()], "affectedPersonNotified", "false"),
    ("in scope by default", [dec()], "inDisclosureScope", "true"),
    ("not a boolean", [dec(affects_natural_person="no")], "inDisclosureScope", "true"),
    ("out of scope", [dec(affects_natural_person=False)], "inDisclosureScope", "false"),
    (
        "disclosed",
        [dec(session_id="s1"), disclosure()],
        "aiInteractionDisclosed",
        "true",
    ),
    (
        "disclosed at",
        [dec(session_id="s1"), disclosure(T1)],
        "aiInteractionDisclosed",
        "true",
    ),
    (
        "disclosed after",
        [dec(session_id="s1"), disclosure(T2)],
        "aiInteractionDisclosed",
        "false",
    ),
    (
        "other session",
        [dec(session_id="s2"), disclosure()],
        "aiInteractionDisclosed",
        "false",
    ),
    ("no session_id", [dec(), disclosure()], "aiInteractionDisclosed", "false"),
    (
        "other type",
        [dec(session_id="s1"), disclosure(disclosure_type="synthetic_marking")],
        "aiInteractionDisclosed",
        "false",
    ),
]


@pytest.mark.parametrize(
    ("events", "literal", "want"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_decision_record_literal(
    events: list[dict[str, Any]], literal: str, want: str
) -> None:
    store = build_graph(events, domain=DOMAIN)
    assert store.literal_values(D1, f"agentce:{literal}") == [want]


def test_literals_ignore_arrival_order() -> None:
    events = [pd("p1", refs={"request": D1}), notice(), dec(person_ref="p:1")]
    store = build_graph(events, domain=DOMAIN)
    for literal in ("authorizationHeld", "explanationTimed", "affectedPersonNotified"):
        assert store.literal_values(D1, f"agentce:{literal}") == ["true"]


@pytest.mark.parametrize(
    "copy",
    [
        "../typescript/testdata/decision-record-cases.json",
        "../java/src/test/resources/testdata/decision-record-cases.json",
    ],
)
def test_engine_tables_match_cases(copy: str) -> None:
    table = json.loads((Path(__file__).parents[1] / copy).read_text(encoding="utf-8"))
    assert [
        (c["name"], c["events"], c["literal"], c["want"]) for c in table["cases"]
    ] == [(n, e, lit, w) for n, e, lit, w in CASES]
    assert table["decision"] == D1
