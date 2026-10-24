"""The six literals REC-03, DAT-01, TRN-02, INT-04, ROB-01 and ROB-04 read (18.37g): three on a Decision, three on a
ToolCall."""

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
D1, T1 = event_iri("d1"), event_iri("t1")
T0, T1_AT, T2 = "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "2026-01-01T00:02:00Z"
EP, IS = "enforcement_point", "independent_system"


def ev(
    event_id: str, kind: str, time: str, data: dict[str, Any], cls: str = IS
) -> dict[str, Any]:
    return {
        "id": event_id,
        "source": f"urn:agentce:source:{cls}",
        "subject": "spiffe://corp/agents/a",
        "time": time,
        "type": f"org.agent-conformance.evidence.{kind}.v1",
        "agentcesourceclass": cls,
        "data": {"@type": kind, **data},
    }


def dec(**data: Any) -> dict[str, Any]:
    return ev("d1", "Decision", T1_AT, {"decision_type": CREDIT, **data})


def read(
    event_id: str = "r1", kind: str = "ResourceAccess", **data: Any
) -> dict[str, Any]:
    base = {"operation": "read"} if kind == "ResourceAccess" else {}
    return ev(event_id, kind, T0, {**base, **data})


def call(cls: str = EP, time: str = T1_AT, **data: Any) -> dict[str, Any]:
    return ev("t1", "ToolCall", time, {"refs": {"decision": D1}, **data}, cls)


def anchored(strength: Any = "export_anchored") -> dict[str, Any]:
    return {"integrity": {"strength": strength}}


def grant(
    time: str = T0, expires: Any = T2, status: str = "verified"
) -> dict[str, Any]:
    data: dict[str, Any] = {"verification": {"status": status}}
    if expires is not None:
        data["expires"] = expires
    return ev("g1", "DelegationIssued", time, data)


def delegated(ref: Any = event_iri("g1"), time: str = T1_AT) -> dict[str, Any]:
    return ev("t1", "ToolCall", time, {"refs": {"decision": D1, "delegation": ref}}, EP)


R1 = event_iri("r1")

CASES: list[tuple[str, list[dict[str, Any]], str, str, str]] = [
    ("inputs held", [read(), dec(inputs=[R1])], D1, "inputsRecorded", "true"),
    (
        "input dangling",
        [read(), dec(inputs=[R1, event_iri("zz")])],
        D1,
        "inputsRecorded",
        "false",
    ),
    ("inputs empty", [dec(inputs=[])], D1, "inputsRecorded", "false"),
    ("no inputs", [dec()], D1, "inputsRecorded", "false"),
    ("input bare", [read(), dec(inputs=["r1"])], D1, "inputsRecorded", "false"),
    ("inputs not a list", [read(), dec(inputs=R1)], D1, "inputsRecorded", "false"),
    ("resource read", [read(), dec(inputs=[R1])], D1, "dataConsumedRecorded", "true"),
    (
        "memory read",
        [read(kind="MemoryRead"), dec(inputs=[R1])],
        D1,
        "dataConsumedRecorded",
        "true",
    ),
    (
        "read beside dangling",
        [read(), dec(inputs=[event_iri("zz"), R1])],
        D1,
        "dataConsumedRecorded",
        "true",
    ),
    (
        "resource write",
        [read(operation="write"), dec(inputs=[R1])],
        D1,
        "dataConsumedRecorded",
        "false",
    ),
    (
        "model call only",
        [read(kind="ModelCall"), dec(inputs=[R1])],
        D1,
        "dataConsumedRecorded",
        "false",
    ),
    ("read bare", [read(), dec(inputs=["r1"])], D1, "dataConsumedRecorded", "false"),
    ("role sole", [dec(ai_role="sole")], D1, "aiRoleRecorded", "true"),
    (
        "role recommendation",
        [dec(ai_role="recommendation")],
        D1,
        "aiRoleRecorded",
        "true",
    ),
    ("role assist", [dec(ai_role="assist")], D1, "aiRoleRecorded", "true"),
    ("no role", [dec()], D1, "aiRoleRecorded", "false"),
    ("role outside", [dec(ai_role="oracle")], D1, "aiRoleRecorded", "false"),
    ("role not a string", [dec(ai_role=7)], D1, "aiRoleRecorded", "false"),
    ("anchored", [dec(), call(**anchored())], T1, "classCorroborated", "true"),
    (
        "signed",
        [dec(), call(**anchored("source_signed"))],
        T1,
        "classCorroborated",
        "true",
    ),
    (
        "chained",
        [dec(), call(**anchored("export_chained"))],
        T1,
        "classCorroborated",
        "false",
    ),
    ("no block", [dec(), call()], T1, "classCorroborated", "false"),
    (
        "block not an object",
        [dec(), call(integrity="export_anchored")],
        T1,
        "classCorroborated",
        "false",
    ),
    (
        "strength not a string",
        [dec(), call(**anchored(7))],
        T1,
        "classCorroborated",
        "false",
    ),
    (
        "independent chained",
        [dec(), call(IS, **anchored("export_chained"))],
        T1,
        "classCorroborated",
        "true",
    ),
    ("independent no block", [dec(), call(IS)], T1, "classCorroborated", "false"),
    (
        "self_report anchored",
        [dec(), call("self_report", **anchored())],
        T1,
        "classCorroborated",
        "false",
    ),
    (
        "effect write",
        [dec(), call(effect_class="write")],
        T1,
        "effectClassified",
        "true",
    ),
    (
        "effect spend",
        [dec(), call(effect_class="spend")],
        T1,
        "effectClassified",
        "true",
    ),
    ("no effect", [dec(), call()], T1, "effectClassified", "false"),
    (
        "effect outside",
        [dec(), call(effect_class="teleport")],
        T1,
        "effectClassified",
        "false",
    ),
    (
        "effect not a string",
        [dec(), call(effect_class=7)],
        T1,
        "effectClassified",
        "false",
    ),
    ("in force", [grant(), dec(), delegated()], T1, "delegationInForce", "true"),
    (
        "issued at the call",
        [grant(T1_AT), dec(), delegated()],
        T1,
        "delegationInForce",
        "true",
    ),
    (
        "issued after the call",
        [grant(T2, "2026-01-02T00:00:00Z"), dec(), delegated()],
        T1,
        "delegationInForce",
        "false",
    ),
    (
        "expired",
        [grant(expires=T0), dec(), delegated()],
        T1,
        "delegationInForce",
        "false",
    ),
    (
        "expires at the call",
        [grant(expires=T1_AT), dec(), delegated()],
        T1,
        "delegationInForce",
        "false",
    ),
    (
        "expiry offset before",
        [grant(expires="2026-01-01T01:00:30+01:00"), dec(), delegated()],
        T1,
        "delegationInForce",
        "false",
    ),
    (
        "expiry offset after",
        [grant(expires="2025-12-31T23:01:30-01:00"), dec(), delegated()],
        T1,
        "delegationInForce",
        "true",
    ),
    (
        "no expiry",
        [grant(expires=None), dec(), delegated()],
        T1,
        "delegationInForce",
        "false",
    ),
    (
        "expiry unplaceable",
        [grant(expires="next tuesday"), dec(), delegated()],
        T1,
        "delegationInForce",
        "false",
    ),
    (
        "expiry not a string",
        [grant(expires=1767229200), dec(), delegated()],
        T1,
        "delegationInForce",
        "false",
    ),
    (
        "call time unplaceable",
        [grant(), dec(), delegated(time="soon")],
        T1,
        "delegationInForce",
        "false",
    ),
    (
        "unverified",
        [grant(status="failed"), dec(), delegated()],
        T1,
        "delegationInForce",
        "false",
    ),
    (
        "dangling",
        [grant(), dec(), delegated(event_iri("zz"))],
        T1,
        "delegationInForce",
        "false",
    ),
    ("bare", [grant(), dec(), delegated("g1")], T1, "delegationInForce", "false"),
    ("another type", [grant(), dec(), delegated(D1)], T1, "delegationInForce", "false"),
    ("no delegation", [grant(), dec(), call()], T1, "delegationInForce", "false"),
]


@pytest.mark.parametrize(
    ("events", "node", "literal", "want"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_call_record_literal(
    events: list[dict[str, Any]], node: str, literal: str, want: str
) -> None:
    store = build_graph(events, domain=DOMAIN)
    assert store.literal_values(node, f"agentce:{literal}") == [want]


@pytest.mark.parametrize(
    "copy",
    [
        "../typescript/testdata/call-record-cases.json",
        "../java/src/test/resources/testdata/call-record-cases.json",
    ],
)
def test_engine_tables_match_cases(copy: str) -> None:
    table = json.loads((Path(__file__).parents[1] / copy).read_text(encoding="utf-8"))
    assert [
        (c["name"], c["events"], c["node"], c["literal"], c["want"])
        for c in table["cases"]
    ] == [(n, e, nd, lit, w) for n, e, nd, lit, w in CASES]
