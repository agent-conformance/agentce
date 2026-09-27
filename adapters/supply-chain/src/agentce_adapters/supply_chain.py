"""The ``supply-chain`` adapter: attestations and bundle-load logs to AgentCE events (SPEC 12).

Supply-chain evidence comes from admission controllers and registries -- Sigstore/in-toto
attestations, CycloneDX AIBOMs, registry pins, and runtime bundle-load logs. These records are trust
class ``enforcement_point`` (admission) or ``independent_system`` (registry) (SPEC 6.4, 12.2). The
adapter maps a normalised supply-chain log (JSON Lines, one record per line, each naming its ``kind``
and ``format``):

* ``bundle_loaded`` (a bundle-load log line or a CycloneDX AIBOM) -> ``BundleLoaded``;
* ``attestation``   (a Sigstore/in-toto statement)               -> ``Attestation``.

The adapter **verifies at adapt time** and records the outcome in ``Attestation.verification`` (SPEC
12.2). Verification is deterministic and offline. An attestation record carries a DSSE envelope
(``dsse``) over an in-toto Statement. It is ``verified`` only when a signature in the envelope
cryptographically verifies, over the DSSE pre-authentication encoding, against a public key the caller
trusts (Ed25519, or ECDSA on P-256 with SHA-256) *and* the Statement's subject digests equal the digests
observed at load. A signature that does not verify, a signer outside the trusted set, or a digest
mismatch is ``failed``; no signature, a trusted key id without key material, or no observed digests to
bind the Statement to is ``unverified``. The statement type and subject digests are read from the signed
payload, never from unsigned record fields, and the reported signer is the verifying key's id.

The adapter contract of SPEC 12.1 holds: deterministic ids, preserved timestamps, a declared trust
class, a recorded convention, no invented events. Standard library plus ``cryptography``; no network, no
learned component.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.serialization import (
    load_der_public_key,
    load_pem_public_key,
)

#: The JSON-LD context every canonical payload carries (SPEC 6.2.2).
BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"

#: The declared trust classes a supply-chain deployment may assert (SPEC 6.4, 12.2).
VALID_SOURCE_CLASSES = frozenset(
    {"self_report", "enforcement_point", "independent_system"}
)

#: Record ``kind`` -> canonical event type.
_KINDS = {"bundle_loaded": "BundleLoaded", "attestation": "Attestation"}

#: The DSSE payload type of an in-toto Statement; an envelope of any other type is not an attestation.
_INTOTO_PAYLOAD_TYPE = "application/vnd.in-toto+json"

_COMPONENT_KINDS = frozenset(
    {"skill", "mcp_server", "model", "prompt", "config", "policy"}
)


class AdapterError(ValueError):
    """The input could not be adapted. ``reason`` is a stable key for tests and callers."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class SkippedRecord:
    """A supply-chain record the adapter did not map, recorded rather than dropped (SPEC 12.1)."""

    line: int
    reason: str


@dataclass(frozen=True)
class AdapterReport:
    adapter: str
    conventions: tuple[str, ...]
    records_seen: int
    events_emitted: int
    skipped: tuple[SkippedRecord, ...]


@dataclass
class AdaptResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    report: AdapterReport = field(
        default_factory=lambda: AdapterReport("supply-chain", (), 0, 0, ())
    )


# --- Decoding helpers. ----------------------------------------------------------------------------


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_str_list(value: object) -> list[str]:
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def _obj(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class _Record:
    line: int
    time: str
    entry_id: str
    kind: str
    convention: str
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    task_id: str | None
    session_id: str | None
    agent: dict[str, Any]
    acted_for: list[str]
    fmt: str
    raw: dict[str, Any]


def _decode(raw: dict[str, Any], line: int) -> _Record | None:
    time = _as_str(raw.get("timestamp"))
    entry_id = _as_str(raw.get("id"))
    trace_id = _as_str(raw.get("trace_id"))
    span_id = _as_str(raw.get("span_id"))
    if entry_id is None and trace_id is not None and span_id is not None:
        entry_id = f"{trace_id}/{span_id}"
    fmt = _as_str(raw.get("format"))
    if time is None or entry_id is None or fmt is None:
        return None
    version = _as_str(raw.get("convention_version"))
    return _Record(
        line=line,
        time=time,
        entry_id=entry_id,
        kind=_as_str(raw.get("kind")) or "",
        convention=f"{fmt}:{version}" if version else fmt,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=_as_str(raw.get("parent_span_id")),
        task_id=_as_str(raw.get("task_id")),
        session_id=_as_str(raw.get("session_id")),
        agent=_obj(raw.get("agent")),
        acted_for=_as_str_list(raw.get("acted_for")),
        fmt=fmt,
        raw=raw,
    )


def _base_payload(record: _Record) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    agent_id = _as_str(record.agent.get("id"))
    if agent_id is not None:
        agent: dict[str, Any] = {"id": agent_id}
        name = _as_str(record.agent.get("name"))
        if name is not None:
            agent["name"] = name
        payload["agent"] = agent
    if record.acted_for:
        payload["acted_for"] = record.acted_for
    if record.session_id is not None:
        payload["session_id"] = record.session_id
    return payload


# --- Verification (deterministic, offline; SPEC 12.2). ------------------------------------------------

_PublicKey = ed25519.Ed25519PublicKey | ec.EllipticCurvePublicKey
_KeyTable = dict[str, _PublicKey | None]


def _load_public_key(key_id: str, material: str) -> _PublicKey:
    """Load one trusted public key: PEM or base64 DER SPKI, or a raw base64 32-byte Ed25519 key."""
    try:
        text = material.strip()
        if text.startswith("-----BEGIN"):
            key = load_pem_public_key(text.encode("ascii"))
        else:
            raw = base64.b64decode(text, validate=True)
            key = (
                ed25519.Ed25519PublicKey.from_public_bytes(raw)
                if len(raw) == 32
                else load_der_public_key(raw)
            )
    except (ValueError, TypeError, binascii.Error, UnsupportedAlgorithm) as exc:
        raise AdapterError("bad_trusted_key", key_id) from exc
    if isinstance(key, ed25519.Ed25519PublicKey) or (
        isinstance(key, ec.EllipticCurvePublicKey)
        and isinstance(key.curve, ec.SECP256R1)
    ):
        return key
    raise AdapterError("bad_trusted_key", key_id)


def _trusted_key_table(
    trusted_keys: Mapping[str, str | None] | Iterable[str] | None,
) -> _KeyTable:
    """Key id -> public key; an id given without material maps to ``None`` (it cannot verify)."""
    if trusted_keys is None:
        return {}
    if isinstance(trusted_keys, Mapping):
        return {
            key_id: None if material is None else _load_public_key(key_id, material)
            for key_id, material in trusted_keys.items()
        }
    return dict.fromkeys(trusted_keys)


def _b64decode(value: object) -> bytes | None:
    """Strict base64 (standard or URL-safe alphabet, padding optional); ``None`` when malformed."""
    if not isinstance(value, str):
        return None
    if ("+" in value or "/" in value) and ("-" in value or "_" in value):
        return None
    text = value.replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        return None


def _pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE pre-authentication encoding: the exact bytes a signature covers."""
    kind = payload_type.encode("utf-8")
    return b"DSSEv1 %d %s %d %s" % (len(kind), kind, len(payload), payload)


def _signature_verifies(key: _PublicKey, signature: bytes, message: bytes) -> bool:
    """Verify ``signature`` over ``message``; the algorithm comes only from the trusted key's type."""
    try:
        if isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(signature, message)
        else:
            key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def _statement_of(envelope: dict[str, Any]) -> tuple[bytes, dict[str, Any]] | None:
    """The signed in-toto Statement and its payload bytes; ``None`` when the envelope is malformed."""
    if envelope.get("payloadType") != _INTOTO_PAYLOAD_TYPE:
        return None
    payload = _b64decode(envelope.get("payload"))
    if payload is None:
        return None
    try:
        statement = json.loads(payload)
    except ValueError:
        return None
    if (
        not isinstance(statement, dict)
        or _as_str(statement.get("_type")) is None
        or not isinstance(statement.get("subject"), list)
    ):
        return None
    return payload, statement


def _subject_digests(statement: dict[str, Any]) -> list[str]:
    digests: list[str] = []
    for subject in statement["subject"]:
        digest = _obj(_obj(subject).get("digest"))
        digests.extend(
            f"{alg}:{value.lower()}"
            for alg, value in sorted(digest.items())
            if isinstance(value, str) and value
        )
    return digests


class _SigOutcome(NamedTuple):
    """One DSSE signature entry, classified: ``verified``, ``bad``, ``untrusted`` or ``nomaterial``."""

    status: str
    key_id: str | None = None
    method: str | None = None


def _signature_outcome(entry: object, pae: bytes, keys: _KeyTable) -> _SigOutcome:
    fields = _obj(entry)
    key_id = _as_str(fields.get("keyid"))
    if key_id is None:
        return _SigOutcome("nomaterial")
    if key_id not in keys:
        return _SigOutcome("untrusted", key_id)
    key = keys[key_id]
    if key is None:
        return _SigOutcome("nomaterial", key_id)
    signature = _b64decode(fields.get("sig"))
    ok = signature is not None and _signature_verifies(key, signature, pae)
    method = (
        "dsse-ed25519"
        if isinstance(key, ed25519.Ed25519PublicKey)
        else "dsse-ecdsa-p256"
    )
    return _SigOutcome("verified" if ok else "bad", key_id, method)


@dataclass(frozen=True)
class _Claims:
    """What an attestation states: the in-toto statement type and its subject digests."""

    statement_type: str | None = None
    subject_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Verdict:
    """Outcome of verifying one attestation record. Claims come from the signed payload when there
    is an envelope; a record with no envelope reports its own unsigned fields, always ``unverified``."""

    status: str
    method: str | None = None
    signer: str | None = None
    claims: _Claims = _Claims()


def _verify(record: _Record, keys: _KeyTable) -> _Verdict:
    """Verify an attestation record's DSSE envelope; unsigned record fields never establish trust."""
    envelope = record.raw.get("dsse")
    if not isinstance(envelope, dict):
        return _Verdict(
            "unverified",
            claims=_Claims(
                _as_str(record.raw.get("statement_type")),
                tuple(_as_str_list(record.raw.get("subject_digests"))),
            ),
        )
    entries = envelope.get("signatures")
    entries = entries if isinstance(entries, list) else []
    decoded = _statement_of(envelope)
    if decoded is None:
        return _Verdict("failed" if entries else "unverified")
    payload, statement = decoded
    claims = _Claims(
        _as_str(statement.get("_type")), tuple(_subject_digests(statement))
    )

    # Any verifying signature wins (stop at the first); otherwise a bad or untrusted one makes it failed.
    pae = _pae(_INTOTO_PAYLOAD_TYPE, payload)
    verified = bad = None
    untrusted = False
    for entry in entries:
        outcome = _signature_outcome(entry, pae, keys)
        if outcome.status == "verified":
            verified = outcome
            break
        if outcome.status == "bad":
            bad = bad or outcome
        untrusted = untrusted or outcome.status == "untrusted"
    if verified is None:
        if bad or untrusted:
            return _Verdict("failed", bad.method if bad else None, claims=claims)
        return _Verdict("unverified", claims=claims)

    observed = {d.lower() for d in _as_str_list(record.raw.get("observed_digests"))}
    if not observed:
        status = "unverified"  # nothing was observed to bind the Statement to
    elif observed != set(claims.subject_digests):
        status = "failed"  # the signed subject is not what was loaded (tampering)
    else:
        status = "verified"
    signer = verified.key_id if status == "verified" else None
    return _Verdict(status, verified.method, signer, claims)


# --- Per-kind payload builders (SPEC 12.2). -------------------------------------------------------


def _component(raw: object) -> dict[str, Any] | None:
    data = _obj(raw)
    component: dict[str, Any] = {}
    kind = _as_str(data.get("kind"))
    if kind in _COMPONENT_KINDS:
        component["kind"] = kind
    for key in ("name", "version", "digest", "signer"):
        value = _as_str(data.get(key))
        if value is not None:
            component[key] = value
    return component or None


def _bundle_loaded(record: _Record, _keys: _KeyTable) -> dict[str, Any] | None:
    payload = _base_payload(record)
    bundle_digest = _as_str(record.raw.get("bundle_digest"))
    if bundle_digest is not None:
        payload["bundle_digest"] = bundle_digest
    raw_components = record.raw.get("components")
    if isinstance(raw_components, Iterable) and not isinstance(
        raw_components, (str, bytes)
    ):
        components = [
            c for c in (_component(item) for item in raw_components) if c is not None
        ]
        if components:
            payload["components"] = components
    attestation_refs = _as_str_list(record.raw.get("attestation_refs"))
    if attestation_refs:
        payload["attestation_refs"] = attestation_refs
    return payload


def _attestation(record: _Record, keys: _KeyTable) -> dict[str, Any] | None:
    payload = _base_payload(record)
    verdict = _verify(record, keys)
    if verdict.claims.statement_type is not None:
        payload["statement_type"] = verdict.claims.statement_type
    if verdict.claims.subject_digests:
        payload["subject_digests"] = list(verdict.claims.subject_digests)
    if verdict.signer is not None:
        payload["signer"] = verdict.signer
    verification: dict[str, Any] = {"status": verdict.status}
    if verdict.method is not None:
        verification["method"] = verdict.method
    payload["verification"] = verification
    return payload


_BUILDERS = {"bundle_loaded": _bundle_loaded, "attestation": _attestation}


# --- Envelope assembly. ---------------------------------------------------------------------------


def _envelope(
    record: _Record,
    *,
    event_type: str,
    payload: dict[str, Any],
    subject: str,
    source: str,
    source_class: str,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "specversion": "1.0",
        "id": f"supply-chain:{record.entry_id}",
        "source": source,
        "type": f"org.agent-conformance.evidence.{event_type}.v1",
        "time": record.time,
        "subject": subject,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": source_class,
        "agentceconv": record.convention,
        "data": {"@context": BASE_CONTEXT, "@type": event_type, **payload},
    }
    if record.trace_id:
        event["agentcetrace"] = record.trace_id
    if record.span_id:
        event["agentcespan"] = record.span_id
    if record.parent_span_id:
        event["agentceparent"] = record.parent_span_id
    if record.task_id:
        event["agentcetask"] = record.task_id
    return event


def _source_for(record: _Record, override: str | None) -> str:
    if override is not None:
        return override
    instance = _as_str(record.raw.get("system")) or _as_str(record.raw.get("registry"))
    return f"urn:supply-chain:{instance}" if instance else "urn:supply-chain:unknown"


# --- Public entry point. --------------------------------------------------------------------------


def adapt(
    payload: bytes | str,
    *,
    subject: str,
    source_class: str = "enforcement_point",
    source: str | None = None,
    trusted_keys: Mapping[str, str | None] | Iterable[str] | None = None,
) -> AdaptResult:
    """Adapt one supply-chain log (JSON Lines) into canonical events (SPEC 12).

    ``subject`` is the assessed subject system and ``source_class`` the adapter's declared trust class
    (SPEC 6.4). ``trusted_keys`` maps each trusted signer key id to its public key (PEM or base64 DER
    SPKI; a raw base64 32-byte Ed25519 key is also accepted); a key id given without key material (a
    ``None`` value, or a plain iterable of ids) can never verify. A key of any type other than Ed25519 or
    ECDSA P-256 raises :class:`AdapterError` (``bad_trusted_key``). ``source`` overrides the source URI
    otherwise derived from the system or registry id.
    """
    if source_class not in VALID_SOURCE_CLASSES:
        raise AdapterError("bad_source_class", source_class)
    keys = _trusted_key_table(trusted_keys)
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload

    events: list[dict[str, Any]] = []
    skipped: list[SkippedRecord] = []
    conventions: set[str] = set()
    records_seen = 0

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        records_seen += 1
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            skipped.append(SkippedRecord(line_number, "invalid_json"))
            continue
        if not isinstance(raw, dict):
            skipped.append(SkippedRecord(line_number, "not_an_object"))
            continue
        record = _decode(raw, line_number)
        if record is None:
            skipped.append(SkippedRecord(line_number, "missing_envelope"))
            continue
        if record.kind not in _KINDS:
            skipped.append(SkippedRecord(line_number, "unknown_kind"))
            continue
        body = _BUILDERS[record.kind](record, keys)
        if body is None:
            skipped.append(SkippedRecord(line_number, "incomplete_record"))
            continue
        events.append(
            _envelope(
                record,
                event_type=_KINDS[record.kind],
                payload=body,
                subject=subject,
                source=_source_for(record, source),
                source_class=source_class,
            )
        )
        conventions.add(record.convention)

    events.sort(key=lambda event: (event["time"], event["id"]))
    report = AdapterReport(
        adapter="supply-chain",
        conventions=tuple(sorted(conventions)),
        records_seen=records_seen,
        events_emitted=len(events),
        skipped=tuple(skipped),
    )
    return AdaptResult(events=events, report=report)
