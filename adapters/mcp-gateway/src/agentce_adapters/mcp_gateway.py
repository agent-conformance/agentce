"""The ``mcp-gateway`` adapter: MCP gateway request/response logs to AgentCE events (SPEC 12).

An MCP gateway sits in front of an agent's tool and resource calls, enforces policy, and (when it
performs token exchange) issues delegations. Because it *could have prevented* the action, its records
are trust class ``enforcement_point`` (SPEC 6.4) -- and, unlike an agent's self-report, it can supply
the authorization link (``refs.authorization``) that ties a tool call to the policy decision that
allowed it.

The adapter is a pure function ``bytes -> AdaptResult`` over a gateway log: JSON Lines, one gateway
interaction per line (the shape is documented in the adapter README). It maps:

* ``tools/call`` -> ``ToolCall``;
* ``resources/read`` -> ``ResourceAccess``;
* an entry carrying a ``policy`` block -> ``PolicyDecision`` (and the executed call references it as
  its authorization);
* an entry carrying a ``delegation`` block -> ``DelegationIssued``.

Alongside the events it emits a **source manifest** -- the per-type record counts the gateway
observed -- which the engine uses as an independent coverage denominator for the agent's own
self-reported calls (SPEC 6.5, 6.6). The contract of SPEC 12.1 holds as for every adapter: deterministic
ids, preserved timestamps, a declared (never inferred) trust class, recorded convention, no invented
events, content referenced by locator rather than captured (R12). Standard library only; no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: The JSON-LD context every canonical payload carries (SPEC 6.2.2).
BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"

#: The declared trust classes an mcp-gateway deployment may assert (SPEC 6.4, 12.2).
VALID_SOURCE_CLASSES = frozenset(
    {"self_report", "enforcement_point", "independent_system"}
)

#: The MCP protocol version assumed when a log entry does not state one.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

_POLICY_ENGINES = frozenset({"opa", "cedar", "openfga", "governance_toolkit", "custom"})
_POLICY_DECISIONS = frozenset({"allow", "deny", "require_approval", "transform"})
_PRINCIPAL_KINDS = frozenset({"human", "service", "agent"})
_SIDE_EFFECTS = frozenset({"none", "read", "write", "external", "irreversible"})
_EFFECT_CLASSES = frozenset(
    {"read", "write", "irreversible", "external_communication", "spend", "physical"}
)


class AdapterError(ValueError):
    """The input could not be adapted. ``reason`` is a stable key for tests and callers."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class SkippedEntry:
    """A gateway log entry the adapter did not map, recorded rather than dropped (SPEC 12.1)."""

    line: int
    reason: str


@dataclass(frozen=True)
class AdapterReport:
    """What the adapter did with one gateway log: the conventions and what it could not map."""

    adapter: str
    conventions: tuple[str, ...]
    entries_seen: int
    events_emitted: int
    skipped: tuple[SkippedEntry, ...]


@dataclass
class AdaptResult:
    """The events a gateway log produced, the source manifest, and the run report."""

    events: list[dict[str, Any]] = field(default_factory=list)
    source_manifest: dict[str, Any] = field(default_factory=dict)
    report: AdapterReport = field(
        default_factory=lambda: AdapterReport("mcp-gateway", (), 0, 0, ())
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


def _locator(entry: "_Entry", fragment: str) -> str:
    """A stable, opaque reference back to content in the gateway log (SPEC R12)."""
    return f"mcp:{entry.trace_id}/{entry.span_id}#{fragment}"


# --- The decoded log entry. -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    line: int
    time: str
    gateway: str
    protocol_version: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    task_id: str | None
    session_id: str | None
    agent: dict[str, Any]
    acted_for: list[str]
    request: dict[str, Any]
    response: dict[str, Any]
    effect: dict[str, Any]
    policy: dict[str, Any]
    delegation: dict[str, Any]
    server: str | None
    raw: dict[str, Any]


def _decode_entry(raw: dict[str, Any], line: int) -> _Entry | None:
    """Decode one log entry; return ``None`` if it lacks the fields every event needs."""
    time = _as_str(raw.get("timestamp"))
    trace_id = _as_str(raw.get("trace_id"))
    span_id = _as_str(raw.get("span_id"))
    gateway = _as_str(raw.get("gateway"))
    if time is None or trace_id is None or span_id is None or gateway is None:
        return None
    return _Entry(
        line=line,
        time=time,
        gateway=gateway,
        protocol_version=_as_str(raw.get("protocol_version"))
        or DEFAULT_PROTOCOL_VERSION,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=_as_str(raw.get("parent_span_id")),
        task_id=_as_str(raw.get("task_id")),
        session_id=_as_str(raw.get("session_id")),
        agent=_obj(raw.get("agent")),
        acted_for=_as_str_list(raw.get("acted_for")),
        request=_obj(raw.get("request")),
        response=_obj(raw.get("response")),
        effect=_obj(raw.get("effect")),
        policy=_obj(raw.get("policy")),
        delegation=_obj(raw.get("delegation")),
        server=_as_str(raw.get("server")),
        raw=raw,
    )


# --- Payload builders. ----------------------------------------------------------------------------


def _agent_ref(entry: _Entry) -> dict[str, Any] | None:
    agent_id = _as_str(entry.agent.get("id"))
    if agent_id is None:
        return None
    ref: dict[str, Any] = {"id": agent_id}
    name = _as_str(entry.agent.get("name"))
    if name is not None:
        ref["name"] = name
    return ref


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


def _base_payload(entry: _Entry) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    agent = _agent_ref(entry)
    if agent is not None:
        payload["agent"] = agent
    if entry.acted_for:
        payload["acted_for"] = entry.acted_for
    if entry.session_id is not None:
        payload["session_id"] = entry.session_id
    return payload


def _error_of(entry: _Entry) -> str | None:
    if _as_str(entry.response.get("outcome")) == "error":
        error = _obj(entry.response.get("error"))
        return _as_str(error.get("message")) or "error"
    return None


def _tool_call(entry: _Entry, policy_ref: str | None) -> dict[str, Any]:
    payload = _base_payload(entry)
    params = _obj(entry.request.get("params"))
    tool: dict[str, Any] = {
        "name": _as_str(params.get("name")) or "unknown",
        "protocol": "mcp",
    }
    server = entry.server or _as_str(params.get("server"))
    if server is not None:
        tool["server"] = server
    payload["tool"] = tool
    args_ref = _as_str(params.get("arguments_ref"))
    if args_ref is not None or "arguments" in params:
        payload["args_ref"] = args_ref or _locator(entry, "args")
    result_ref = _as_str(entry.response.get("result_ref"))
    if result_ref is not None or "result" in entry.response:
        payload["result_ref"] = result_ref or _locator(entry, "result")
    side_effect = _as_str(entry.effect.get("side_effect"))
    if side_effect in _SIDE_EFFECTS:
        payload["side_effect"] = side_effect
    effect_class = _as_str(entry.effect.get("effect_class"))
    if effect_class in _EFFECT_CLASSES:
        payload["effect_class"] = effect_class
    error = _error_of(entry)
    if error is not None:
        payload["error"] = error
    if policy_ref is not None:
        payload["refs"] = {"authorization": policy_ref}
    return payload


def _resource_access(entry: _Entry, policy_ref: str | None) -> dict[str, Any]:
    payload = _base_payload(entry)
    params = _obj(entry.request.get("params"))
    resource: dict[str, Any] = {"uri": _as_str(params.get("uri")) or "unknown"}
    kind = _as_str(params.get("kind"))
    if kind is not None:
        resource["kind"] = kind
    payload["resource"] = resource
    payload["operation"] = "read"
    if policy_ref is not None:
        payload["refs"] = {"authorization": policy_ref}
    return payload


def _policy_decision(entry: _Entry, request_ref: str | None) -> dict[str, Any]:
    payload = _base_payload(entry)
    engine = _as_str(entry.policy.get("engine"))
    if engine in _POLICY_ENGINES:
        payload["engine"] = engine
    for optional in ("policy_id", "policy_version", "policy_digest"):
        value = _as_str(entry.policy.get(optional))
        if value is not None:
            payload[optional] = value
    decision = _as_str(entry.policy.get("decision"))
    if decision in _POLICY_DECISIONS:
        payload["decision"] = decision
    reasons = _as_str_list(entry.policy.get("reasons"))
    if reasons:
        payload["reasons"] = reasons
    obligations = _as_str_list(entry.policy.get("obligations"))
    if obligations:
        payload["obligations"] = obligations
    principal = _principal(entry.policy.get("principal"))
    if principal is not None:
        payload["principal"] = principal
    if request_ref is not None:
        payload["refs"] = {"request": request_ref}
    return payload


def _delegation_issued(entry: _Entry) -> dict[str, Any]:
    payload = _base_payload(entry)
    delegation = entry.delegation
    for optional in ("token_ref", "issuer", "subject_principal", "actor_principal"):
        value = _as_str(delegation.get(optional))
        if value is not None:
            payload[optional] = value
    chain = [
        principal
        for principal in (
            _principal(item) for item in _as_list(delegation.get("chain"))
        )
        if principal
    ]
    if chain:
        payload["chain"] = chain
    scope_granted = _as_str_list(delegation.get("scope_granted"))
    if scope_granted:
        payload["scope_granted"] = scope_granted
    scope_parent = _as_str_list(delegation.get("scope_parent"))
    if scope_parent:
        payload["scope_parent"] = scope_parent
    expires = _as_str(delegation.get("expires"))
    if expires is not None:
        payload["expires"] = expires
    verification = _verification(delegation.get("verification"))
    if verification:
        payload["verification"] = verification
    return payload


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _verification(raw: object) -> dict[str, Any]:
    data = _obj(raw)
    verification: dict[str, Any] = {}
    status = _as_str(data.get("status"))
    if status in {"verified", "unverified", "failed"}:
        verification["status"] = status
    for optional in ("method", "log_ref"):
        value = _as_str(data.get(optional))
        if value is not None:
            verification[optional] = value
    return verification


# --- Envelope assembly. ---------------------------------------------------------------------------


def _envelope(
    entry: _Entry,
    *,
    event_type: str,
    event_id: str,
    payload: dict[str, Any],
    subject: str,
    source: str,
    source_class: str,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "specversion": "1.0",
        "id": event_id,
        "source": source,
        "type": f"org.agent-conformance.evidence.{event_type}.v1",
        "time": entry.time,
        "subject": subject,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": source_class,
        "agentceconv": f"mcp:{entry.protocol_version}",
        "data": {"@context": BASE_CONTEXT, "@type": event_type, **payload},
    }
    event["agentcetrace"] = entry.trace_id
    event["agentcespan"] = entry.span_id
    if entry.parent_span_id:
        event["agentceparent"] = entry.parent_span_id
    if entry.task_id:
        event["agentcetask"] = entry.task_id
    return event


def _method_event_type(entry: _Entry) -> str | None:
    method = _as_str(entry.request.get("method"))
    if method == "tools/call":
        return "ToolCall"
    if method == "resources/read":
        return "ResourceAccess"
    return None


# --- Public entry point. --------------------------------------------------------------------------


def adapt(
    payload: bytes | str,
    *,
    subject: str,
    source_class: str = "enforcement_point",
    source: str | None = None,
) -> AdaptResult:
    """Adapt one MCP gateway log (JSON Lines) into canonical events and a source manifest (SPEC 12).

    ``subject`` is the assessed subject system and ``source_class`` the adapter's declared trust class
    (SPEC 6.4); both are properties of the deployment, never inferred from log contents. ``source``
    overrides the per-entry source URI otherwise derived from the gateway id.
    """
    if source_class not in VALID_SOURCE_CLASSES:
        raise AdapterError("bad_source_class", source_class)
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload

    events: list[dict[str, Any]] = []
    skipped: list[SkippedEntry] = []
    conventions: set[str] = set()
    sources: set[str] = set()
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
        entry = _decode_entry(raw, line_number)
        if entry is None:
            skipped.append(SkippedEntry(line_number, "missing_envelope"))
            continue
        emitted = _map_entry(
            entry, subject=subject, source_class=source_class, source=source
        )
        if not emitted:
            skipped.append(SkippedEntry(line_number, "unmapped_entry"))
            continue
        conventions.add(f"mcp:{entry.protocol_version}")
        for event in emitted:
            sources.add(str(event["source"]))
        events.extend(emitted)

    events.sort(key=lambda event: (event["time"], event["id"]))
    manifest = _source_manifest(events, subject, sorted(sources))
    report = AdapterReport(
        adapter="mcp-gateway",
        conventions=tuple(sorted(conventions)),
        entries_seen=entries_seen,
        events_emitted=len(events),
        skipped=tuple(skipped),
    )
    return AdaptResult(events=events, source_manifest=manifest, report=report)


def _map_entry(
    entry: _Entry,
    *,
    subject: str,
    source_class: str,
    source: str | None,
) -> list[dict[str, Any]]:
    """Return the events one gateway log entry maps to (SPEC 12.2)."""
    resolved_source = (
        source if source is not None else f"urn:mcp-gateway:{entry.gateway}"
    )

    def envelope(
        event_type: str, event_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _envelope(
            entry,
            event_type=event_type,
            event_id=event_id,
            payload=body,
            subject=subject,
            source=resolved_source,
            source_class=source_class,
        )

    base_id = f"mcp:{entry.trace_id}/{entry.span_id}"
    method_type = _method_event_type(entry)
    has_policy = bool(entry.policy)
    has_delegation = bool(entry.delegation)

    events: list[dict[str, Any]] = []
    policy_ref = f"agentce:event/{base_id}#policy" if has_policy else None
    # A denied policy blocks the call, so the method event is only emitted when the gateway allowed it.
    decision = _as_str(entry.policy.get("decision"))
    executed = method_type is not None and decision != "deny"
    method_ref = f"agentce:event/{base_id}" if executed else None

    if has_policy:
        events.append(
            envelope(
                "PolicyDecision",
                f"{base_id}#policy",
                _policy_decision(entry, method_ref),
            )
        )
    if executed and method_type == "ToolCall":
        events.append(envelope("ToolCall", base_id, _tool_call(entry, policy_ref)))
    elif executed and method_type == "ResourceAccess":
        events.append(
            envelope("ResourceAccess", base_id, _resource_access(entry, policy_ref))
        )
    if has_delegation:
        events.append(
            envelope(
                "DelegationIssued", f"{base_id}#delegation", _delegation_issued(entry)
            )
        )
    return events


def _source_manifest(
    events: list[dict[str, Any]], subject: str, sources: list[str]
) -> dict[str, Any]:
    """The per-type record counts the gateway observed, an independent denominator (SPEC 6.5, 6.6)."""
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event["data"]["@type"])
        counts[event_type] = counts.get(event_type, 0) + 1
    return {
        "adapter": "mcp-gateway",
        "subject": subject,
        "sources": sources,
        "counts": dict(sorted(counts.items())),
    }
