"""The ``policy-engines`` adapter: authorization decision logs to AgentCE events (SPEC 12).

Policy engines sit at an enforcement point and decide whether an action is allowed, so their records
are trust class ``enforcement_point`` (SPEC 6.4). This adapter has one sub-adapter per engine, all
producing the same canonical output (SPEC 12.2):

* **OpenFGA** relationship-check logs -> ``AuthzCheck`` (user / relation / object / allowed / store);
* **OPA** decision logs -> ``PolicyDecision``;
* **Cedar** authorization logs -> ``PolicyDecision`` (a thin sub-adapter, exercised by fixtures);
* **governance-toolkit** audit logs -> ``PolicyDecision`` (a thin sub-adapter, exercised by fixtures).

The engine is chosen by the caller (``adapt(..., engine=...)``), because the collector knows which
engine's logs it is reading. Input is JSON Lines, one decision per line, wrapped in a small envelope
(timestamp, a stable decision id, optional trace context and principal). The adapter contract of
SPEC 12.1 holds: deterministic ids, preserved timestamps, a declared trust class, a recorded
convention, and no invented events. Standard library only; no network, no learned component.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: The JSON-LD context every canonical payload carries (SPEC 6.2.2).
BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"

#: The declared trust classes a policy-engine deployment may assert (SPEC 6.4, 12.2).
VALID_SOURCE_CLASSES = frozenset(
    {"self_report", "enforcement_point", "independent_system"}
)

#: The engines this adapter has a sub-adapter for (SPEC 12.2).
ENGINES = ("openfga", "opa", "cedar", "governance_toolkit")

_PRINCIPAL_KINDS = frozenset({"human", "service", "agent"})

#: Raw decision spellings each engine uses, mapped to the canonical PolicyDecisionOutcome (SPEC App. F).
_DECISION_ALIASES = {
    "allow": "allow",
    "allowed": "allow",
    "permit": "allow",
    "deny": "deny",
    "denied": "deny",
    "forbid": "deny",
    "require_approval": "require_approval",
    "conditional": "require_approval",
    "transform": "transform",
}


class AdapterError(ValueError):
    """The input could not be adapted. ``reason`` is a stable key for tests and callers."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class SkippedEntry:
    """A decision-log entry the adapter did not map, recorded rather than dropped (SPEC 12.1)."""

    line: int
    reason: str


@dataclass(frozen=True)
class AdapterReport:
    adapter: str
    conventions: tuple[str, ...]
    entries_seen: int
    events_emitted: int
    skipped: tuple[SkippedEntry, ...]


@dataclass
class AdaptResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    report: AdapterReport = field(
        default_factory=lambda: AdapterReport("policy-engines", (), 0, 0, ())
    )


# --- Decoding helpers. ----------------------------------------------------------------------------


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


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


def _decision(raw: object) -> str | None:
    """Normalise an engine's raw decision (bool or string) to a PolicyDecisionOutcome."""
    if isinstance(raw, bool):
        return "allow" if raw else "deny"
    text = _as_str(raw)
    if text is None:
        return None
    return _DECISION_ALIASES.get(text.lower())


# --- The decoded envelope shared by every engine. -------------------------------------------------


@dataclass(frozen=True)
class _Entry:
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


def _decode_entry(raw: dict[str, Any], engine: str, line: int) -> _Entry | None:
    """Decode the common envelope; return ``None`` if it lacks a timestamp or a stable id."""
    time = _as_str(raw.get("timestamp"))
    entry_id = (
        _as_str(raw.get("id"))
        or _as_str(raw.get("decision_id"))
        or _as_str(raw.get("check_id"))
        or _as_str(raw.get("request_id"))
    )
    trace_id = _as_str(raw.get("trace_id"))
    span_id = _as_str(raw.get("span_id"))
    if entry_id is None and trace_id is not None and span_id is not None:
        entry_id = f"{trace_id}/{span_id}"
    if time is None or entry_id is None:
        return None
    version = _as_str(raw.get("convention_version"))
    return _Entry(
        line=line,
        time=time,
        entry_id=entry_id,
        convention=f"{engine}:{version}" if version else engine,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=_as_str(raw.get("parent_span_id")),
        task_id=_as_str(raw.get("task_id")),
        session_id=_as_str(raw.get("session_id")),
        agent=_obj(raw.get("agent")),
        acted_for=_as_str_list(raw.get("acted_for")),
        raw=raw,
    )


def _base_payload(entry: _Entry) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    agent_id = _as_str(entry.agent.get("id"))
    if agent_id is not None:
        agent: dict[str, Any] = {"id": agent_id}
        name = _as_str(entry.agent.get("name"))
        if name is not None:
            agent["name"] = name
        payload["agent"] = agent
    if entry.acted_for:
        payload["acted_for"] = entry.acted_for
    if entry.session_id is not None:
        payload["session_id"] = entry.session_id
    return payload


# --- Sub-adapters: one per engine, all producing the same canonical shape (SPEC 12.2). ------------


def _openfga(entry: _Entry) -> tuple[str, dict[str, Any]] | None:
    """OpenFGA relationship check -> AuthzCheck."""
    tuple_key = _obj(entry.raw.get("tuple_key"))
    user = _as_str(tuple_key.get("user"))
    relation = _as_str(tuple_key.get("relation"))
    obj = _as_str(tuple_key.get("object"))
    allowed = _as_bool(entry.raw.get("allowed"))
    if user is None or relation is None or obj is None or allowed is None:
        return None
    payload = _base_payload(entry)
    payload["object"] = obj
    payload["relation"] = relation
    payload["user"] = user
    payload["allowed"] = allowed
    store = _as_str(entry.raw.get("store_id"))
    if store is not None:
        payload["store_ref"] = store
    return "AuthzCheck", payload


def _policy_decision(entry: _Entry, engine: str) -> tuple[str, dict[str, Any]] | None:
    """OPA / Cedar / governance-toolkit decision -> PolicyDecision."""
    decision = _decision(
        entry.raw.get("decision")
        if "decision" in entry.raw
        else entry.raw.get("result")
    )
    if decision is None:
        return None
    payload = _base_payload(entry)
    payload["engine"] = engine
    policy_id = _as_str(entry.raw.get("policy_id")) or _as_str(entry.raw.get("path"))
    if policy_id is not None:
        payload["policy_id"] = policy_id
    version = _as_str(entry.raw.get("policy_version")) or _as_str(
        entry.raw.get("revision")
    )
    if version is not None:
        payload["policy_version"] = version
    digest = _as_str(entry.raw.get("policy_digest"))
    if digest is not None:
        payload["policy_digest"] = digest
    payload["decision"] = decision
    reasons = _as_str_list(entry.raw.get("reasons"))
    if reasons:
        payload["reasons"] = reasons
    obligations = _as_str_list(entry.raw.get("obligations"))
    if obligations:
        payload["obligations"] = obligations
    principal = _principal(entry.raw.get("principal"))
    if principal is not None:
        payload["principal"] = principal
    return "PolicyDecision", payload


def _sub_adapter(engine: str) -> Callable[[_Entry], tuple[str, dict[str, Any]] | None]:
    if engine == "openfga":
        return _openfga
    return lambda entry: _policy_decision(entry, engine)


# --- Envelope assembly. ---------------------------------------------------------------------------


def _envelope(
    entry: _Entry,
    *,
    event_type: str,
    payload: dict[str, Any],
    engine: str,
    subject: str,
    source: str,
    source_class: str,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "specversion": "1.0",
        "id": f"{engine}:{entry.entry_id}",
        "source": source,
        "type": f"org.agent-conformance.evidence.{event_type}.v1",
        "time": entry.time,
        "subject": subject,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": source_class,
        "agentceconv": entry.convention,
        "data": {"@context": BASE_CONTEXT, "@type": event_type, **payload},
    }
    if entry.trace_id:
        event["agentcetrace"] = entry.trace_id
    if entry.span_id:
        event["agentcespan"] = entry.span_id
    if entry.parent_span_id:
        event["agentceparent"] = entry.parent_span_id
    if entry.task_id:
        event["agentcetask"] = entry.task_id
    return event


# --- Public entry point. --------------------------------------------------------------------------


def adapt(
    payload: bytes | str,
    *,
    engine: str,
    subject: str,
    source_class: str = "enforcement_point",
    source: str | None = None,
) -> AdaptResult:
    """Adapt one policy engine's decision log (JSON Lines) into canonical events (SPEC 12).

    ``engine`` selects the sub-adapter (one of ``ENGINES``). ``subject`` is the assessed subject
    system and ``source_class`` the adapter's declared trust class (SPEC 6.4); both are properties of
    the deployment, never inferred from log contents. ``source`` overrides the source URI otherwise
    derived from the engine and instance.
    """
    if source_class not in VALID_SOURCE_CLASSES:
        raise AdapterError("bad_source_class", source_class)
    if engine not in ENGINES:
        raise AdapterError("unknown_engine", engine)
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    sub_adapter = _sub_adapter(engine)

    events: list[dict[str, Any]] = []
    skipped: list[SkippedEntry] = []
    conventions: set[str] = set()
    entries_seen = 0

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        entries_seen += 1
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            skipped.append(SkippedEntry(line_number, "invalid_json"))
            continue
        if not isinstance(raw, dict):
            skipped.append(SkippedEntry(line_number, "not_an_object"))
            continue
        entry = _decode_entry(raw, engine, line_number)
        if entry is None:
            skipped.append(SkippedEntry(line_number, "missing_envelope"))
            continue
        mapped = sub_adapter(entry)
        if mapped is None:
            skipped.append(SkippedEntry(line_number, "incomplete_decision"))
            continue
        event_type, body = mapped
        resolved_source = source if source is not None else _source_for(engine, raw)
        events.append(
            _envelope(
                entry,
                event_type=event_type,
                payload=body,
                engine=engine,
                subject=subject,
                source=resolved_source,
                source_class=source_class,
            )
        )
        conventions.add(entry.convention)

    events.sort(key=lambda event: (event["time"], event["id"]))
    report = AdapterReport(
        adapter="policy-engines",
        conventions=tuple(sorted(conventions)),
        entries_seen=entries_seen,
        events_emitted=len(events),
        skipped=tuple(skipped),
    )
    return AdaptResult(events=events, report=report)


def _source_for(engine: str, raw: dict[str, Any]) -> str:
    instance = _as_str(raw.get("instance")) or _as_str(raw.get("store_id"))
    return (
        f"urn:policy-engine:{engine}:{instance}"
        if instance
        else f"urn:policy-engine:{engine}"
    )
