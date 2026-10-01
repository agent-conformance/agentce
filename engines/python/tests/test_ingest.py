"""Ingest: acceptance and every Phase-1 quarantine reason (SPEC §8.1, App. F)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentce.bundle import load_bundle
from agentce.ingest import ingest
from agentce.quarantine import QuarantineReason


def _ingest(make_bundle: Callable[..., Any], items: list[Any], **kwargs: Any) -> Any:
    return ingest(load_bundle(make_bundle(items, **kwargs)))


def test_accepts_valid_events(
    make_bundle: Callable[..., Any], example_events: list[dict[str, Any]]
) -> None:
    result = _ingest(make_bundle, example_events)
    assert len(result.accepted) == 2
    assert result.quarantined == []


def test_blank_lines_are_skipped(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    import json

    result = ingest(load_bundle(make_bundle(["", json.dumps(example_event), "   "])))
    assert len(result.accepted) == 1
    assert result.quarantined == []


def test_invalid_json(make_bundle: Callable[..., Any]) -> None:
    result = _ingest(make_bundle, ["{not json"])
    assert [q.reason for q in result.quarantined] == [QuarantineReason.SCHEMA_INVALID]


def test_schema_invalid_event(make_bundle: Callable[..., Any]) -> None:
    result = _ingest(make_bundle, [{"id": "x"}])
    assert result.accepted == []
    assert result.quarantined[0].reason == QuarantineReason.SCHEMA_INVALID


def test_unknown_type(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    bad = clone(example_event)
    bad["type"] = "org.agent-conformance.evidence.NotAType.v1"
    result = _ingest(make_bundle, [bad])
    assert result.quarantined[0].reason == QuarantineReason.UNKNOWN_TYPE


def test_duplicate_id(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    result = _ingest(make_bundle, [example_event, example_event])
    assert len(result.accepted) == 1
    assert result.quarantined[0].reason == QuarantineReason.DUPLICATE_ID


def test_time_order(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    first = clone(example_event)
    earlier = clone(example_event)
    earlier["id"] = "b" * 64
    earlier["time"] = "2020-01-01T00:00:00.000Z"
    result = _ingest(make_bundle, [first, earlier])
    assert len(result.accepted) == 1
    assert result.quarantined[0].reason == QuarantineReason.TIME_ORDER
    assert result.quarantined[0].stream


def test_unknown_source(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    result = _ingest(make_bundle, [example_event], sources=["urn:agentce:source:other"])
    assert result.quarantined[0].reason == QuarantineReason.UNKNOWN_SOURCE


def test_oversize(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    result = ingest(load_bundle(make_bundle([example_event])), max_event_bytes=10)
    assert result.quarantined[0].reason == QuarantineReason.OVERSIZE
    assert result.accepted == []


def test_class_mismatch(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    # The event is enforcement_point; the manifest declares the source as self_report (SPEC §6.4).
    source = str(example_event["source"])
    result = _ingest(
        make_bundle, [example_event], source_classes={source: "self_report"}
    )
    assert result.accepted == []
    assert result.quarantined[0].reason == QuarantineReason.CLASS_MISMATCH
    assert result.quarantined[0].source == source


def test_class_match_is_accepted(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    source = str(example_event["source"])
    result = _ingest(
        make_bundle, [example_event], source_classes={source: "enforcement_point"}
    )
    assert len(result.accepted) == 1
    assert result.quarantined == []


def test_source_declared_without_a_class_is_not_class_checked(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    source = str(example_event["source"])
    result = _ingest(make_bundle, [example_event], sources=[source])
    assert len(result.accepted) == 1
    assert result.quarantined == []


def _ingest_bytes(make_bundle: Callable[..., Any], body: bytes) -> Any:
    """Ingest a bundle whose one events file holds exactly ``body`` (any bytes, not only UTF-8)."""
    import hashlib
    import json

    root = make_bundle([])
    events = root / "events" / "stream.jsonl"
    events.write_bytes(body)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = hashlib.sha256(body).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return ingest(load_bundle(root))


def test_lines_end_at_lf_crlf_or_a_lone_cr(
    make_bundle: Callable[..., Any], example_events: list[dict[str, Any]]
) -> None:
    """The stream-line rule all three engines share (18.65): a lone CR ends a line as LF and CRLF
    do, so two events joined by a CR are two events, not one invalid line."""
    import json

    first, second = (json.dumps(e).encode() for e in example_events)
    for sep in (b"\n", b"\r\n", b"\r"):
        result = _ingest_bytes(make_bundle, first + sep + second + sep)
        assert len(result.accepted) == 2, sep
        assert result.quarantined == [], sep


def test_a_line_that_is_not_utf8_is_quarantined_alone(
    make_bundle: Callable[..., Any], example_events: list[dict[str, Any]]
) -> None:
    """Each line decodes on its own: an invalid UTF-8 line is one schema_invalid record, and the
    file's other lines are still read (18.65; Python used to crash on the whole file)."""
    import json

    first, second = (json.dumps(e).encode() for e in example_events)
    result = _ingest_bytes(make_bundle, first + b"\n\xff\xfe{}\n" + second + b"\n")
    assert len(result.accepted) == 2
    assert [(q.reason, q.detail) for q in result.quarantined] == [
        (QuarantineReason.SCHEMA_INVALID, "invalid UTF-8")
    ]


def test_a_leading_byte_order_mark_is_not_json_whitespace(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    """Only JSON's own whitespace is trimmed, so a BOM-prefixed line is not JSON in any engine."""
    import json

    result = _ingest_bytes(
        make_bundle, b"\xef\xbb\xbf" + json.dumps(example_event).encode()
    )
    assert result.accepted == []
    assert [q.reason for q in result.quarantined] == [QuarantineReason.SCHEMA_INVALID]


def test_nesting_past_the_limit_refuses_and_brackets_in_strings_do_not_count(
    make_bundle: Callable[..., Any],
) -> None:
    import pytest

    from agentce.errors import AgentceError
    from agentce.signing import MAX_JSON_DEPTH

    with pytest.raises(AgentceError) as exc:
        _ingest_bytes(make_bundle, b"[" * (MAX_JSON_DEPTH + 1) + b"\n")
    assert exc.value.key == "input.event_structure_too_deep"
    # Brackets inside a JSON string are text: the line parses and is quarantined as an event that
    # is not an object, not refused for depth.
    deep_text = b'"' + b"[" * (MAX_JSON_DEPTH + 1) + b'"\n'
    result = _ingest_bytes(make_bundle, deep_text)
    assert [q.detail for q in result.quarantined] == ["event is not a JSON object"]
