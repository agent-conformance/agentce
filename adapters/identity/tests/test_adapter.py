"""Behavioural and contract tests for the identity adapter (SPEC 12.1, 12.2, v1.1)."""

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


def test_fixtures_present() -> None:
    assert {f.name for f in FIXTURES} == {"idp-issuance", "token-exchange"}


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
        assert event["data"]["@type"] == "DelegationIssued"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_adaptation_is_deterministic(fixture: Fixture) -> None:
    first = [canonical_string(e) for e in fixture.run().events]
    second = [canonical_string(e) for e in fixture.run().events]
    assert first == second


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_ids_are_unique_and_prefixed(fixture: Fixture) -> None:
    events = fixture.run().events
    assert len({e["id"] for e in events}) == len(events)
    assert all(e["id"].startswith("identity:") for e in events)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_declared_class_is_used(fixture: Fixture) -> None:
    for event in fixture.run().events:
        assert event["agentcesourceclass"] == "enforcement_point"


def test_token_exchange_carries_the_chain_and_scopes() -> None:
    event = _events("token-exchange")[0]["data"]
    assert event["token_ref"] == "jwt://exchange/1"
    assert event["subject_principal"] == "spiffe://corp/services/credit-orchestrator"
    assert event["actor_principal"] == "spiffe://corp/agents/credit-underwriter"
    assert event["chain"] == [
        {"id": "spiffe://corp/services/credit-orchestrator", "kind": "service"},
        {
            "id": "urn:example:person:credit-officer-7",
            "kind": "human",
            "role": "officer",
        },
    ]
    assert event["scope_granted"] == ["bureau:read", "policies:read"]
    assert event["scope_parent"] == ["bureau:read", "policies:read", "ledger:write"]
    assert event["verification"] == {
        "status": "verified",
        "method": "jwt-sig",
        "log_ref": "idp://audit/1",
    }


def test_idp_issuance_maps_and_skips_incomplete() -> None:
    report = _fixture("idp-issuance").run().report
    assert report.events_emitted == 1
    assert [(s.line, s.reason) for s in report.skipped] == [(2, "incomplete_record")]
    issued = _events("idp-issuance")[0]["data"]
    assert issued["token_ref"] == "jwt://issue/9"
    assert issued["chain"] == [
        {
            "id": "urn:example:person:credit-officer-7",
            "kind": "human",
            "role": "officer",
        }
    ]


def test_conventions_name_the_formats() -> None:
    assert _fixture("token-exchange").run().report.conventions == (
        "token-exchange:oauth-8693",
    )
    assert _fixture("idp-issuance").run().report.conventions == (
        "idp-issuance:oidc-1.0",
    )


def test_source_is_derived_from_system_or_issuer() -> None:
    assert _events("token-exchange")[0]["source"] == "urn:identity:okta"
    # idp-issuance has no system, so the issuer is used.
    assert _events("idp-issuance")[0]["source"] == "urn:identity:https://idp.corp"


def test_a_record_without_a_token_ref_is_incomplete() -> None:
    record = {
        "timestamp": "2026-05-09T09:00:00.000Z",
        "id": "x",
        "format": "idp-issuance",
        "issuer": "i",
    }
    result = adapt(json.dumps(record), subject="s")
    assert result.events == []
    assert result.report.skipped[0].reason == "incomplete_record"


def test_bad_source_class_is_rejected() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b"", subject="s", source_class="trusted")
    assert excinfo.value.reason == "bad_source_class"


def test_source_can_be_overridden() -> None:
    fixture = _fixture("token-exchange")
    result = adapt(fixture.input_bytes, subject="s", source="urn:custom:idp")
    assert {e["source"] for e in result.events} == {"urn:custom:idp"}


def test_empty_log_yields_no_events() -> None:
    result = adapt(b"", subject="s")
    assert result.events == []
    assert result.report.records_seen == 0
