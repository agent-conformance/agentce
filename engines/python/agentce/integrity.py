"""Integrity verification of evidence streams (SPEC §6.6).

An integrity stream is ``(source, subject)``; each event carries ``data.integrity``
``{hash, prev, stream, strength, sig_ref?}``. ``hash`` is the SHA-256 of the RFC 8785 canonical form
of the event with ``data.integrity`` removed, and ``prev`` links it to the previous event's hash
(``"0"*64`` at genesis). Verification recomputes each hash (detecting tampering at the exact index),
checks the ``prev`` chain (gap or reordered), cross-checks source timestamps against anchor times
(time_suspect), and reports the stream ``strength`` and one ``status`` per stream. Broken streams are
never fatal: they are findings the evaluator uses to downgrade dependent assertions (SPEC §8.1).

A ``source_signed`` stream reads verified only when every event's ``sig_ref`` names a DSSE envelope in the bundle
that verifies against a key the run trusts and whose in-toto Statement names that event's own hash (SPEC §6.6); one
envelope signs a segment by naming each of its events' hashes. The trusted keys are the operator's own
trust root (``--trust-root`` or ``AGENTCE_TRUST_ROOT``), never the development root vendored in the engine, whose
private seed is published: with no trust root no source signature counts, and the stream reads unsigned.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from . import canonical, signing
from .bundle import confine_to_root, safe_is_file
from .structural import compare_instants

GENESIS_PREV = "0" * 64
#: Any aware date-time; an anchor time comparable with it is a UTC instant (compare_instants).
_EPOCH = "1970-01-01T00:00:00Z"
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
    #: What the strength and signatures alone read (verified, verified_weak or unsigned), kept when the time check has
    #: already made the status time_suspect, so a stream's weakness still caps (SPEC §6.6). Not written out.
    strength_status: str = ""

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


def _latest_anchor(anchors: list[dict[str, Any]]) -> str | None:
    """The latest anchor time, compared as instants (SPEC §6.7); an anchor that is not an object, or whose time is not
    a string with a UTC offset, places no record and is left out."""
    latest: str | None = None
    for anchor in anchors:
        at = anchor.get("at") if isinstance(anchor, dict) else None
        if (
            isinstance(at, str)
            and compare_instants(at, _EPOCH) is not None
            and (latest is None or compare_instants(at, latest) == 1)
        ):
            latest = at
    return latest


def _verify_stream(
    stream: str,
    events: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    *,
    bundle_root: Path | None,
    source_trust: signing.TrustRoot | None,
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

    # An anchor whose time cannot be placed (no UTC offset, absent, or not a date-time) anchors nothing (SPEC §8.1).
    latest_anchor = _latest_anchor(anchors)
    result.strength_status = _strength_status(
        strength, blocks, latest_anchor is not None, bundle_root, source_trust
    )

    # 3. Timestamp trust: an event later than the anchor that covers it is suspect, and so is one whose time cannot
    # be placed against it (no UTC offset, or not a date-time): it cannot be cross-checked (SPEC §6.6, fail closed).
    # A stream whose anchors are all unplaceable cannot be cross-checked at all, so it is suspect as a whole.
    if anchors and latest_anchor is None:
        result.status = IntegrityStatus.TIME_SUSPECT.value
        return result
    if latest_anchor is not None:
        for index, event in enumerate(events):
            if compare_instants(str(event.get("time", "")), latest_anchor) in (1, None):
                result.status = IntegrityStatus.TIME_SUSPECT.value
                result.first_bad_index = index
                return result

    result.status = result.strength_status
    return result


def _strength_status(
    strength: str,
    blocks: list[dict[str, Any] | None],
    anchored: bool,
    bundle_root: Path | None,
    trust: signing.TrustRoot | None,
) -> str:
    """What a stream's strength and signatures read: source_signed is verified only when every record is signed,
    export_anchored only with a placeable anchor, and export_chained proves only "unchanged since export"."""
    if strength == IntegrityStrength.SOURCE_SIGNED.value:
        envelopes: dict[str, frozenset[str]] = {}
        signed = all(
            _is_signed(block, bundle_root, trust, envelopes) for block in blocks
        )
        return (
            IntegrityStatus.VERIFIED.value if signed else IntegrityStatus.UNSIGNED.value
        )
    if strength == IntegrityStrength.EXPORT_ANCHORED.value and anchored:
        return IntegrityStatus.VERIFIED.value
    return IntegrityStatus.VERIFIED_WEAK.value


def _is_signed(
    block: dict[str, Any] | None,
    bundle_root: Path | None,
    trust: signing.TrustRoot | None,
    envelopes: dict[str, frozenset[str]],
) -> bool:
    """Whether a record's ``sig_ref`` envelope verifies against a trusted key and names the record's own hash. A later
    record's hash does not do: a hash leaves out ``data.integrity``, so it commits to no earlier record. Anything else,
    including no trust root or no bundle to read the envelope from, is unsigned (fail closed)."""
    if block is None or trust is None or bundle_root is None:
        return False
    sig_ref = block.get("sig_ref")
    if not isinstance(sig_ref, str) or not sig_ref:
        return False
    if sig_ref not in envelopes:
        envelopes[sig_ref] = _signed_digests(bundle_root, sig_ref, trust)
    return block.get("hash") in envelopes[sig_ref]


def _signed_digests(
    bundle_root: Path, sig_ref: str, trust: signing.TrustRoot
) -> frozenset[str]:
    """The SHA-256 digests the in-toto Statement of a verified ``sig_ref`` envelope names as subjects; none when the
    file is outside the bundle or unreadable, is not a DSSE envelope over an in-toto Statement, or verifies against
    no key in ``trust``."""
    path = confine_to_root(bundle_root, sig_ref)
    if path is None or not safe_is_file(path):
        return frozenset()
    try:
        envelope = signing.read_verify_input(
            path, "verify.envelope_malformed", "the signature envelope"
        )
        verified = signing.verify_envelope(envelope, trust)
        if envelope.get("payloadType") != signing.INTOTO_PAYLOAD_TYPE:
            return frozenset()
        statement = signing.parse_verify_input(
            verified.payload, "verify.statement_unreadable", "the signed statement"
        )
    except (signing.VerificationError, OSError):
        return frozenset()
    subjects = statement.get("subject") if isinstance(statement, dict) else None
    if not isinstance(subjects, list):
        return frozenset()
    digests = (s.get("digest") for s in subjects if isinstance(s, dict))
    return frozenset(
        d["sha256"]
        for d in digests
        if isinstance(d, dict) and isinstance(d.get("sha256"), str)
    )


def verify_bundle(
    events: list[dict[str, Any]],
    manifest: dict[str, Any],
    bundle_root: Path | None = None,
    *,
    source_trust: signing.TrustRoot | None = None,
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
) -> list[IntegrityResult]:
    """Verify every integrity stream in ``events``; return one result per stream, ordered by stream id, then source.

    A chain is keyed on (the record's source, the stream it claims): a claimed stream id names a stream of the record's
    own source (SPEC §6.6), so a record never joins, or breaks, another source's chain by naming it, and no chain under
    an id two sources claim reads verified (18.151)."""
    return list(
        verify_streams(
            events,
            manifest,
            bundle_root,
            source_trust=source_trust,
            clock_skew_seconds=clock_skew_seconds,
        ).values()
    )


def verify_streams(
    events: list[dict[str, Any]],
    manifest: dict[str, Any],
    bundle_root: Path | None = None,
    *,
    source_trust: signing.TrustRoot | None = None,
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
) -> dict[tuple[str, str], IntegrityResult]:
    """:func:`verify_bundle`'s results keyed by (stream id, source), in its order."""
    anchors_by_stream = _anchors_by_stream(manifest)
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for event in events:
        grouped.setdefault(_stream_key(event), {}).setdefault(
            str(event.get("source", "")), []
        ).append(event)

    results: dict[tuple[str, str], IntegrityResult] = {}
    for stream in sorted(grouped):
        by_source = grouped[stream]
        for source in sorted(by_source):
            result = _verify_stream(
                stream,
                by_source[source],
                anchors_by_stream.get(stream, []),
                bundle_root=bundle_root,
                source_trust=source_trust,
                clock_skew_seconds=clock_skew_seconds,
            )
            # A stream id two sources claim: the manifest's anchors and a sig_ref name no source, so neither chain
            # under that id reads verified, which fails closed rather than let one source's chain borrow another's
            # anchors (18.151).
            if len(by_source) > 1:
                if result.status == IntegrityStatus.VERIFIED.value:
                    result.status = IntegrityStatus.VERIFIED_WEAK.value
                if result.strength_status == IntegrityStatus.VERIFIED.value:
                    result.strength_status = IntegrityStatus.VERIFIED_WEAK.value
            results[(stream, source)] = result
    return results


#: A broken chain: every record on it is read at most as self_report (SPEC §8.1).
_BROKEN = frozenset(
    {
        IntegrityStatus.FAILED.value,
        IntegrityStatus.GAP.value,
        IntegrityStatus.REORDERED.value,
    }
)
#: A chain below verified: an enforcement-point record on it caps a pass at partial (SPEC §6.6, weak_integrity).
_WEAK = frozenset({IntegrityStatus.VERIFIED_WEAK.value, IntegrityStatus.UNSIGNED.value})
_CAPPED_CLASSES = frozenset({"enforcement_point", "independent_system"})


@dataclass(frozen=True)
class IntegrityCaps:
    """Which accepted records an integrity result caps, by event id (SPEC §6.6, §8.1): ``broken`` holds the
    enforcement-point and independent-system records on a failed, gap or reordered stream (a record with no
    integrity member included), ``weak`` the enforcement-point records on a verified_weak or unsigned stream, and
    ``time_suspect`` every record on a time_suspect stream."""

    broken: frozenset[str] = frozenset()
    weak: frozenset[str] = frozenset()
    time_suspect: frozenset[str] = frozenset()


NO_CAPS = IntegrityCaps()


def integrity_caps(
    raw_accepted: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
    results: dict[tuple[str, str], IntegrityResult],
) -> IntegrityCaps:
    """The caps ``results`` (from :func:`verify_streams` over ``raw_accepted``) put on ``accepted``, the same
    records index for index with their trust-corrected class (ingest)."""
    broken: set[str] = set()
    weak: set[str] = set()
    suspect: set[str] = set()
    for raw, event in zip(raw_accepted, accepted, strict=True):
        result = results.get((_stream_key(raw), str(raw.get("source", ""))))
        status = result.status if result is not None else IntegrityStatus.FAILED.value
        event_id = str(event.get("id"))
        cls = event.get("agentcesourceclass")
        if status in _BROKEN and cls in _CAPPED_CLASSES:
            broken.add(event_id)
        elif status in _WEAK and cls == "enforcement_point":
            weak.add(event_id)
        elif status == IntegrityStatus.TIME_SUSPECT.value:
            suspect.add(event_id)
            # The time check reads before the strength: a weak or unsigned stream stays weak whatever its times say.
            if (
                cls == "enforcement_point"
                and result is not None
                and result.strength_status in _WEAK
            ):
                weak.add(event_id)
    return IntegrityCaps(frozenset(broken), frozenset(weak), frozenset(suspect))


def _anchors_by_stream(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    streams = manifest.get("streams")
    if isinstance(streams, list):
        for entry in streams:
            if isinstance(entry, dict) and isinstance(entry.get("stream"), str):
                anchors = entry.get("anchors")
                out[str(entry["stream"])] = anchors if isinstance(anchors, list) else []
    return out
