"""The ``supply-chain`` adapter: attestations and bundle-load logs to AgentCE events (SPEC 12).

Supply-chain evidence comes from admission controllers and registries -- Sigstore/in-toto
attestations, CycloneDX AIBOMs, registry pins, and runtime bundle-load logs. These records are trust
class ``enforcement_point`` (admission) or ``independent_system`` (registry) (SPEC 6.4, 12.2). The
adapter maps a normalised supply-chain log (JSON Lines, one record per line, each naming its ``kind``
and ``format``):

* ``bundle_loaded`` (a bundle-load log line or a CycloneDX AIBOM) -> ``BundleLoaded``;
* ``attestation``   (a Sigstore/in-toto statement)               -> ``Attestation``.

The adapter **verifies at adapt time** and records the outcome in ``Attestation.verification`` (SPEC
12.2). Verification is deterministic and offline: it checks the digest binding (the attested
``subject_digests`` against the digests actually observed at load) and the signer's trust (the key id
against a vendored trusted-key set). A valid, in-trust, digest-matching attestation is ``verified``; a
digest mismatch or an untrusted signer is ``failed``; an attestation with no signature is
``unverified``. Cryptographic verification of the signature bytes against a Fulcio/Rekor trust root is
a release-time concern (SPEC §8.7, Phase 5), exactly as the engine's integrity verifier defers it.

The adapter contract of SPEC 12.1 holds: deterministic ids, preserved timestamps, a declared trust
class, a recorded convention, no invented events. Standard library only; no network, no learned component.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: The JSON-LD context every canonical payload carries (SPEC 6.2.2).
BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"

#: The declared trust classes a supply-chain deployment may assert (SPEC 6.4, 12.2).
VALID_SOURCE_CLASSES = frozenset(
    {"self_report", "enforcement_point", "independent_system"}
)

#: Record ``kind`` -> canonical event type.
_KINDS = {"bundle_loaded": "BundleLoaded", "attestation": "Attestation"}

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


# --- Verification (deterministic, offline; SPEC 12.2, §8.7 defers signature-byte crypto). ----------


def _verify(
    record: _Record, trusted_keys: frozenset[str]
) -> tuple[str, dict[str, Any]]:
    """Return (status, verification block) for an attestation record."""
    subject = _as_str_list(record.raw.get("subject_digests"))
    signature = _obj(record.raw.get("signature"))
    key_id = _as_str(signature.get("key_id"))
    observed = _as_str_list(record.raw.get("observed_digests"))

    if key_id is None:
        status = "unverified"
    elif key_id not in trusted_keys:
        status = "failed"
    elif observed and set(observed) != set(subject):
        status = (
            "failed"  # the attested subject does not match what was loaded (tampering)
        )
    else:
        status = "verified"

    verification: dict[str, Any] = {"status": status}
    method = _as_str(record.raw.get("method"))
    if method is not None:
        verification["method"] = method
    log_ref = _as_str(record.raw.get("log_ref"))
    if log_ref is not None:
        verification["log_ref"] = log_ref
    return status, verification


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


def _bundle_loaded(record: _Record, _trusted: frozenset[str]) -> dict[str, Any] | None:
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


def _attestation(
    record: _Record, trusted_keys: frozenset[str]
) -> dict[str, Any] | None:
    payload = _base_payload(record)
    statement_type = _as_str(record.raw.get("statement_type"))
    if statement_type is not None:
        payload["statement_type"] = statement_type
    subject_digests = _as_str_list(record.raw.get("subject_digests"))
    if subject_digests:
        payload["subject_digests"] = subject_digests
    signer = _as_str(record.raw.get("signer"))
    if signer is not None:
        payload["signer"] = signer
    _status, verification = _verify(record, trusted_keys)
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
    trusted_keys: Iterable[str] | None = None,
) -> AdaptResult:
    """Adapt one supply-chain log (JSON Lines) into canonical events (SPEC 12).

    ``subject`` is the assessed subject system and ``source_class`` the adapter's declared trust class
    (SPEC 6.4). ``trusted_keys`` is the vendored set of signer key ids the adapter trusts; an
    attestation signed by a key outside it verifies as ``failed``. ``source`` overrides the source URI
    otherwise derived from the system or registry id.
    """
    if source_class not in VALID_SOURCE_CLASSES:
        raise AdapterError("bad_source_class", source_class)
    trusted = frozenset(trusted_keys or ())
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
        body = _BUILDERS[record.kind](record, trusted)
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
