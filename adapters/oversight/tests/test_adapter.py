"""Behavioural and contract tests for the oversight adapter (SPEC 12.1, 12.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agentce.canonical import canonical_string
from agentce.schema import validate_event

from agentce_adapters import AdapterError, adapt
from agentce_adapters.fixtures import Fixture, discover_fixtures

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = discover_fixtures(ROOT / "fixtures")
FIXTURES_BY_NAME = {fixture.name: fixture for fixture in FIXTURES}


def _fixture(name: str) -> Fixture:
    return FIXTURES_BY_NAME[name]


def _events(name: str) -> list[dict[str, Any]]:
    return _fixture(name).run().events


def _one(name: str, event_type: str) -> dict[str, Any]:
    return next(e for e in _events(name) if e["data"]["@type"] == event_type)


def test_fixtures_present() -> None:
    assert {f.name for f in FIXTURES} == {
        "disclosure-notices",
        "ticketing-approvals",
        "workflow-oversight",
    }


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_output_is_byte_identical_to_expected(fixture: Fixture) -> None:
    produced = fixture.run().events
    assert len(produced) == len(fixture.expected), fixture.name
    for event, expected in zip(produced, fixture.expected, strict=True):
        assert canonical_string(event) == canonical_string(expected)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_every_event_is_schema_valid(fixture: Fixture) -> None:
    for event in fixture.run().events:
        assert validate_event(event) == [], event["id"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_adaptation_is_deterministic(fixture: Fixture) -> None:
    first = [canonical_string(e) for e in fixture.run().events]
    second = [canonical_string(e) for e in fixture.run().events]
    assert first == second


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_ids_are_unique_and_prefixed(fixture: Fixture) -> None:
    events = fixture.run().events
    ids = [event["id"] for event in events]
    assert len(ids) == len(set(ids))
    assert all(event["id"].startswith("oversight:") for event in events)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_events_are_in_non_decreasing_time_order(fixture: Fixture) -> None:
    times = [event["time"] for event in fixture.run().events]
    assert times == sorted(times)


def test_declared_class_is_used_for_every_event() -> None:
    for event in _events("ticketing-approvals"):
        assert event["agentcesourceclass"] == "independent_system"
    for event in _events("disclosure-notices"):
        assert event["agentcesourceclass"] == "self_report"


def test_approval_request_and_decision() -> None:
    requested = _one("ticketing-approvals", "ApprovalRequested")
    assert requested["data"]["requested_from"] == "credit-committee"
    assert requested["data"]["deadline"] == "2026-05-05T17:00:00.000Z"
    decided = _one("ticketing-approvals", "ApprovalDecided")
    assert decided["data"]["outcome"] == "approve"
    assert decided["data"]["session_ref"] == "idp://okta/sessions/abc123"
    assert decided["data"]["latency_ms"] == 1800000
    assert decided["data"]["explanation_viewed"] is True


def test_the_human_actor_carries_role_and_authority() -> None:
    decided = _one("ticketing-approvals", "ApprovalDecided")
    assert decided["data"]["actor"] == {
        "id": "urn:example:person:credit-officer-7",
        "kind": "human",
        "role": "senior-credit-officer",
        "authority_ref": "policy://delegations/credit-committee",
        "org": "acme-bank",
    }


def test_override_and_interrupt() -> None:
    override = _one("workflow-oversight", "Override")
    assert override["data"]["original"] == "blob://decision/auto-approve"
    assert override["data"]["replacement"] == "blob://decision/manual-decline"
    assert override["data"]["reason_code"] == "policy_exception"
    interrupt = _one("workflow-oversight", "Interrupt")
    assert interrupt["data"]["mechanism"] == "kill_switch"
    assert interrupt["data"]["effect"] == "halted"


def test_notices() -> None:
    notices = _events("disclosure-notices")
    assert [n["data"]["notice_type"] for n in notices] == [
        "subject_of_ai_decision",
        "adverse_action",
    ]
    assert notices[0]["data"]["person_ref"] == "urn:example:person:applicant-42"


def test_convention_names_the_source_format_and_version() -> None:
    assert _fixture("ticketing-approvals").run().report.conventions == (
        "ticketing:2.1",
    )
    assert _fixture("workflow-oversight").run().report.conventions == (
        "workflow_engine:1.4",
    )
    assert _fixture("disclosure-notices").run().report.conventions == (
        "approval_gate:3.0",
    )


def test_source_is_derived_from_format_and_system() -> None:
    assert (
        _one("ticketing-approvals", "ApprovalDecided")["source"]
        == "urn:oversight:ticketing:jira"
    )


def test_records_that_are_incomplete_or_unknown_are_reported() -> None:
    report = _fixture("disclosure-notices").run().report
    assert report.records_seen == 5
    assert report.events_emitted == 2
    assert {(s.line, s.reason) for s in report.skipped} == {
        (3, "unknown_kind"),
        (4, "incomplete_record"),
        (5, "invalid_json"),
    }


def test_independent_system_requires_a_justification() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b"", subject="s", source_class="independent_system")
    assert excinfo.value.reason == "class_justification_required"
    # With a justification it is accepted.
    adapt(
        b"",
        subject="s",
        source_class="independent_system",
        class_justification="records kept externally",
    )


def test_self_report_needs_no_justification() -> None:
    result = adapt(b"", subject="s", source_class="self_report")
    assert result.events == []


def test_bad_source_class_is_rejected() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b"", subject="s", source_class="trusted")
    assert excinfo.value.reason == "bad_source_class"


def test_source_can_be_overridden() -> None:
    fixture = _fixture("workflow-oversight")
    result = adapt(
        fixture.input_bytes,
        **{**fixture.adapt_kwargs, "source": "urn:custom:oversight"},
    )
    assert {event["source"] for event in result.events} == {"urn:custom:oversight"}


def test_str_and_bytes_are_both_accepted() -> None:
    fixture = _fixture("ticketing-approvals")
    as_bytes = [
        canonical_string(e)
        for e in adapt(fixture.input_bytes, **fixture.adapt_kwargs).events
    ]
    as_str = [
        canonical_string(e)
        for e in adapt(fixture.input_bytes.decode(), **fixture.adapt_kwargs).events
    ]
    assert as_bytes == as_str


def test_approval_decided_needs_an_actor_and_a_session_ref() -> None:
    base = {
        "timestamp": "2026-05-05T09:00:00.000Z",
        "id": "d",
        "kind": "approval_decided",
        "source_format": "ticketing",
        "outcome": "approve",
    }
    actor = {"id": "p", "kind": "human", "role": "officer", "authority_ref": "a"}
    # Missing session_ref.
    no_session = adapt(json.dumps({**base, "actor": actor}), subject="s")
    assert no_session.report.skipped[0].reason == "incomplete_record"
    # Missing actor.
    no_actor = adapt(json.dumps({**base, "session_ref": "s"}), subject="s")
    assert no_actor.report.skipped[0].reason == "incomplete_record"
    # Both present -> emitted.
    ok = adapt(
        json.dumps({**base, "actor": actor, "session_ref": "idp://s"}), subject="s"
    )
    assert len(ok.events) == 1
