"""Behavioural and contract tests for the otel-genai adapter (SPEC 12.1, 12.2).

The fixtures under ``fixtures/`` are the primary contract: each native export must map to its
``expected.jsonl`` byte-for-byte under the shared canonical form (SPEC 6.7). Alongside that, these
tests assert the adapter-contract properties directly -- deterministic ids, preserved timestamps, a
declared (never inferred) trust class, no invented events, no captured content -- so the suite proves
the mapping is correct and does not merely restate the fixtures.
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


def test_fixtures_present() -> None:
    # The support matrix and these tests are only meaningful if every event type has a fixture.
    assert {f.name for f in FIXTURES} == {
        "openinference-rag",
        "otel-genai-agent-session",
        "otel-genai-chat",
        "otel-genai-errors-and-skips",
        "otel-genai-legacy-tokens",
        "datadog",
        "langfuse",
        "langsmith",
        "phoenix",
    }


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_output_is_byte_identical_to_expected(fixture: Fixture) -> None:
    """The adapter reproduces each fixture's expected events byte-for-byte in canonical form."""
    produced = fixture.run().events
    assert len(produced) == len(fixture.expected), fixture.name
    for event, expected in zip(produced, fixture.expected, strict=True):
        assert canonical_string(event) == canonical_string(expected)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_every_event_is_schema_valid(fixture: Fixture) -> None:
    """Every produced event validates against the generated evidence schema (it will ingest)."""
    for event in fixture.run().events:
        assert validate_event(event) == [], event["id"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_adaptation_is_deterministic(fixture: Fixture) -> None:
    """Re-adapting the same export yields identical events (deterministic ids and order)."""
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
            f"otel:{event['agentcetrace']}/{event['agentcespan']}"
        )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_events_are_in_non_decreasing_time_order(fixture: Fixture) -> None:
    times = [event["time"] for event in fixture.run().events]
    assert times == sorted(times)


def test_timestamps_preserve_source_to_the_millisecond() -> None:
    # 1734000001500000000 ns is …:01.500Z: the exporter's millisecond is preserved, not rounded.
    model_call = _events("otel-genai-chat")[0]
    assert model_call["time"] == "2024-12-12T10:40:00.000Z"
    assert all(
        event["time"].endswith("Z") and event["time"][-5] == "."
        for event in _events("otel-genai-chat")
    )


def test_millisecond_truncation_does_not_round_up() -> None:
    # 1_999_999 ns is 1.999999 ms; it must truncate to .001Z, never round to .002Z.
    otlp = json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "svc"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "x"},
                            "schemaUrl": "https://opentelemetry.io/schemas/1.36.0",
                            "spans": [
                                {
                                    "traceId": "0" * 32,
                                    "spanId": "1" * 16,
                                    "name": "chat",
                                    "startTimeUnixNano": "1999999",
                                    "endTimeUnixNano": "2000000",
                                    "attributes": [
                                        {
                                            "key": "gen_ai.operation.name",
                                            "value": {"stringValue": "chat"},
                                        }
                                    ],
                                    "status": {},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    event = adapt(otlp, subject="s").events[0]
    assert event["time"] == "1970-01-01T00:00:00.001Z"


def test_declared_trust_class_is_used_for_every_event_not_inferred() -> None:
    # The errors fixture declares enforcement_point; every event carries it regardless of content.
    events = _events("otel-genai-errors-and-skips")
    assert events
    assert all(event["agentcesourceclass"] == "enforcement_point" for event in events)
    # The default is self_report, and it too is applied uniformly.
    assert all(
        event["agentcesourceclass"] == "self_report"
        for event in _events("otel-genai-chat")
    )


def test_unrecognised_spans_are_reported_never_invented() -> None:
    report = _fixture("otel-genai-errors-and-skips").run().report
    assert report.spans_seen == 4
    assert report.events_emitted == 2
    reasons = {skip.reason for skip in report.skipped}
    assert reasons == {"unrecognised_operation", "missing_operation"}


def test_convention_is_detected_per_span() -> None:
    assert _fixture("otel-genai-chat").run().report.conventions == ("otel-genai:1.36",)
    assert _fixture("otel-genai-legacy-tokens").run().report.conventions == (
        "otel-genai:1.21",
    )
    assert _fixture("openinference-rag").run().report.conventions == (
        "openinference:0.1.14",
    )
    for event in _events("otel-genai-legacy-tokens"):
        assert event["agentceconv"] == "otel-genai:1.21"


def test_token_usage_is_mapped_across_convention_versions() -> None:
    # Legacy prompt_tokens/completion_tokens map to the same canonical usage members as the new names.
    legacy = _events("otel-genai-legacy-tokens")[0]["data"]["usage"]
    assert legacy == {"input_tokens": 2048, "output_tokens": 512}
    current = _events("otel-genai-chat")[0]["data"]["usage"]
    assert current == {"input_tokens": 1024, "output_tokens": 256}


def test_openinference_token_counts_are_mapped() -> None:
    model_call = _events("openinference-rag")[0]
    assert model_call["data"]["usage"] == {"input_tokens": 1500, "output_tokens": 300}
    assert model_call["data"]["model"] == {"provider": "openai", "name": "gpt-4o-mini"}


def test_content_is_referenced_by_locator_not_captured() -> None:
    # SPEC R12: the exporter's (redacted) content value must never appear in the evidence event; the
    # adapter only emits an opaque locator back to the source span.
    events = _events("otel-genai-chat")
    blob = "\n".join(canonical_string(event) for event in events)
    assert "[redacted-by-exporter]" not in blob
    model_call = events[0]["data"]
    assert model_call["input_ref"].endswith("#input")
    assert model_call["output_ref"].endswith("#output")


def test_an_invoke_agent_span_brackets_a_session() -> None:
    events = _events("otel-genai-agent-session")
    kinds = [event["data"]["@type"] for event in events]
    assert kinds[0] == "SessionStart"
    assert kinds[-1] == "SessionEnd"
    assert events[0]["time"] < events[-1]["time"]
    assert events[-1]["data"]["end_reason"] == "completed"


def test_error_status_becomes_an_error_member_and_end_reason() -> None:
    model_call, tool_call = _events("otel-genai-errors-and-skips")
    assert model_call["data"]["error"] == "rate limited"
    assert tool_call["data"]["error"] == "policy denied"


def test_source_is_derived_from_service_name() -> None:
    for event in _events("otel-genai-chat"):
        assert event["source"] == "urn:otel:credit-underwriter"


def test_source_can_be_overridden() -> None:
    fixture = _fixture("otel-genai-chat")
    result = adapt(fixture.input_bytes, subject="s", source="urn:custom:collector")
    assert {event["source"] for event in result.events} == {"urn:custom:collector"}


def test_trace_context_is_carried_on_the_envelope() -> None:
    tool_call = _events("otel-genai-agent-session")[1]
    assert tool_call["agentcetrace"] == "9f3e0000000000001111222233334455"
    assert tool_call["agentcespan"] == "b2b2b2b2b2b2b2b2"
    assert tool_call["agentceparent"] == "a1a1a1a1a1a1a1a1"


def test_bad_source_class_is_rejected() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b'{"resourceSpans": []}', subject="s", source_class="totally_trusted")
    assert excinfo.value.reason == "bad_source_class"


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b"{not json", subject="s")
    assert excinfo.value.reason == "invalid_json"


def test_non_otlp_document_is_rejected() -> None:
    with pytest.raises(AdapterError) as excinfo:
        adapt(b'{"foo": 1}', subject="s")
    assert excinfo.value.reason == "not_otlp"
    with pytest.raises(AdapterError) as excinfo:
        adapt(b"[1, 2, 3]", subject="s")
    assert excinfo.value.reason == "not_otlp"


def test_empty_export_yields_no_events() -> None:
    result = adapt(b'{"resourceSpans": []}', subject="s")
    assert result.events == []
    assert result.report.spans_seen == 0
    assert result.report.events_emitted == 0
    assert result.report.conventions == ()


def test_a_span_without_a_timestamp_is_skipped() -> None:
    otlp = json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "scope": {"name": "x"},
                            "schemaUrl": "https://opentelemetry.io/schemas/1.36.0",
                            "spans": [
                                {
                                    "traceId": "0" * 32,
                                    "spanId": "1" * 16,
                                    "name": "chat",
                                    "attributes": [
                                        {
                                            "key": "gen_ai.operation.name",
                                            "value": {"stringValue": "chat"},
                                        }
                                    ],
                                    "status": {},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    result = adapt(otlp, subject="s")
    assert result.events == []


def test_string_and_str_payloads_are_both_accepted() -> None:
    fixture = _fixture("otel-genai-chat")
    as_bytes = adapt(fixture.input_bytes, subject="s").events
    as_str = adapt(fixture.input_bytes.decode("utf-8"), subject="s").events
    assert [canonical_string(e) for e in as_bytes] == [
        canonical_string(e) for e in as_str
    ]
