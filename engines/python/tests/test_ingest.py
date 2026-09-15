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
