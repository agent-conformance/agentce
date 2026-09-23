"""Integrity verification of evidence streams (SPEC §6.6).

An integrity stream is ``(source, subject)``; each event carries ``data.integrity``
``{hash, prev, stream, strength, sig_ref?}``. ``hash`` is the SHA-256 of the RFC 8785 canonical form
of the event with ``data.integrity`` removed, and ``prev`` links it to the previous event's hash
(``"0"*64`` at genesis). Verification recomputes each hash (detecting tampering at the exact index),
checks the ``prev`` chain (gap or reordered), cross-checks source timestamps against anchor times
(time_suspect), and reports the stream ``strength`` and one ``status`` per stream. Broken streams are
never fatal: they are findings the evaluator uses to downgrade dependent assertions (SPEC §8.1).

Cryptographic DSSE/Sigstore signature verification against a vendored trust root is a release-time
concern (SPEC §8.7, Phase 5); here a ``source_signed`` stream is treated as signed when every event
carries a ``sig_ref`` whose attestation file is present in the bundle, and the hash chain itself is
verified cryptographically with SHA-256.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from . import canonical
from .bundle import confine_to_root, safe_is_file

GENESIS_PREV = "0" * 64
DEFAULT_CLOCK_SKEW_SECONDS = 300  # 5 minutes (SPEC §6.6 timestamp trust)


class IntegrityStrength(str, Enum):
    SOURCE_SIGNED = "source_signed"
    EXPORT_ANCHORED = "export_anchored"
    EXPORT_CHAINED = "export_chained"


class IntegrityStatus(str, Enum):
    VERIFIED = "verified"
    VERIFIED_WEAK = "verified_weak"
    GAP = "gap"
    REORDERED = "reordered"
    UNSIGNED = "unsigned"
    TIME_SUSPECT = "time_suspect"
    FAILED = "failed"


@dataclass
class IntegrityResult:
    """The verification outcome for one integrity stream (integrity-result.schema.json)."""

    stream: str
    strength: str
    status: str
    first_bad_index: int | None = None
    anchors: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "stream": self.stream,
            "strength": self.strength,
            "status": self.status,
        }
        if self.first_bad_index is not None:
            record["first_bad_index"] = self.first_bad_index
        if self.anchors:
            record["anchors"] = self.anchors
        return record


def _integrity(event: dict[str, Any]) -> dict[str, Any] | None:
    data = event.get("data")
    if isinstance(data, dict):
        block = data.get("integrity")
        if isinstance(block, dict):
            return block
    return None


def _stream_key(event: dict[str, Any]) -> str:
    block = _integrity(event)
    if block is not None and isinstance(block.get("stream"), str):
        return str(block["stream"])
    return f"{event.get('source', '')}|{event.get('subject', '')}"


def recompute_hash(event: dict[str, Any]) -> str:
    """Return the SHA-256 of the canonical event with ``data.integrity`` removed (SPEC §6.6)."""
    without = copy.deepcopy(event)
    data = without.get("data")
    if isinstance(data, dict):
        data.pop("integrity", None)
    return canonical.sha256_hex(without)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _anchor_times(anchors: list[dict[str, Any]]) -> list[datetime]:
    times = []
    for anchor in anchors:
        parsed = _parse_time(str(anchor.get("at", "")))
        if parsed is not None:
            times.append(parsed)
    return times


def _verify_stream(
    stream: str,
    events: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    *,
    bundle_root: Path | None,
    clock_skew_seconds: int,
) -> IntegrityResult:
    blocks = [_integrity(e) for e in events]
    strength = next(
        (
            str(b["strength"])
            for b in blocks
            if b is not None and isinstance(b.get("strength"), str)
        ),
        IntegrityStrength.EXPORT_CHAINED.value,
    )
    result = IntegrityResult(
        stream=stream, strength=strength, status="", anchors=anchors
    )

    # 1. Tamper: a recomputed hash that does not match the stored one.
    for index, (event, block) in enumerate(zip(events, blocks, strict=True)):
        if block is None or block.get("hash") != recompute_hash(event):
            result.status = IntegrityStatus.FAILED.value
            result.first_bad_index = index
            return result

    # 2. Chain linkage in arrival order.
    all_hashes = {str(b["hash"]) for b in blocks if b is not None and "hash" in b}
    for index, block in enumerate(blocks):
        assert block is not None  # tamper pass guarantees a hash on every event
        prev = str(block.get("prev", ""))
        want = GENESIS_PREV if index == 0 else str(blocks[index - 1]["hash"])  # type: ignore[index]
        if prev != want:
            broken = (
                IntegrityStatus.GAP
                if (prev != GENESIS_PREV and prev not in all_hashes)
                else IntegrityStatus.REORDERED
            )
            result.status = broken.value
            result.first_bad_index = index
            return result

    # 3. Timestamp trust: an event later than the anchor that covers it is suspect.
    anchor_times = _anchor_times(anchors)
    if anchor_times:
        latest_anchor = max(anchor_times)
        for index, event in enumerate(events):
            event_time = _parse_time(str(event.get("time", "")))
            if event_time is not None and event_time > latest_anchor:
                result.status = IntegrityStatus.TIME_SUSPECT.value
                result.first_bad_index = index
                return result

    # 4. Strength and signatures.
    if strength == IntegrityStrength.SOURCE_SIGNED.value:
        if all(_is_signed(block, bundle_root) for block in blocks):
            result.status = IntegrityStatus.VERIFIED.value
        else:
            result.status = IntegrityStatus.UNSIGNED.value
    elif strength == IntegrityStrength.EXPORT_ANCHORED.value:
        result.status = (
            IntegrityStatus.VERIFIED.value
            if anchors
            else IntegrityStatus.VERIFIED_WEAK.value
        )
    else:  # export_chained: a chain computed after export proves only "unchanged since export".
        result.status = IntegrityStatus.VERIFIED_WEAK.value
    return result


def _is_signed(block: dict[str, Any] | None, bundle_root: Path | None) -> bool:
    if block is None:
        return False
    sig_ref = block.get("sig_ref")
    if not isinstance(sig_ref, str) or not sig_ref:
        return False
    if bundle_root is None:
        return True
    path = confine_to_root(bundle_root, sig_ref)
    return path is not None and safe_is_file(path)


def verify_bundle(
    events: list[dict[str, Any]],
    manifest: dict[str, Any],
    bundle_root: Path | None = None,
    *,
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
) -> list[IntegrityResult]:
    """Verify every integrity stream in ``events``; return one result per stream, ordered by stream id."""
    anchors_by_stream = _anchors_by_stream(manifest)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(_stream_key(event), []).append(event)

    results = []
    for stream in sorted(grouped):
        results.append(
            _verify_stream(
                stream,
                grouped[stream],
                anchors_by_stream.get(stream, []),
                bundle_root=bundle_root,
                clock_skew_seconds=clock_skew_seconds,
            )
        )
    return results


def _anchors_by_stream(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    streams = manifest.get("streams")
    if isinstance(streams, list):
        for entry in streams:
            if isinstance(entry, dict) and isinstance(entry.get("stream"), str):
                anchors = entry.get("anchors")
                out[str(entry["stream"])] = anchors if isinstance(anchors, list) else []
    return out
