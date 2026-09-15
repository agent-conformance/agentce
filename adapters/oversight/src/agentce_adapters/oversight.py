"""The ``oversight`` adapter: human-oversight records to AgentCE events (SPEC 12).

Human oversight is evidenced by systems outside the agent runtime -- approval gates, workflow engines,
and ticketing systems. When the records are kept by a system in which no principal in the agent's
delegation chain can transition approval states, they are trust class ``independent_system``;
otherwise (an in-process approval prompt, an agent-written log) they are ``self_report`` (SPEC 6.4,
12.2). The class is declared by the caller with a ``class_justification``; the adapter **enforces**
that a claim of ``independent_system`` is justified, and the engine's ``class_mismatch`` check guards
it at ingest.

The adapter is a pure function ``bytes -> AdaptResult`` over a normalised oversight log (JSON Lines,
one record per line; each record names its ``kind`` and the ``source_format`` it came from). It maps:

* ``approval_requested`` -> ``ApprovalRequested``;
* ``approval_decided``   -> ``ApprovalDecided``;
* ``override``           -> ``Override``;
* ``interrupt``          -> ``Interrupt``;
* ``notice``             -> ``Notice``.

A decision, override, or interrupt must name its human actor with a role, an authority reference, and
(for a decision) an identity-provider ``session_ref`` (SPEC 12.2); a record that does not is reported,
never emitted. The adapter contract of SPEC 12.1 holds: deterministic ids, preserved timestamps, a
declared trust class, a recorded convention. Standard library only; no network, no learned component.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: The JSON-LD context every canonical payload carries (SPEC 6.2.2).
BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"

#: The declared trust classes an oversight deployment may assert (SPEC 6.4, 12.2).
VALID_SOURCE_CLASSES = frozenset(
    {"self_report", "enforcement_point", "independent_system"}
)

#: The export origins the adapter normalises (SPEC 12.2); each becomes the recorded convention.
SOURCE_FORMATS = frozenset({"approval_gate", "workflow_engine", "ticketing"})

#: Record ``kind`` -> canonical event type.
_KINDS = {
    "approval_requested": "ApprovalRequested",
    "approval_decided": "ApprovalDecided",
    "override": "Override",
    "interrupt": "Interrupt",
    "notice": "Notice",
}

_APPROVAL_OUTCOMES = frozenset({"approve", "edit", "reject"})
_INTERRUPT_MECHANISMS = frozenset(
    {"stop_button", "kill_switch", "circuit_breaker", "manual"}
)
_INTERRUPT_EFFECTS = frozenset({"halted", "paused", "degraded"})
_NOTICE_TYPES = frozenset({"subject_of_ai_decision", "explanation", "adverse_action"})
_PRINCIPAL_KINDS = frozenset({"human", "service", "agent"})


class AdapterError(ValueError):
    """The input could not be adapted. ``reason`` is a stable key for tests and callers."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class SkippedRecord:
    """An oversight record the adapter did not map, recorded rather than dropped (SPEC 12.1)."""

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
        default_factory=lambda: AdapterReport("oversight", (), 0, 0, ())
    )


# --- Decoding helpers. ----------------------------------------------------------------------------


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _obj(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _human_actor(raw: object) -> dict[str, Any] | None:
    """Build a human actor Principal; a role and an authority reference are required (SPEC 12.2)."""
    data = _obj(raw)
    actor_id = _as_str(data.get("id"))
    role = _as_str(data.get("role"))
    authority_ref = _as_str(data.get("authority_ref"))
    if actor_id is None or role is None or authority_ref is None:
        return None
    kind = _as_str(data.get("kind")) or "human"
    if kind not in _PRINCIPAL_KINDS:
        return None
    actor: dict[str, Any] = {
        "id": actor_id,
        "kind": kind,
        "role": role,
        "authority_ref": authority_ref,
    }
    org = _as_str(data.get("org"))
    if org is not None:
        actor["org"] = org
    return actor


# --- The decoded record. --------------------------------------------------------------------------


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
    source_format: str
    raw: dict[str, Any]


def _decode(raw: dict[str, Any], line: int) -> _Record | None:
    time = _as_str(raw.get("timestamp"))
    entry_id = _as_str(raw.get("id"))
    trace_id = _as_str(raw.get("trace_id"))
    span_id = _as_str(raw.get("span_id"))
    if entry_id is None and trace_id is not None and span_id is not None:
        entry_id = f"{trace_id}/{span_id}"
    kind = _as_str(raw.get("kind"))
    source_format = _as_str(raw.get("source_format"))
    if time is None or entry_id is None or source_format not in SOURCE_FORMATS:
        return None
    version = _as_str(raw.get("convention_version"))
    acted_for = raw.get("acted_for")
    return _Record(
        line=line,
        time=time,
        entry_id=entry_id,
        kind=kind or "",
        convention=f"{source_format}:{version}" if version else source_format,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=_as_str(raw.get("parent_span_id")),
        task_id=_as_str(raw.get("task_id")),
        session_id=_as_str(raw.get("session_id")),
        agent=_obj(raw.get("agent")),
        acted_for=[item for item in acted_for if isinstance(item, str)]
        if isinstance(acted_for, list)
        else [],
        source_format=source_format,
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


# --- Per-kind payload builders (SPEC 12.2). -------------------------------------------------------


def _approval_requested(record: _Record) -> dict[str, Any] | None:
    payload = _base_payload(record)
    for key in ("explanation_ref", "requested_from", "channel", "deadline"):
        value = _as_str(record.raw.get(key))
        if value is not None:
            payload[key] = value
    return payload


def _approval_decided(record: _Record) -> dict[str, Any] | None:
    actor = _human_actor(record.raw.get("actor"))
    session_ref = _as_str(record.raw.get("session_ref"))
    if actor is None or session_ref is None:
        return None
    payload = _base_payload(record)
    payload["actor"] = actor
    payload["session_ref"] = session_ref
    outcome = _as_str(record.raw.get("outcome"))
    if outcome in _APPROVAL_OUTCOMES:
        payload["outcome"] = outcome
    edits_ref = _as_str(record.raw.get("edits_ref"))
    if edits_ref is not None:
        payload["edits_ref"] = edits_ref
    latency = _as_int(record.raw.get("latency_ms"))
    if latency is not None:
        payload["latency_ms"] = latency
    viewed = _as_bool(record.raw.get("explanation_viewed"))
    if viewed is not None:
        payload["explanation_viewed"] = viewed
    return payload


def _override(record: _Record) -> dict[str, Any] | None:
    actor = _human_actor(record.raw.get("actor"))
    if actor is None:
        return None
    payload = _base_payload(record)
    payload["actor"] = actor
    for key in ("original", "replacement", "reason_code"):
        value = _as_str(record.raw.get(key))
        if value is not None:
            payload[key] = value
    return payload


def _interrupt(record: _Record) -> dict[str, Any] | None:
    actor = _human_actor(record.raw.get("actor"))
    if actor is None:
        return None
    payload = _base_payload(record)
    payload["actor"] = actor
    mechanism = _as_str(record.raw.get("mechanism"))
    if mechanism in _INTERRUPT_MECHANISMS:
        payload["mechanism"] = mechanism
    effect = _as_str(record.raw.get("effect"))
    if effect in _INTERRUPT_EFFECTS:
        payload["effect"] = effect
    return payload


def _notice(record: _Record) -> dict[str, Any] | None:
    payload = _base_payload(record)
    person_ref = _as_str(record.raw.get("person_ref"))
    if person_ref is not None:
        payload["person_ref"] = person_ref
    notice_type = _as_str(record.raw.get("notice_type"))
    if notice_type in _NOTICE_TYPES:
        payload["notice_type"] = notice_type
    for key in ("delivered_at", "channel", "content_ref"):
        value = _as_str(record.raw.get(key))
        if value is not None:
            payload[key] = value
    return payload


_BUILDERS = {
    "approval_requested": _approval_requested,
    "approval_decided": _approval_decided,
    "override": _override,
    "interrupt": _interrupt,
    "notice": _notice,
}


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
        "id": f"oversight:{record.entry_id}",
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
    instance = _as_str(record.raw.get("system")) or _as_str(record.raw.get("instance"))
    return (
        f"urn:oversight:{record.source_format}:{instance}"
        if instance
        else f"urn:oversight:{record.source_format}"
    )


# --- Public entry point. --------------------------------------------------------------------------


def adapt(
    payload: bytes | str,
    *,
    subject: str,
    source_class: str = "self_report",
    class_justification: str | None = None,
    source: str | None = None,
) -> AdaptResult:
    """Adapt one normalised oversight log (JSON Lines) into canonical events (SPEC 12).

    ``subject`` is the assessed subject system and ``source_class`` the adapter's declared trust class
    (SPEC 6.4). A claim of ``independent_system`` must be justified: ``class_justification`` is then
    required, or the call is refused -- the adapter's class-justification enforcement (SPEC 12.2).
    ``source`` overrides the source URI otherwise derived from the source format and instance.
    """
    if source_class not in VALID_SOURCE_CLASSES:
        raise AdapterError("bad_source_class", source_class)
    if source_class == "independent_system" and not class_justification:
        raise AdapterError(
            "class_justification_required",
            "independent_system must state why no principal in the chain can transition it",
        )
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
        body = _BUILDERS[record.kind](record)
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
        adapter="oversight",
        conventions=tuple(sorted(conventions)),
        records_seen=records_seen,
        events_emitted=len(events),
        skipped=tuple(skipped),
    )
    return AdaptResult(events=events, report=report)
