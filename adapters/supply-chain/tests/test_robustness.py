"""The adapter parses untrusted supply-chain logs; malformed or partial records degrade, never crash."""

from __future__ import annotations

import json
from typing import Any

from agentce_adapters import adapt


def _record(**fields: Any) -> str:
    base = {"timestamp": "2026-05-08T09:00:00.000Z", "id": "r", "format": "in-toto"}
    return json.dumps({**base, **fields})


def test_each_missing_envelope_field_skips_the_record() -> None:
    for field in ("timestamp", "id", "format"):
        record = json.loads(_record(kind="attestation", subject_digests=["sha256:a"]))
        del record[field]
        result = adapt(json.dumps(record), subject="s")
        assert result.events == []
        assert result.report.skipped[0].reason == "missing_envelope"


def test_id_falls_back_to_trace_and_span() -> None:
    record = {
        "timestamp": "2026-05-08T09:00:00.000Z",
        "format": "sigstore",
        "kind": "attestation",
        "trace_id": "0" * 32,
        "span_id": "1" * 16,
        "subject_digests": ["sha256:a"],
    }
    event = adapt(json.dumps(record), subject="s").events[0]
    assert event["id"] == "supply-chain:" + "0" * 32 + "/" + "1" * 16


def test_unknown_kind_is_skipped() -> None:
    result = adapt(_record(kind="provenance"), subject="s")
    assert result.report.skipped[0].reason == "unknown_kind"


def test_invalid_json_and_non_object_lines_are_skipped() -> None:
    log = '"a string"\n{broken\n' + _record(
        kind="attestation", subject_digests=["sha256:a"]
    )
    result = adapt(log, subject="s")
    assert len(result.events) == 1
    assert {s.reason for s in result.report.skipped} == {
        "not_an_object",
        "invalid_json",
    }


def test_a_matching_digest_with_a_trusted_key_verifies() -> None:
    record = _record(
        kind="attestation",
        format="sigstore",
        subject_digests=["sha256:a", "sha256:b"],
        observed_digests=["sha256:b", "sha256:a"],  # order-independent
        signature={"key_id": "k1"},
    )
    event = adapt(record, subject="s", trusted_keys=["k1"]).events[0]
    assert event["data"]["verification"]["status"] == "verified"


def test_a_bundle_loaded_with_no_components_still_emits() -> None:
    record = _record(
        kind="bundle_loaded", format="bundle-load", bundle_digest="sha256:x"
    )
    event = adapt(record, subject="s").events[0]
    assert event["data"]["@type"] == "BundleLoaded"
    assert "components" not in event["data"]


def test_components_that_are_not_a_list_are_ignored() -> None:
    record = _record(
        kind="bundle_loaded",
        format="bundle-load",
        bundle_digest="sha256:x",
        components="oops",
    )
    event = adapt(record, subject="s").events[0]
    assert "components" not in event["data"]


def test_trace_context_is_carried() -> None:
    record = _record(
        kind="attestation",
        subject_digests=["sha256:a"],
        trace_id="a" * 32,
        span_id="b" * 16,
        parent_span_id="c" * 16,
        task_id="t",
    )
    event = adapt(record, subject="s").events[0]
    assert event["agentceparent"] == "c" * 16
    assert event["agentcetask"] == "t"


def test_blank_lines_are_ignored() -> None:
    result = adapt(
        "\n\n" + _record(kind="attestation", subject_digests=["sha256:a"]) + "\n",
        subject="s",
    )
    assert result.report.records_seen == 1
    assert len(result.events) == 1


def test_str_and_bytes_are_both_accepted() -> None:
    record = _record(kind="attestation", subject_digests=["sha256:a"])
    assert (
        adapt(record.encode(), subject="s").events == adapt(record, subject="s").events
    )
