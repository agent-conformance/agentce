"""The adapter parses untrusted identity logs; malformed or partial records degrade, never crash."""

from __future__ import annotations

import json
from typing import Any

from agentce_adapters import adapt
from agentce_adapters.identity import _principal, _verification


def _record(**fields: Any) -> str:
    base = {
        "timestamp": "2026-05-09T09:00:00.000Z",
        "id": "r",
        "format": "token-exchange",
    }
    return json.dumps({**base, **fields})


def test_principal_requires_id_and_valid_kind() -> None:
    assert _principal({"id": "p", "kind": "service"}) == {"id": "p", "kind": "service"}
    assert _principal({"id": "p", "kind": "alien"}) is None
    assert _principal({"kind": "human"}) is None
    assert _principal("nope") is None


def test_verification_validates_status() -> None:
    assert _verification({"status": "failed", "method": "m"}) == {
        "status": "failed",
        "method": "m",
    }
    assert _verification({"status": "bogus"}) == {}
    assert _verification("nope") == {}


def test_chain_drops_malformed_principals() -> None:
    event = adapt(
        _record(
            token_ref="t", chain=["x", {"id": "a", "kind": "service"}, {"id": "b"}]
        ),
        subject="s",
    ).events[0]
    assert event["data"]["chain"] == [{"id": "a", "kind": "service"}]


def test_chain_that_is_not_a_list_is_ignored() -> None:
    event = adapt(_record(token_ref="t", chain="nope"), subject="s").events[0]
    assert "chain" not in event["data"]


def test_each_missing_envelope_field_skips_the_record() -> None:
    for field in ("timestamp", "id", "format"):
        record = json.loads(_record(token_ref="t"))
        del record[field]
        result = adapt(json.dumps(record), subject="s")
        assert result.report.skipped[0].reason == "missing_envelope"


def test_id_falls_back_to_trace_and_span() -> None:
    record = {
        "timestamp": "2026-05-09T09:00:00.000Z",
        "format": "token-exchange",
        "trace_id": "0" * 32,
        "span_id": "1" * 16,
        "token_ref": "t",
    }
    event = adapt(json.dumps(record), subject="s").events[0]
    assert event["id"] == "identity:" + "0" * 32 + "/" + "1" * 16


def test_invalid_json_and_non_object_lines_are_skipped() -> None:
    log = '"string"\n{bad\n' + _record(token_ref="t")
    result = adapt(log, subject="s")
    assert len(result.events) == 1
    assert {s.reason for s in result.report.skipped} == {
        "not_an_object",
        "invalid_json",
    }


def test_trace_context_is_carried() -> None:
    event = adapt(
        _record(
            token_ref="t",
            trace_id="a" * 32,
            span_id="b" * 16,
            parent_span_id="c" * 16,
            task_id="task",
        ),
        subject="s",
    ).events[0]
    assert event["agentceparent"] == "c" * 16
    assert event["agentcetask"] == "task"


def test_blank_lines_are_ignored() -> None:
    result = adapt("\n\n" + _record(token_ref="t") + "\n", subject="s")
    assert result.report.records_seen == 1
    assert len(result.events) == 1


def test_str_and_bytes_are_both_accepted() -> None:
    record = _record(token_ref="t")
    assert (
        adapt(record.encode(), subject="s").events == adapt(record, subject="s").events
    )
