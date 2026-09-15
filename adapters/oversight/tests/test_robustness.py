"""The adapter parses untrusted oversight logs; malformed or partial records degrade, never crash."""

from __future__ import annotations

import json
from typing import Any

from agentce_adapters import adapt
from agentce_adapters.oversight import _human_actor


def _record(**fields: Any) -> str:
    base = {
        "timestamp": "2026-05-05T09:00:00.000Z",
        "id": "r",
        "source_format": "ticketing",
    }
    return json.dumps({**base, **fields})


def test_human_actor_requires_id_role_and_authority() -> None:
    full = {
        "id": "p",
        "kind": "human",
        "role": "officer",
        "authority_ref": "a",
        "org": "acme",
    }
    assert _human_actor(full) == full
    assert (
        _human_actor({"id": "p", "kind": "human", "role": "officer"}) is None
    )  # no authority_ref
    assert _human_actor({"id": "p", "role": "officer", "authority_ref": "a"}) == {
        "id": "p",
        "kind": "human",  # kind defaults to human
        "role": "officer",
        "authority_ref": "a",
    }
    assert (
        _human_actor({"id": "p", "kind": "martian", "role": "r", "authority_ref": "a"})
        is None
    )
    assert _human_actor("not-a-dict") is None


def test_each_missing_envelope_field_skips_the_record() -> None:
    for field in ("timestamp", "id", "source_format"):
        record = json.loads(
            _record(kind="notice", person_ref="x", notice_type="explanation")
        )
        del record[field]
        result = adapt(json.dumps(record), subject="s")
        assert result.events == []
        assert result.report.skipped[0].reason == "missing_envelope"


def test_an_unknown_source_format_is_skipped() -> None:
    result = adapt(_record(kind="notice", source_format="carrier_pigeon"), subject="s")
    # source_format is validated in the envelope, so an unknown one is missing_envelope.
    assert result.report.skipped[0].reason == "missing_envelope"


def test_an_unknown_kind_is_skipped() -> None:
    result = adapt(_record(kind="ponder"), subject="s")
    assert result.report.skipped[0].reason == "unknown_kind"


def test_invalid_enum_values_are_dropped() -> None:
    actor = {"id": "p", "kind": "human", "role": "r", "authority_ref": "a"}
    interrupt = adapt(
        _record(kind="interrupt", actor=actor, mechanism="teleport", effect="vanished"),
        subject="s",
    ).events[0]
    assert "mechanism" not in interrupt["data"]
    assert "effect" not in interrupt["data"]
    notice = adapt(_record(kind="notice", notice_type="gossip"), subject="s").events[0]
    assert "notice_type" not in notice["data"]


def test_id_falls_back_to_trace_and_span() -> None:
    record = {
        "timestamp": "2026-05-05T09:00:00.000Z",
        "source_format": "ticketing",
        "kind": "notice",
        "trace_id": "0" * 32,
        "span_id": "1" * 16,
        "notice_type": "explanation",
    }
    event = adapt(json.dumps(record), subject="s").events[0]
    assert event["id"] == "oversight:" + "0" * 32 + "/" + "1" * 16
    assert event["agentcetrace"] == "0" * 32


def test_trace_context_is_carried() -> None:
    event = adapt(
        _record(
            kind="notice",
            notice_type="explanation",
            trace_id="a" * 32,
            span_id="b" * 16,
            parent_span_id="c" * 16,
            task_id="t",
        ),
        subject="s",
    ).events[0]
    assert event["agentceparent"] == "c" * 16
    assert event["agentcetask"] == "t"


def test_non_object_and_invalid_json_lines_are_skipped() -> None:
    log = '"a string"\n{bad\n' + _record(kind="notice", notice_type="explanation")
    result = adapt(log, subject="s")
    assert len(result.events) == 1
    assert {s.reason for s in result.report.skipped} == {
        "not_an_object",
        "invalid_json",
    }


def test_blank_lines_are_ignored() -> None:
    result = adapt(
        "\n\n" + _record(kind="notice", notice_type="explanation") + "\n\n", subject="s"
    )
    assert result.report.records_seen == 1
    assert len(result.events) == 1
