"""Integrity verification: one IntegrityResult per stream, every status, tamper at the exact index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from agentce import integrity
from agentce.integrity import (
    GENESIS_PREV,
    IntegrityStatus,
    integrity_caps,
    verify_bundle,
    verify_streams,
)

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


def test_a_record_never_breaks_another_sources_chain_by_claiming_its_stream() -> None:
    # 18.151: the agent's own record names the gateway's stream with a hash that matches nothing; chains are keyed on
    # (source, claimed stream), so the gateway's chain reads as it does alone and the hijacker gets its own result.
    genuine = chain(2, strength="export_chained")
    hijack = make_event(
        "x9", "e" * 64, "2026-01-01T00:05:00Z", strength="export_chained"
    )
    hijack["source"] = "urn:example:agent"
    _ig(hijack)["hash"] = "f" * 64
    results = verify_bundle([genuine[0], hijack, genuine[1]], {})
    assert [(r.stream, r.status) for r in results] == [
        (STREAM, IntegrityStatus.VERIFIED_WEAK.value),
        (STREAM, IntegrityStatus.FAILED.value),
    ]
    assert [(r.stream, r.status) for r in verify_bundle(genuine, {})] == [
        (STREAM, IntegrityStatus.VERIFIED_WEAK.value)
    ]


def test_no_chain_reads_verified_under_a_stream_id_two_sources_claim() -> None:
    # 18.151: the manifest's anchors name no source, so the agent's own well-formed export_anchored link claiming the
    # gateway's stream id would borrow the gateway's anchors; with two sources on one id, neither reads verified.
    manifest = {
        "streams": [
            {
                "stream": STREAM,
                "anchors": [
                    {"kind": "rfc3161", "ref": "a.tsr", "at": "2026-01-02T00:00:00Z"}
                ],
            }
        ]
    }
    genuine = chain(2, strength="export_anchored")
    hijack = make_event(
        "x9", GENESIS_PREV, "2026-01-01T00:05:00Z", strength="export_anchored"
    )
    hijack["source"] = "urn:example:agent"
    _ig(hijack)["hash"] = integrity.recompute_hash(hijack)
    assert [r.status for r in verify_bundle(genuine, manifest)] == ["verified"]
    assert [r.status for r in verify_bundle([*genuine, hijack], manifest)] == [
        IntegrityStatus.VERIFIED_WEAK.value,
        IntegrityStatus.VERIFIED_WEAK.value,
    ]


def _anchored(at: str) -> dict[str, Any]:
    return {
        "streams": [
            {"stream": STREAM, "anchors": [{"kind": "rfc3161", "ref": "r", "at": at}]}
        ]
    }


def test_time_check_record_time_without_offset_is_time_suspect() -> None:
    """A record time with no UTC offset cannot be placed against the anchor, so it cannot be cross-checked (SPEC §6.6)."""
    events = chain(2, strength="export_anchored", sig_ref=None)
    events[1]["time"] = "2026-01-01T00:01:00"
    _ig(events[1])["hash"] = integrity.recompute_hash(events[1])
    events[1]["data"]["integrity"]["prev"] = _ig(events[0])["hash"]
    (result,) = verify_bundle(events, _anchored("2026-01-01T09:00:00Z"))
    assert result.status == IntegrityStatus.TIME_SUSPECT.value
    assert result.first_bad_index == 1


def test_time_check_anchor_without_offset_anchors_nothing() -> None:
    """An anchor time with no UTC offset, or none at all, anchors nothing: the stream is time_suspect as a whole and
    its strength reads as an unanchored stream's (critic r2 N1)."""
    for at in ("2026-01-01T09:00:00", ""):
        events = chain(2, strength="export_anchored", sig_ref=None)
        (result,) = verify_bundle(events, _anchored(at))
        assert result.status == IntegrityStatus.TIME_SUSPECT.value
        assert result.first_bad_index is None
        assert result.strength_status == IntegrityStatus.VERIFIED_WEAK.value


def test_time_check_anchor_of_the_wrong_type_anchors_nothing() -> None:
    """An anchor that is not an object, or whose time is not a string, anchors nothing, even when it holds a placeable
    time, and never crashes verify (the verify census)."""
    placeable = "2026-01-01T09:00:00Z"
    for anchor in (
        1.5,
        True,
        None,
        [placeable],
        {"at": 1.5},
        {"at": [placeable]},
        {"at": {"t": placeable}},
    ):
        manifest = {"streams": [{"stream": STREAM, "anchors": [anchor]}]}
        (result,) = verify_bundle(
            chain(2, strength="export_anchored", sig_ref=None), manifest
        )
        assert result.status == IntegrityStatus.TIME_SUSPECT.value
        assert result.strength_status == IntegrityStatus.VERIFIED_WEAK.value


def test_time_check_keeps_the_strength_reading_of_a_suspect_stream() -> None:
    """time_suspect never hides an unsigned source_signed stream (critic r1 F1)."""
    (result,) = verify_bundle(chain(2, sig_ref=None), _anchored("2026-01-01T00:00:30Z"))
    assert result.status == IntegrityStatus.TIME_SUSPECT.value
    assert result.strength_status == IntegrityStatus.UNSIGNED.value


def _classed(events: list[dict[str, Any]], cls: str) -> list[dict[str, Any]]:
    return [{**e, "agentcesourceclass": cls} for e in events]


def test_integrity_cap_classification() -> None:
    """Broken streams cap enforcement-point and independent-system records, weak ones only enforcement-point records,
    a time_suspect stream every record (and its enforcement-point records stay weak when its strength is weak); a
    self_report record is never capped, and a record no chain verified (no integrity member) counts as broken."""
    tampered = chain(2)
    tampered[0]["note"] = "changed after it was hashed"
    for cls in ("enforcement_point", "independent_system"):
        raw = tampered
        caps = integrity_caps(raw, _classed(raw, cls), verify_streams(raw, {}))
        assert caps.broken == {"e0", "e1"}
    raw = tampered
    caps = integrity_caps(raw, _classed(raw, "self_report"), verify_streams(raw, {}))
    assert not (caps.broken or caps.weak or caps.time_suspect)

    weak = chain(2, strength="export_chained", sig_ref=None)
    assert integrity_caps(
        weak, _classed(weak, "enforcement_point"), verify_streams(weak, {})
    ).weak == {"e0", "e1"}
    assert (
        integrity_caps(
            weak, _classed(weak, "independent_system"), verify_streams(weak, {})
        )
        == integrity.NO_CAPS
    )

    suspect = chain(2, strength="export_chained", sig_ref=None)
    results = verify_streams(suspect, _anchored("2026-01-01T00:00:30Z"))
    caps = integrity_caps(suspect, _classed(suspect, "enforcement_point"), results)
    assert caps.time_suspect == {"e0", "e1"}
    assert caps.weak == {"e0", "e1"}

    missing = [{**chain(1)[0], "data": {"@type": "ToolCall"}}]
    caps = integrity_caps(
        missing, _classed(missing, "enforcement_point"), verify_streams(missing, {})
    )
    assert caps.broken == {"e0"}
