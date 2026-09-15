"""The ``identity`` adapter (SPEC 12, v1.1): IdP and token-exchange logs to AgentCE events.

An identity provider issues and exchanges the tokens an agent's delegation chain is built from. Its
records sit at an enforcement point (the token is not minted without it), so they are trust class
``enforcement_point`` (SPEC 6.4, 12.2). The adapter maps a normalised identity log (JSON Lines, one
issuance or exchange per line) to ``DelegationIssued`` events carrying the delegation chain, the
granted and parent scopes, and the token verification. The chain-attenuation check -- that each hop's
scope is within its parent's -- is engine-side (SPEC 12.2), not the adapter's; the adapter faithfully
records ``scope_granted`` and ``scope_parent`` for the engine to compare.

The adapter contract of SPEC 12.1 holds: deterministic ids, preserved timestamps, a declared trust
class, a recorded convention, no invented events. Standard library only; no network, no learned component.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: The JSON-LD context every canonical payload carries (SPEC 6.2.2).
BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"

#: The declared trust classes an identity deployment may assert (SPEC 6.4, 12.2).
VALID_SOURCE_CLASSES = frozenset(
    {"self_report", "enforcement_point", "independent_system"}
)

_PRINCIPAL_KINDS = frozenset({"human", "service", "agent"})
_VERIFICATION_STATUSES = frozenset({"verified", "unverified", "failed"})


class AdapterError(ValueError):
    """The input could not be adapted. ``reason`` is a stable key for tests and callers."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class SkippedRecord:
    """An identity record the adapter did not map, recorded rather than dropped (SPEC 12.1)."""

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
        default_factory=lambda: AdapterReport("identity", (), 0, 0, ())
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


def _principal(raw: object) -> dict[str, Any] | None:
    data = _obj(raw)
    principal_id = _as_str(data.get("id"))
    kind = _as_str(data.get("kind"))
    if principal_id is None or kind not in _PRINCIPAL_KINDS:
        return None
    principal: dict[str, Any] = {"id": principal_id, "kind": kind}
    for optional in ("role", "authority_ref", "org"):
        value = _as_str(data.get(optional))
        if value is not None:
            principal[optional] = value
    return principal


def _verification(raw: object) -> dict[str, Any]:
    data = _obj(raw)
    verification: dict[str, Any] = {}
    status = _as_str(data.get("status"))
    if status in _VERIFICATION_STATUSES:
        verification["status"] = status
    for optional in ("method", "log_ref"):
        value = _as_str(data.get(optional))
        if value is not None:
            verification[optional] = value
    return verification


@dataclass(frozen=True)
class _Record:
    line: int
    time: str
    entry_id: str
    convention: str
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    task_id: str | None
    session_id: str | None
    agent: dict[str, Any]
    acted_for: list[str]
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
        convention=f"{fmt}:{version}" if version else fmt,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=_as_str(raw.get("parent_span_id")),
        task_id=_as_str(raw.get("task_id")),
        session_id=_as_str(raw.get("session_id")),
        agent=_obj(raw.get("agent")),
        acted_for=_as_str_list(raw.get("acted_for")),
        raw=raw,
    )


def _delegation_issued(record: _Record) -> dict[str, Any] | None:
    """Map an issuance or exchange record to a DelegationIssued payload (needs a token ref)."""
    token_ref = _as_str(record.raw.get("token_ref"))
    if token_ref is None:
        return None
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
    payload["token_ref"] = token_ref
    for key in ("issuer", "subject_principal", "actor_principal", "expires"):
        value = _as_str(record.raw.get(key))
        if value is not None:
            payload[key] = value
    chain = [
        p
        for p in (_principal(item) for item in _list(record.raw.get("chain")))
        if p is not None
    ]
    if chain:
        payload["chain"] = chain
    scope_granted = _as_str_list(record.raw.get("scope_granted"))
    if scope_granted:
        payload["scope_granted"] = scope_granted
    scope_parent = _as_str_list(record.raw.get("scope_parent"))
    if scope_parent:
        payload["scope_parent"] = scope_parent
    verification = _verification(record.raw.get("verification"))
    if verification:
        payload["verification"] = verification
    return payload


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


# --- Envelope assembly. ---------------------------------------------------------------------------


def _envelope(
    record: _Record,
    *,
    payload: dict[str, Any],
    subject: str,
    source: str,
    source_class: str,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "specversion": "1.0",
        "id": f"identity:{record.entry_id}",
        "source": source,
        "type": "org.agent-conformance.evidence.DelegationIssued.v1",
        "time": record.time,
        "subject": subject,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": source_class,
        "agentceconv": record.convention,
        "data": {"@context": BASE_CONTEXT, "@type": "DelegationIssued", **payload},
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
    instance = _as_str(record.raw.get("system")) or _as_str(record.raw.get("issuer"))
    return f"urn:identity:{instance}" if instance else "urn:identity:unknown"


# --- Public entry point. --------------------------------------------------------------------------


def adapt(
    payload: bytes | str,
    *,
    subject: str,
    source_class: str = "enforcement_point",
    source: str | None = None,
) -> AdaptResult:
    """Adapt one identity log (JSON Lines) into canonical DelegationIssued events (SPEC 12, v1.1).

    ``subject`` is the assessed subject system and ``source_class`` the adapter's declared trust class
    (SPEC 6.4). ``source`` overrides the source URI otherwise derived from the system or issuer.
    """
    if source_class not in VALID_SOURCE_CLASSES:
        raise AdapterError("bad_source_class", source_class)
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
        body = _delegation_issued(record)
        if body is None:
            skipped.append(SkippedRecord(line_number, "incomplete_record"))
            continue
        events.append(
            _envelope(
                record,
                payload=body,
                subject=subject,
                source=_source_for(record, source),
                source_class=source_class,
            )
        )
        conventions.add(record.convention)

    events.sort(key=lambda event: (event["time"], event["id"]))
    report = AdapterReport(
        adapter="identity",
        conventions=tuple(sorted(conventions)),
        records_seen=records_seen,
        events_emitted=len(events),
        skipped=tuple(skipped),
    )
    return AdaptResult(events=events, report=report)
