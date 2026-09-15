"""Integrity verification: one IntegrityResult per stream, every status, tamper at the exact index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from agentce import integrity
from agentce.integrity import GENESIS_PREV, IntegrityStatus, verify_bundle

_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "spec"
        / "report"
        / "integrity-result.schema.json"
    ).read_text(encoding="utf-8")
)

STREAM = "urn:agentce:source:gw|spiffe://corp/agents/a"


def _ig(event: dict[str, Any]) -> dict[str, Any]:
    return event["data"]["integrity"]


def make_event(
    event_id: str,
    prev: str,
    time: str,
    *,
    strength: str = "source_signed",
    sig_ref: str | None = "attestations/a.dsse.json",
    stream: str = STREAM,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": event_id,
        "source": "urn:agentce:source:gw",
        "subject": "spiffe://corp/agents/a",
        "time": time,
        "type": "org.agent-conformance.evidence.ToolCall.v1",
        "data": {"@type": "ToolCall", "note": event_id, "integrity": {}},
    }
    block: dict[str, Any] = {"prev": prev, "stream": stream, "strength": strength}
    if sig_ref is not None:
        block["sig_ref"] = sig_ref
    event["data"]["integrity"] = block
    block["hash"] = integrity.recompute_hash(event)
    return event


def chain(
    count: int,
    *,
    strength: str = "source_signed",
    sig_ref: str | None = "attestations/a.dsse.json",
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    prev = GENESIS_PREV
    for i in range(count):
        event = make_event(
            f"e{i}", prev, f"2026-01-01T00:0{i}:00Z", strength=strength, sig_ref=sig_ref
        )
        events.append(event)
        prev = _ig(event)["hash"]
    return events


def test_verified_source_signed() -> None:
    (result,) = verify_bundle(chain(3), {})
    assert result.status == IntegrityStatus.VERIFIED.value
    assert result.first_bad_index is None
    jsonschema.validate(result.to_json(), _SCHEMA)


def test_verified_weak_export_chained() -> None:
    (result,) = verify_bundle(chain(2, strength="export_chained"), {})
    assert result.status == IntegrityStatus.VERIFIED_WEAK.value


def test_unsigned_when_source_signed_without_sig() -> None:
    (result,) = verify_bundle(chain(2, sig_ref=None), {})
    assert result.status == IntegrityStatus.UNSIGNED.value


def test_failed_on_tamper_at_exact_index() -> None:
    events = chain(3)
    events[1]["data"]["note"] = (
        "tampered"  # content changed, stored hash no longer matches
    )
    (result,) = verify_bundle(events, {})
    assert result.status == IntegrityStatus.FAILED.value
    assert result.first_bad_index == 1


def test_gap_when_event_missing() -> None:
    events = chain(3)
    (result,) = verify_bundle([events[0], events[2]], {})  # drop the middle event
    assert result.status == IntegrityStatus.GAP.value
    assert result.first_bad_index == 1


def test_reordered_when_swapped() -> None:
    events = chain(2)
    (result,) = verify_bundle([events[1], events[0]], {})
    assert result.status == IntegrityStatus.REORDERED.value
    assert result.first_bad_index == 0


def test_time_suspect_against_anchor() -> None:
    events = chain(2)
    manifest = {
        "streams": [
            {
                "stream": STREAM,
                "anchors": [
                    {"kind": "rfc3161", "ref": "r", "at": "2026-01-01T00:00:30Z"}
                ],
            }
        ]
    }
    (result,) = verify_bundle(events, manifest)
    assert result.status == IntegrityStatus.TIME_SUSPECT.value
    assert result.first_bad_index == 1
    jsonschema.validate(result.to_json(), _SCHEMA)


def test_export_anchored_verified_with_anchor() -> None:
    events = chain(2, strength="export_anchored", sig_ref=None)
    manifest = {
        "streams": [
            {
                "stream": STREAM,
                "anchors": [
                    {
                        "kind": "transparency_log",
                        "ref": "tl",
                        "at": "2026-01-01T09:00:00Z",
                    }
                ],
            }
        ]
    }
    (result,) = verify_bundle(events, manifest)
    assert result.status == IntegrityStatus.VERIFIED.value


def test_export_anchored_weak_without_anchor() -> None:
    events = chain(2, strength="export_anchored", sig_ref=None)
    (result,) = verify_bundle(events, {})
    assert result.status == IntegrityStatus.VERIFIED_WEAK.value


def test_sig_file_existence_checked_with_bundle_root(tmp_path: Path) -> None:
    events = chain(1, sig_ref="attestations/missing.dsse.json")
    (result,) = verify_bundle(
        events, {}, tmp_path
    )  # attestation file absent under root
    assert result.status == IntegrityStatus.UNSIGNED.value
