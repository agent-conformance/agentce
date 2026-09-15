"""Behavioural and contract tests for the policy-engines adapter (SPEC 12.1, 12.2)."""

from __future__ import annotations

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


def test_fixtures_present_one_per_engine() -> None:
    assert {f.name for f in FIXTURES} == {
        "cedar-authz",
        "governance-toolkit-audit",
        "opa-decisions",
        "openfga-checks",
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
def test_ids_are_unique_and_prefixed_by_engine(fixture: Fixture) -> None:
    engine = fixture.adapt_kwargs["engine"]
    events = fixture.run().events
    ids = [event["id"] for event in events]
    assert len(ids) == len(set(ids))
    assert all(event["id"].startswith(f"{engine}:") for event in events)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_events_are_in_non_decreasing_time_order(fixture: Fixture) -> None:
    times = [event["time"] for event in fixture.run().events]
    assert times == sorted(times)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_declared_trust_class_is_used_for_every_event(fixture: Fixture) -> None:
    for event in fixture.run().events:
        assert event["agentcesourceclass"] == "enforcement_point"


def test_openfga_check_becomes_an_authz_check() -> None:
    allowed = _events("openfga-checks")[0]
    assert allowed["data"]["@type"] == "AuthzCheck"
    assert allowed["data"]["user"] == "agent:credit-underwriter"
    assert allowed["data"]["relation"] == "can_read"
    assert allowed["data"]["object"] == "account:12345"
    assert allowed["data"]["allowed"] is True
    assert allowed["data"]["store_ref"] == "01H8FGA-STORE"


def test_openfga_denied_check_carries_allowed_false() -> None:
    denied = _events("openfga-checks")[1]
    assert denied["data"]["allowed"] is False


def test_openfga_incomplete_check_is_skipped() -> None:
    report = _fixture("openfga-checks").run().report
    assert [(s.line, s.reason) for s in report.skipped] == [(3, "incomplete_decision")]


def test_opa_result_boolean_maps_to_a_decision() -> None:
    events = _events("opa-decisions")
    assert events[0]["data"]["decision"] == "allow"
    assert events[0]["data"]["engine"] == "opa"
    assert events[0]["data"]["policy_id"] == "authz/tool_access"
    assert events[0]["data"]["policy_version"] == "bundle-42"
    assert events[1]["data"]["decision"] == "deny"
    assert events[1]["data"]["reasons"] == ["role_missing"]


def test_cedar_allow_and_deny_map_to_decisions() -> None:
    events = _events("cedar-authz")
    assert [e["data"]["decision"] for e in events] == ["allow", "deny"]
    assert all(e["data"]["engine"] == "cedar" for e in events)
    assert events[0]["data"]["principal"] == {
        "id": 'User::"alice"',
        "kind": "human",
        "role": "officer",
    }


def test_governance_toolkit_permit_maps_to_allow_with_obligations() -> None:
    permit = _events("governance-toolkit-audit")[0]
    assert permit["data"]["engine"] == "governance_toolkit"
    assert permit["data"]["decision"] == "allow"
    assert permit["data"]["obligations"] == ["log", "notify_dpo"]
    assert permit["data"]["policy_digest"] == "sha256:1f0a"


def test_convention_names_the_engine_and_version() -> None:
    assert _fixture("opa-decisions").run().report.conventions == ("opa:0.68",)
    assert _fixture("cedar-authz").run().report.conventions == ("cedar:4.2",)
    assert _fixture("openfga-checks").run().report.conventions == ("openfga:1.1",)
    assert _fixture("governance-toolkit-audit").run().report.conventions == (
        "governance_toolkit:1.0",
    )


def test_source_is_derived_from_engine_and_instance() -> None:
    assert (
        _events("cedar-authz")[0]["source"]
        == "urn:policy-engine:cedar:cedar-authorizer"
    )
    # OpenFGA derives the instance from the store id.
    assert (
        _events("openfga-checks")[0]["source"]
        == "urn:policy-engine:openfga:01H8FGA-STORE"
    )


def test_unknown_engine_is_rejected() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b"", engine="rego", subject="s")
    assert excinfo.value.reason == "unknown_engine"


def test_bad_source_class_is_rejected() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b"", engine="opa", subject="s", source_class="trusted")
    assert excinfo.value.reason == "bad_source_class"


def test_empty_log_yields_no_events() -> None:
    result = adapt(b"", engine="opa", subject="s")
    assert result.events == []
    assert result.report.entries_seen == 0


def test_source_can_be_overridden() -> None:
    fixture = _fixture("opa-decisions")
    result = adapt(
        fixture.input_bytes, engine="opa", subject="s", source="urn:custom:opa"
    )
    assert {event["source"] for event in result.events} == {"urn:custom:opa"}
