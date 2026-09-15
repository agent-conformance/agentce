"""The adapter parses untrusted decision logs; malformed or partial entries degrade, never crash."""

from __future__ import annotations

import json
from typing import Any

import pytest
from agentce_adapters import adapt
from agentce_adapters.policy_engines import _decision, _principal


def _opa(**fields: Any) -> Any:
    entry = {"timestamp": "2026-05-04T10:00:00.000Z", "decision_id": "d", **fields}
    return adapt(json.dumps(entry), engine="opa", subject="s")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, "allow"),
        (False, "deny"),
        ("Allow", "allow"),
        ("permit", "allow"),
        ("Deny", "deny"),
        ("denied", "deny"),
        ("conditional", "require_approval"),
        ("transform", "transform"),
        ("gibberish", None),
        (42, None),
        (None, None),
    ],
)
def test_decision_normalisation(raw: object, expected: str | None) -> None:
    assert _decision(raw) == expected


def test_principal_requires_id_and_valid_kind() -> None:
    assert _principal({"id": "p", "kind": "agent"}) == {"id": "p", "kind": "agent"}
    assert _principal({"id": "p", "kind": "robot"}) is None
    assert _principal({"kind": "agent"}) is None
    assert _principal("not-a-dict") is None
    assert _principal(
        {"id": "p", "kind": "human", "org": "acme", "authority_ref": "a"}
    ) == {
        "id": "p",
        "kind": "human",
        "org": "acme",
        "authority_ref": "a",
    }


def test_a_decision_that_cannot_be_normalised_is_skipped() -> None:
    result = _opa(result="mumble")
    assert result.events == []
    assert result.report.skipped[0].reason == "incomplete_decision"


def test_result_object_is_not_mistaken_for_a_decision() -> None:
    # A structured result that is neither bool nor a known string cannot be normalised -> skipped.
    result = _opa(result={"allow": True})
    assert result.report.skipped[0].reason == "incomplete_decision"


def test_missing_timestamp_or_id_is_skipped() -> None:
    no_time = adapt(
        json.dumps({"decision_id": "d", "result": True}), engine="opa", subject="s"
    )
    assert no_time.report.skipped[0].reason == "missing_envelope"
    no_id = adapt(
        json.dumps({"timestamp": "2026-05-04T10:00:00.000Z", "result": True}),
        engine="opa",
        subject="s",
    )
    assert no_id.report.skipped[0].reason == "missing_envelope"


def test_id_falls_back_to_trace_and_span() -> None:
    entry = {
        "timestamp": "2026-05-04T10:00:00.000Z",
        "trace_id": "0" * 32,
        "span_id": "1" * 16,
        "result": True,
    }
    event = adapt(json.dumps(entry), engine="opa", subject="s").events[0]
    assert event["id"] == "opa:" + "0" * 32 + "/" + "1" * 16
    assert event["agentcetrace"] == "0" * 32


def test_invalid_json_and_non_object_lines_are_skipped() -> None:
    log = '{"broken\n"a string"\n' + json.dumps(
        {"timestamp": "2026-05-04T10:00:00.000Z", "decision_id": "d", "result": True}
    )
    result = adapt(log, engine="opa", subject="s")
    assert len(result.events) == 1
    reasons = {s.reason for s in result.report.skipped}
    assert reasons == {"invalid_json", "not_an_object"}


def test_openfga_needs_a_complete_tuple() -> None:
    entry = {
        "timestamp": "2026-05-04T09:00:00.000Z",
        "check_id": "c",
        "tuple_key": {"user": "u", "relation": "r"},
        "allowed": True,
    }
    result = adapt(json.dumps(entry), engine="openfga", subject="s")
    assert result.report.skipped[0].reason == "incomplete_decision"


def test_trace_context_is_carried_when_present() -> None:
    entry = {
        "timestamp": "2026-05-04T10:00:00.000Z",
        "decision_id": "d",
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "parent_span_id": "c" * 16,
        "task_id": "t-1",
        "result": True,
    }
    event = adapt(json.dumps(entry), engine="opa", subject="s").events[0]
    assert event["agentcetrace"] == "a" * 32
    assert event["agentceparent"] == "c" * 16
    assert event["agentcetask"] == "t-1"


def test_blank_lines_are_ignored() -> None:
    entry = json.dumps(
        {"timestamp": "2026-05-04T10:00:00.000Z", "decision_id": "d", "result": True}
    )
    result = adapt("\n\n" + entry + "\n\n", engine="opa", subject="s")
    assert result.report.entries_seen == 1
    assert len(result.events) == 1


def test_str_and_bytes_are_both_accepted() -> None:
    entry = {
        "timestamp": "2026-05-04T10:00:00.000Z",
        "decision_id": "d",
        "result": True,
    }
    as_bytes = adapt(json.dumps(entry).encode(), engine="opa", subject="s").events
    as_str = adapt(json.dumps(entry), engine="opa", subject="s").events
    assert as_bytes == as_str
