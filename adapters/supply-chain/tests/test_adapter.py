"""Behavioural and contract tests for the supply-chain adapter (SPEC 12.1, 12.2)."""

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


def _by_id(name: str, event_id: str) -> dict[str, Any]:
    return next(e for e in _events(name) if e["id"] == event_id)


def test_fixtures_present() -> None:
    assert {f.name for f in FIXTURES} == {"attestations", "bundle-load"}


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
    assert all(event["id"].startswith("supply-chain:") for event in events)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_events_are_in_non_decreasing_time_order(fixture: Fixture) -> None:
    times = [event["time"] for event in fixture.run().events]
    assert times == sorted(times)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_declared_class_is_used_for_every_event(fixture: Fixture) -> None:
    for event in fixture.run().events:
        assert event["agentcesourceclass"] == "enforcement_point"


def test_a_valid_attestation_verifies() -> None:
    att = _by_id("attestations", "supply-chain:att-1")
    assert att["data"]["@type"] == "Attestation"
    assert att["data"]["verification"] == {
        "status": "verified",
        "method": "sigstore-bundle",
        "log_ref": "rekor://index/12345",
    }


def test_a_tampered_subject_digest_fails_verification() -> None:
    att = _by_id("attestations", "supply-chain:att-2")
    assert att["data"]["verification"]["status"] == "failed"


def test_an_unsigned_attestation_is_unverified() -> None:
    att = _by_id("attestations", "supply-chain:att-3")
    assert att["data"]["verification"]["status"] == "unverified"


def test_an_untrusted_signer_fails_verification() -> None:
    att = _by_id("attestations", "supply-chain:att-4")
    assert att["data"]["verification"]["status"] == "failed"


def test_bundle_loaded_maps_components() -> None:
    bl = _by_id("bundle-load", "supply-chain:bl-1")
    assert bl["data"]["@type"] == "BundleLoaded"
    assert bl["data"]["bundle_digest"] == "sha256:bundle-1"
    kinds = [c["kind"] for c in bl["data"]["components"]]
    assert kinds == ["skill", "mcp_server", "model"]
    assert bl["data"]["attestation_refs"] == ["att-1", "att-2"]


def test_cyclonedx_bom_becomes_a_bundle_loaded() -> None:
    bl = _by_id("bundle-load", "supply-chain:bl-2")
    assert bl["agentceconv"] == "cyclonedx:1.6"
    assert [c["kind"] for c in bl["data"]["components"]] == ["config", "policy"]


def test_conventions_name_the_formats() -> None:
    assert _fixture("attestations").run().report.conventions == (
        "in-toto:1.0",
        "sigstore:0.3",
    )
    assert _fixture("bundle-load").run().report.conventions == (
        "bundle-load:1",
        "cyclonedx:1.6",
    )


def test_verification_defaults_to_failed_without_trusted_keys() -> None:
    # A signed attestation whose key is not in an (empty) trusted set fails.
    record = {
        "timestamp": "2026-05-08T09:00:00.000Z",
        "id": "x",
        "kind": "attestation",
        "format": "sigstore",
        "subject_digests": ["sha256:a"],
        "signature": {"key_id": "key-1"},
    }
    result = adapt(json.dumps(record), subject="s")
    assert result.events[0]["data"]["verification"]["status"] == "failed"


def test_an_invalid_component_kind_is_dropped() -> None:
    record = {
        "timestamp": "2026-05-08T10:00:00.000Z",
        "id": "b",
        "kind": "bundle_loaded",
        "format": "bundle-load",
        "components": [{"kind": "wormhole", "name": "x", "digest": "sha256:1"}],
    }
    bl = adapt(json.dumps(record), subject="s").events[0]
    assert "kind" not in bl["data"]["components"][0]
    assert bl["data"]["components"][0]["name"] == "x"


def test_bad_source_class_is_rejected() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b"", subject="s", source_class="trusted")
    assert excinfo.value.reason == "bad_source_class"


def test_source_can_be_overridden() -> None:
    fixture = _fixture("bundle-load")
    result = adapt(
        fixture.input_bytes, **{**fixture.adapt_kwargs, "source": "urn:custom:sc"}
    )
    assert {event["source"] for event in result.events} == {"urn:custom:sc"}


def test_empty_log_yields_no_events() -> None:
    result = adapt(b"", subject="s")
    assert result.events == []
    assert result.report.records_seen == 0
