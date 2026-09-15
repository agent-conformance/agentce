"""Behavioural and contract tests for the mcp-gateway adapter (SPEC 12.1, 12.2).

The fixtures under ``fixtures/`` are the primary contract: each gateway log must map to its
``expected.jsonl`` byte-for-byte under the shared canonical form (SPEC 6.7) and produce its
``expected-manifest.json``. Alongside that, these tests assert the adapter-contract properties
directly -- deterministic ids, preserved timestamps, a declared trust class, the enforcement-point
authorization linkage, no invented events, no captured content -- so the suite proves the mapping is
correct and does not merely restate the fixtures.
"""

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


def _by_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event["data"]["@type"] == event_type]


def test_fixtures_present() -> None:
    assert {f.name for f in FIXTURES} == {
        "errors-and-skips",
        "resources-and-delegation",
        "tools-call-enforced",
    }


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_output_is_byte_identical_to_expected(fixture: Fixture) -> None:
    produced = fixture.run().events
    assert len(produced) == len(fixture.expected), fixture.name
    for event, expected in zip(produced, fixture.expected, strict=True):
        assert canonical_string(event) == canonical_string(expected)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_source_manifest_matches_expected(fixture: Fixture) -> None:
    assert fixture.run().source_manifest == fixture.expected_manifest


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_manifest_counts_equal_emitted_events(fixture: Fixture) -> None:
    result = fixture.run()
    counts: dict[str, int] = {}
    for event in result.events:
        counts[event["data"]["@type"]] = counts.get(event["data"]["@type"], 0) + 1
    assert result.source_manifest["counts"] == counts


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
def test_ids_are_unique_and_derive_from_the_span(fixture: Fixture) -> None:
    events = fixture.run().events
    ids = [event["id"] for event in events]
    assert len(ids) == len(set(ids))
    for event in events:
        assert event["id"].startswith(
            f"mcp:{event['agentcetrace']}/{event['agentcespan']}"
        )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_events_are_in_non_decreasing_time_order(fixture: Fixture) -> None:
    times = [event["time"] for event in fixture.run().events]
    assert times == sorted(times)


def test_source_timestamp_is_preserved() -> None:
    tool = _by_type(_events("tools-call-enforced"), "ToolCall")[0]
    assert tool["time"] == "2026-05-01T09:00:01.000Z"


def test_declared_trust_class_is_used_for_every_event() -> None:
    for name in FIXTURES_BY_NAME:
        for event in _events(name):
            assert event["agentcesourceclass"] == "enforcement_point"


def test_a_tool_call_references_the_policy_decision_that_authorised_it() -> None:
    events = _events("tools-call-enforced")
    tool = _by_type(events, "ToolCall")[0]
    policy_id = tool["data"]["refs"]["authorization"]
    # The referenced authorization is a real PolicyDecision event in the same bundle.
    policy_events = {
        f"agentce:event/{e['id']}" for e in _by_type(events, "PolicyDecision")
    }
    assert policy_id in policy_events


def test_a_policy_decision_references_the_action_it_decided() -> None:
    events = _events("tools-call-enforced")
    allowed_tool = _by_type(events, "ToolCall")[0]
    policy = next(
        e
        for e in _by_type(events, "PolicyDecision")
        if e["data"].get("refs", {}).get("request")
        == f"agentce:event/{allowed_tool['id']}"
    )
    assert policy["data"]["decision"] == "allow"


def test_a_denied_call_yields_a_policy_decision_but_no_tool_call() -> None:
    events = _events("tools-call-enforced")
    denied = [
        e for e in _by_type(events, "PolicyDecision") if e["data"]["decision"] == "deny"
    ]
    assert len(denied) == 1
    # The denied tool's span produced no ToolCall (the gateway blocked it).
    denied_span = denied[0]["agentcespan"]
    tool_spans = {e["agentcespan"] for e in _by_type(events, "ToolCall")}
    assert denied_span not in tool_spans
    assert denied[0]["data"]["reasons"] == ["scope_exceeded", "no_approval"]
    assert denied[0]["data"]["obligations"] == ["notify_officer"]


def test_delegation_chain_carries_typed_principals() -> None:
    delegation = _by_type(_events("resources-and-delegation"), "DelegationIssued")[0][
        "data"
    ]
    assert delegation["chain"] == [
        {"id": "spiffe://corp/services/credit-orchestrator", "kind": "service"},
        {
            "id": "urn:example:person:credit-officer-7",
            "kind": "human",
            "role": "officer",
        },
    ]
    assert delegation["scope_granted"] == ["bureau:read", "policies:read"]
    assert delegation["verification"] == {"status": "verified", "method": "jwt-sig"}


def test_a_resource_read_without_a_policy_has_no_authorization_ref() -> None:
    reads = _by_type(_events("resources-and-delegation"), "ResourceAccess")
    unauthorised = [e for e in reads if "refs" not in e["data"]]
    assert len(unauthorised) == 1
    assert unauthorised[0]["data"]["resource"]["uri"].endswith("appendix.pdf")


def test_error_response_becomes_an_error_member() -> None:
    tool = _by_type(_events("errors-and-skips"), "ToolCall")[0]
    assert tool["data"]["error"] == "upstream timeout"


def test_effect_classification_is_carried_from_the_gateway() -> None:
    external = next(
        e
        for e in _by_type(_events("tools-call-enforced"), "ToolCall")
        if e["data"]["tool"]["name"] == "send_adverse_action_notice"
    )
    assert external["data"]["side_effect"] == "external"
    assert external["data"]["effect_class"] == "external_communication"


def test_malformed_and_unmapped_entries_are_reported_never_invented() -> None:
    report = _fixture("errors-and-skips").run().report
    assert report.entries_seen == 5
    assert report.events_emitted == 2
    reasons = {skip.reason for skip in report.skipped}
    assert reasons == {
        "unmapped_entry",
        "invalid_json",
        "not_an_object",
        "missing_envelope",
    }


def test_convention_is_detected_per_entry() -> None:
    assert _fixture("tools-call-enforced").run().report.conventions == (
        "mcp:2025-06-18",
    )
    assert _fixture("resources-and-delegation").run().report.conventions == (
        "mcp:2024-11-05",
    )


def test_content_is_referenced_not_captured() -> None:
    # An entry with inline argument/result content yields a locator, and the content never appears.
    log = json.dumps(
        {
            "timestamp": "2026-05-01T09:00:00.000Z",
            "gateway": "g",
            "trace_id": "0" * 32,
            "span_id": "1" * 16,
            "request": {
                "method": "tools/call",
                "params": {"name": "t", "arguments": {"pii": "SECRET"}},
            },
            "response": {"outcome": "ok", "result": {"balance": "SECRET"}},
        }
    )
    event = adapt(log, subject="s").events[0]
    assert (
        event["data"]["args_ref"]
        == "mcp:00000000000000000000000000000000/1111111111111111#args"
    )
    assert (
        event["data"]["result_ref"]
        == "mcp:00000000000000000000000000000000/1111111111111111#result"
    )
    assert "SECRET" not in canonical_string(event)


def test_source_is_derived_from_the_gateway_id() -> None:
    for event in _events("tools-call-enforced"):
        assert event["source"] == "urn:mcp-gateway:mcp-gateway-prod"


def test_source_can_be_overridden() -> None:
    fixture = _fixture("tools-call-enforced")
    result = adapt(fixture.input_bytes, subject="s", source="urn:custom:gw")
    assert {event["source"] for event in result.events} == {"urn:custom:gw"}


def test_bad_source_class_is_rejected() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b"", subject="s", source_class="trusted")
    assert excinfo.value.reason == "bad_source_class"


def test_empty_log_yields_no_events() -> None:
    result = adapt(b"", subject="s")
    assert result.events == []
    assert result.report.entries_seen == 0
    assert result.source_manifest["counts"] == {}


def test_str_and_bytes_are_both_accepted() -> None:
    fixture = _fixture("tools-call-enforced")
    as_bytes = [
        canonical_string(e) for e in adapt(fixture.input_bytes, subject="s").events
    ]
    as_str = [
        canonical_string(e)
        for e in adapt(fixture.input_bytes.decode(), subject="s").events
    ]
    assert as_bytes == as_str
