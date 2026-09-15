"""The adapter parses untrusted, sometimes malformed, telemetry; it must degrade, not crash.

These tests exercise the OTLP/JSON decoding helpers and the tolerance of the span walker to missing
or wrong-typed structure. A malformed span is skipped or ignored, never allowed to raise.
"""

from __future__ import annotations

import json
from typing import Any

from agentce_adapters import adapt
from agentce_adapters.otel_genai import (
    _any_value,
    _as_int,
    _as_str,
    _attributes,
    _otel_genai_version,
    _rfc3339_millis,
)


def test_any_value_decodes_each_otlp_scalar_kind() -> None:
    assert _any_value({"stringValue": "x"}) == "x"
    assert _any_value({"intValue": "42"}) == 42
    assert _any_value({"intValue": 42}) == 42
    assert _any_value({"boolValue": True}) is True
    assert _any_value({"doubleValue": 1.5}) == 1.5
    assert _any_value(
        {"arrayValue": {"values": [{"stringValue": "a"}, {"intValue": "2"}]}}
    ) == ["a", 2]
    assert _any_value(
        {"kvlistValue": {"values": [{"key": "k", "value": {"stringValue": "v"}}]}}
    ) == {"k": "v"}


def test_any_value_tolerates_junk() -> None:
    assert _any_value("not a dict") is None
    assert _any_value({"bytesValue": "unhandled"}) is None
    assert _any_value({"arrayValue": "not a dict"}) == []
    assert _any_value({"kvlistValue": {}}) == {}


def test_attributes_ignores_malformed_entries() -> None:
    raw: list[Any] = [
        {"key": "good", "value": {"stringValue": "yes"}},
        "not-a-dict",
        {"key": 123, "value": {"stringValue": "bad-key"}},
        {"no-key": True},
    ]
    assert _attributes(raw) == {"good": "yes"}
    assert _attributes("not a list") == {}


def test_as_str_and_as_int_edge_cases() -> None:
    assert _as_str("") is None
    assert _as_str(5) is None
    assert _as_int(True) is None
    assert _as_int("nope") is None
    assert _as_int(7) == 7
    assert _as_int("7") == 7
    assert _as_int(1.5) is None


def test_rfc3339_rejects_bad_timestamps() -> None:
    assert _rfc3339_millis(None) is None
    assert _rfc3339_millis(-1) is None
    assert _rfc3339_millis("not-a-number") is None
    assert _rfc3339_millis(0) == "1970-01-01T00:00:00.000Z"


def test_otel_genai_version_parsing() -> None:
    assert _otel_genai_version("https://opentelemetry.io/schemas/1.36.0") == "1.36"
    assert (
        _otel_genai_version(None, "https://opentelemetry.io/schemas/1.38.0") == "1.38"
    )
    assert _otel_genai_version("https://example.test/no-version") is None
    assert _otel_genai_version(None) is None


def _one_span(
    attributes: list[dict[str, Any]], *, scope: dict[str, Any] | None = None
) -> str:
    return json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "scope": scope or {"name": "x"},
                            "schemaUrl": "https://opentelemetry.io/schemas/1.36.0",
                            "spans": [
                                {
                                    "traceId": "0" * 32,
                                    "spanId": "1" * 16,
                                    "name": "span",
                                    "startTimeUnixNano": "1000000",
                                    "endTimeUnixNano": "2000000",
                                    "attributes": attributes,
                                    "status": {},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )


def test_source_falls_back_when_service_name_is_absent() -> None:
    otlp = _one_span(
        [{"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}}]
    )
    event = adapt(otlp, subject="s").events[0]
    assert event["source"] == "urn:otel:unknown"


def test_task_attribute_is_carried_when_present() -> None:
    otlp = _one_span(
        [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
            {"key": "a2a.task.id", "value": {"stringValue": "task-42"}},
        ]
    )
    assert adapt(otlp, subject="s").events[0]["agentcetask"] == "task-42"


def test_unknown_openinference_kind_is_skipped() -> None:
    otlp = _one_span(
        [{"key": "openinference.span.kind", "value": {"stringValue": "CHAIN"}}],
        scope={"name": "openinference.instrumentation.x", "version": "0.1.14"},
    )
    result = adapt(otlp, subject="s")
    assert result.events == []
    assert result.report.skipped[0].reason == "unrecognised_operation"


def test_openinference_without_a_version_is_still_named() -> None:
    otlp = _one_span(
        [
            {"key": "openinference.span.kind", "value": {"stringValue": "LLM"}},
            {"key": "llm.model_name", "value": {"stringValue": "m"}},
        ],
        scope={"name": "openinference.instrumentation.x"},
    )
    assert adapt(otlp, subject="s").events[0]["agentceconv"] == "openinference"


def test_otel_without_a_schema_url_is_still_named() -> None:
    otlp = json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "scope": {"name": "x"},
                            "spans": [
                                {
                                    "traceId": "0" * 32,
                                    "spanId": "1" * 16,
                                    "name": "chat",
                                    "startTimeUnixNano": "1000000",
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
    assert adapt(otlp, subject="s").events[0]["agentceconv"] == "otel-genai"


def test_invalid_enum_valued_attributes_are_dropped() -> None:
    # A tool protocol or memory trust value outside the model's enum is not carried through.
    otlp = _one_span(
        [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
            {"key": "gen_ai.tool.name", "value": {"stringValue": "t"}},
            {"key": "gen_ai.tool.protocol", "value": {"stringValue": "carrier-pigeon"}},
        ]
    )
    tool = adapt(otlp, subject="s").events[0]["data"]["tool"]
    assert "protocol" not in tool


def test_memory_write_without_optional_members() -> None:
    otlp = _one_span(
        [{"key": "gen_ai.operation.name", "value": {"stringValue": "memory.write"}}]
    )
    event = adapt(otlp, subject="s").events[0]
    assert event["data"]["@type"] == "MemoryWrite"
    assert set(event["data"]) == {"@context", "@type"}


def test_memory_read_ignores_non_list_record_ids() -> None:
    otlp = _one_span(
        [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "memory.read"}},
            {"key": "gen_ai.memory.record.ids", "value": {"stringValue": "not-a-list"}},
        ]
    )
    event = adapt(otlp, subject="s").events[0]
    assert "record_refs" not in event["data"]


def test_status_ok_with_a_message_is_not_an_error() -> None:
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
                                    "startTimeUnixNano": "1000000",
                                    "endTimeUnixNano": "2000000",
                                    "attributes": [
                                        {
                                            "key": "gen_ai.operation.name",
                                            "value": {"stringValue": "chat"},
                                        }
                                    ],
                                    "status": {"code": 1, "message": "ok-ish"},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    assert "error" not in adapt(otlp, subject="s").events[0]["data"]


def test_malformed_span_containers_are_tolerated() -> None:
    otlp = json.dumps(
        {
            "resourceSpans": [
                "not-a-dict",
                {"resource": {"attributes": []}, "scopeSpans": "not-a-list"},
                {"resource": {"attributes": []}, "scopeSpans": ["not-a-dict"]},
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [{"scope": {"name": "x"}, "spans": "no"}],
                },
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [{"scope": {"name": "x"}, "spans": ["no"]}],
                },
            ]
        }
    )
    result = adapt(otlp, subject="s")
    assert result.events == []
    assert result.report.spans_seen == 0
