"""The ``otel-genai`` adapter: OpenTelemetry GenAI and OpenInference spans to AgentCE events.

This module implements the adapter contract of SPEC 12.1 for the ``otel-genai`` source. It is a pure
function ``bytes -> (events, AdapterReport)`` over an OTLP/JSON trace export (an
``ExportTraceServiceRequest`` document, or the equivalent shape written by OpenInference exporters).
It:

* assigns **deterministic** event ids derived from the source trace and span ids, so re-adapting the
  same export yields the same ids;
* **preserves** the source span timestamps, converted to the canonical RFC 3339 UTC millisecond form
  (SPEC 6.7) without inventing precision;
* sets ``agentcesourceclass`` from the adapter's **declared** trust class, never inferred per event
  (SPEC 6.4): OTel GenAI is ``self_report`` by default, or ``enforcement_point`` when the collector
  sits at a gateway;
* records ``agentceconv`` as the upstream convention version it mapped, detected per span from the
  OTLP schema URL (``otel-genai:<major.minor>``) or the OpenInference instrumentation scope
  (``openinference:<version>``); the ``gen_ai.*`` attribute mapping is version-aware, accepting both
  the current ``usage.input_tokens``/``output_tokens`` and the legacy
  ``usage.prompt_tokens``/``completion_tokens`` spellings;
* **never invents** events: a span the adapter does not recognise is reported as skipped, never
  emitted, and content is referenced by an opaque locator rather than captured (SPEC R12);
* is deterministic and free of any network access or learned component.

The engine consults the companion ``support-matrix.yaml`` to explain ``insufficient_evidence``
outcomes; ``check_support_matrix`` proves the matrix stays consistent with what this adapter emits.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

#: The JSON-LD context every canonical payload carries (SPEC 6.2.2).
BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"

#: The declared trust classes an otel-genai deployment may assert (SPEC 6.4, 12.2).
VALID_SOURCE_CLASSES = frozenset(
    {"self_report", "enforcement_point", "independent_system"}
)


class AdapterError(ValueError):
    """The input could not be adapted. ``reason`` is a stable key for tests and callers."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class SkippedSpan:
    """A source span the adapter did not map, recorded rather than dropped silently (SPEC 12.1)."""

    span_id: str
    name: str
    reason: str


@dataclass(frozen=True)
class AdapterReport:
    """What the adapter did with one export: the conventions it mapped and what it could not map."""

    adapter: str
    conventions: tuple[str, ...]
    spans_seen: int
    events_emitted: int
    skipped: tuple[SkippedSpan, ...]


@dataclass
class AdaptResult:
    """The events an export produced, in canonical time order, and the run report."""

    events: list[dict[str, Any]] = field(default_factory=list)
    report: AdapterReport = field(
        default_factory=lambda: AdapterReport("otel-genai", (), 0, 0, ())
    )


# --- OTLP/JSON decoding helpers. ------------------------------------------------------------------


def _any_value(value: object) -> object:
    """Decode one OTLP ``AnyValue`` to a plain Python scalar/list (protobuf-JSON encoding)."""
    if not isinstance(value, dict):
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        # int64 is JSON-encoded as a string in the OTLP/JSON protobuf mapping.
        raw = value["intValue"]
        return int(raw) if isinstance(raw, str) else raw
    if "boolValue" in value:
        return value["boolValue"]
    if "doubleValue" in value:
        return value["doubleValue"]
    if "arrayValue" in value:
        inner = value["arrayValue"]
        items = inner.get("values", []) if isinstance(inner, dict) else []
        return [_any_value(item) for item in items]
    if "kvlistValue" in value:
        inner = value["kvlistValue"]
        pairs = inner.get("values", []) if isinstance(inner, dict) else []
        return {
            p["key"]: _any_value(p.get("value")) for p in pairs if isinstance(p, dict)
        }
    return None


def _attributes(raw: object) -> dict[str, object]:
    """Turn an OTLP attribute list ``[{key, value}]`` into a flat ``{key: scalar}`` dict."""
    out: dict[str, object] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("key"), str):
                out[item["key"]] = _any_value(item.get("value"))
    return out


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _rfc3339_millis(unix_nanos: object) -> str | None:
    """Format a Unix-nanoseconds timestamp as canonical RFC 3339 UTC with millisecond precision."""
    nanos = _as_int(unix_nanos)
    if nanos is None or nanos < 0:
        return None
    millis_total, _ = divmod(
        nanos, 1_000_000
    )  # truncate to milliseconds; never round up
    seconds, millis = divmod(millis_total, 1000)
    moment = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


# --- Convention detection. ------------------------------------------------------------------------


def _otel_genai_version(*schema_urls: object) -> str | None:
    """Extract ``<major.minor>`` from the first OTLP schema URL that carries one."""
    for candidate in schema_urls:
        url = _as_str(candidate)
        if not url:
            continue
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        parts = tail.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{parts[0]}.{parts[1]}"
    return None


# --- Span mapping. --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Span:
    """The decoded fields of one OTLP span the mapper needs."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start: str | None
    end: str | None
    attrs: dict[str, object]
    status_code: int
    status_message: str | None
    convention: str


def _iter_spans(document: dict[str, object]) -> Iterator[_Span]:
    """Yield every span in an OTLP/JSON trace export, tagged with its convention version."""
    resource_spans = document.get("resourceSpans")
    if not isinstance(resource_spans, list):
        raise AdapterError("not_otlp", "document has no resourceSpans array")
    for resource_span in resource_spans:
        if not isinstance(resource_span, dict):
            continue
        resource = resource_span.get("resource")
        resource_attrs = _attributes(
            resource.get("attributes") if isinstance(resource, dict) else None
        )
        resource_schema = resource_span.get("schemaUrl")
        scope_spans = resource_span.get("scopeSpans")
        if not isinstance(scope_spans, list):
            continue
        for scope_span in scope_spans:
            if not isinstance(scope_span, dict):
                continue
            scope = scope_span.get("scope")
            scope_name = (
                _as_str(scope.get("name") if isinstance(scope, dict) else None) or ""
            )
            scope_version = _as_str(
                scope.get("version") if isinstance(scope, dict) else None
            )
            scope_schema = scope_span.get("schemaUrl")
            spans = scope_span.get("spans")
            if not isinstance(spans, list):
                continue
            for span in spans:
                if not isinstance(span, dict):
                    continue
                attrs = _attributes(span.get("attributes"))
                convention = _convention_for(
                    scope_name, scope_version, scope_schema, resource_schema, attrs
                )
                status = span.get("status")
                status_code = 0
                status_message: str | None = None
                if isinstance(status, dict):
                    status_code = _as_int(status.get("code")) or 0
                    status_message = _as_str(status.get("message"))
                merged = {**resource_attrs, **attrs}
                yield _Span(
                    trace_id=_as_str(span.get("traceId")) or "",
                    span_id=_as_str(span.get("spanId")) or "",
                    parent_span_id=_as_str(span.get("parentSpanId")),
                    name=_as_str(span.get("name")) or "",
                    start=_rfc3339_millis(span.get("startTimeUnixNano")),
                    end=_rfc3339_millis(span.get("endTimeUnixNano")),
                    attrs=merged,
                    status_code=status_code,
                    status_message=status_message,
                    convention=convention,
                )


def _convention_for(
    scope_name: str,
    scope_version: str | None,
    scope_schema: object,
    resource_schema: object,
    attrs: dict[str, object],
) -> str:
    """Name the upstream convention version for one span (SPEC 12.2, version-aware)."""
    if "openinference.span.kind" in attrs or scope_name.startswith("openinference"):
        return f"openinference:{scope_version}" if scope_version else "openinference"
    version = _otel_genai_version(scope_schema, resource_schema)
    return f"otel-genai:{version}" if version else "otel-genai"


def _openinference_operation(attrs: dict[str, object]) -> str | None:
    """Map an OpenInference span kind to the adapter's internal operation name.

    A kind that is present but not mapped (``CHAIN``, ``RERANKER``, ...) returns itself, so the caller
    can tell an unrecognised kind (reported as ``unrecognised_operation``) from a span that carries no
    operation signal at all (``missing_operation``).
    """
    kind = _as_str(attrs.get("openinference.span.kind"))
    if kind is None:
        return None
    return {
        "LLM": "chat",
        "EMBEDDING": "embeddings",
        "TOOL": "execute_tool",
        "AGENT": "invoke_agent",
        "RETRIEVER": "retrieve",
    }.get(kind, kind)


def _operation(span: _Span) -> str | None:
    """Resolve the AgentCE-relevant operation for a span across both conventions."""
    if span.convention.startswith("openinference"):
        return _openinference_operation(span.attrs)
    return _as_str(span.attrs.get("gen_ai.operation.name"))


def _agent_ref(attrs: dict[str, object]) -> dict[str, Any] | None:
    """Build an ``AgentRef`` from ``gen_ai.agent.*`` attributes (id is required)."""
    agent_id = _as_str(attrs.get("gen_ai.agent.id"))
    if agent_id is None:
        return None
    agent: dict[str, Any] = {"id": agent_id}
    name = _as_str(attrs.get("gen_ai.agent.name"))
    if name is not None:
        agent["name"] = name
    return agent


def _first_str(attrs: dict[str, object], *keys: str) -> str | None:
    """Return the first present, non-empty string attribute among ``keys`` (version-aware lookup)."""
    for key in keys:
        found = _as_str(attrs.get(key))
        if found is not None:
            return found
    return None


def _first_int(attrs: dict[str, object], *keys: str) -> int | None:
    for key in keys:
        if key in attrs:
            found = _as_int(attrs.get(key))
            if found is not None:
                return found
    return None


def _locator(span: _Span, fragment: str) -> str:
    """A stable, opaque reference back to content that lives in the source span (SPEC R12).

    The adapter references content by location instead of copying it, so no prompt, completion, tool
    argument, or tool result is captured into the evidence event.
    """
    return f"otel:{span.trace_id}/{span.span_id}#{fragment}"


def _end_reason(span: _Span) -> str:
    return "error" if span.status_code == 2 else "completed"


def _error_of(span: _Span) -> str | None:
    if span.status_code == 2:
        return span.status_message or "error"
    return None


def _base_payload(span: _Span) -> dict[str, Any]:
    """The common payload members every mapping starts from (agent, session)."""
    payload: dict[str, Any] = {}
    agent = _agent_ref(span.attrs)
    if agent is not None:
        payload["agent"] = agent
    session_id = _first_str(span.attrs, "gen_ai.conversation.id", "session.id")
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def _model_call(span: _Span, operation: str) -> dict[str, Any]:
    payload = _base_payload(span)
    payload["operation"] = operation
    model: dict[str, Any] = {}
    provider = _first_str(
        span.attrs, "gen_ai.system", "gen_ai.provider.name", "llm.provider"
    )
    if provider is not None:
        model["provider"] = provider
    name = _first_str(span.attrs, "gen_ai.request.model", "llm.model_name")
    if name is not None:
        model["name"] = name
    resolved = _first_str(span.attrs, "gen_ai.response.model")
    if resolved is not None:
        model["version_or_digest"] = resolved
    if model:
        payload["model"] = model
    usage: dict[str, Any] = {}
    input_tokens = _first_int(
        span.attrs,
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.prompt_tokens",
        "llm.token_count.prompt",
    )
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    output_tokens = _first_int(
        span.attrs,
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.completion_tokens",
        "llm.token_count.completion",
    )
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    if usage:
        payload["usage"] = usage
    if _has_input(span):
        payload["input_ref"] = _locator(span, "input")
    if _has_output(span):
        payload["output_ref"] = _locator(span, "output")
    error = _error_of(span)
    if error is not None:
        payload["error"] = error
    return payload


def _has_input(span: _Span) -> bool:
    return any(
        key in span.attrs
        for key in ("gen_ai.input.messages", "gen_ai.prompt", "input.value")
    )


def _has_output(span: _Span) -> bool:
    return any(
        key in span.attrs
        for key in ("gen_ai.output.messages", "gen_ai.completion", "output.value")
    )


def _tool_call(span: _Span) -> dict[str, Any]:
    payload = _base_payload(span)
    tool_name = _first_str(span.attrs, "gen_ai.tool.name", "tool.name") or span.name
    tool: dict[str, Any] = {"name": tool_name}
    server = _first_str(span.attrs, "gen_ai.tool.server", "server.address")
    if server is not None:
        tool["server"] = server
    protocol = _first_str(span.attrs, "gen_ai.tool.protocol")
    if protocol in {"mcp", "a2a", "http", "native"}:
        tool["protocol"] = protocol
    payload["tool"] = tool
    if any(
        key in span.attrs
        for key in ("gen_ai.tool.call.arguments", "tool.parameters", "input.value")
    ):
        payload["args_ref"] = _locator(span, "args")
    if any(key in span.attrs for key in ("gen_ai.tool.call.result", "output.value")):
        payload["result_ref"] = _locator(span, "result")
    error = _error_of(span)
    if error is not None:
        payload["error"] = error
    return payload


def _resource_access(span: _Span) -> dict[str, Any]:
    payload = _base_payload(span)
    uri = _first_str(
        span.attrs, "gen_ai.data_source.id", "retrieval.source", "db.collection.name"
    )
    resource: dict[str, Any] = {"uri": uri or span.name}
    kind = _first_str(span.attrs, "gen_ai.data_source.kind")
    if kind is not None:
        resource["kind"] = kind
    payload["resource"] = resource
    payload["operation"] = "read"
    count = _first_int(
        span.attrs, "gen_ai.retrieval.document.count", "retrieval.documents.count"
    )
    if count is not None:
        payload["count"] = count
    return payload


def _memory_write(span: _Span) -> dict[str, Any]:
    payload = _base_payload(span)
    store = _first_str(span.attrs, "gen_ai.memory.store")
    if store is not None:
        payload["store"] = store
    record = _first_str(span.attrs, "gen_ai.memory.record.id")
    if record is not None:
        payload["record_ref"] = record
    trust = _first_str(span.attrs, "gen_ai.memory.trust")
    if trust in {"trusted", "untrusted", "quarantined"}:
        payload["trust"] = trust
    return payload


def _memory_read(span: _Span) -> dict[str, Any]:
    payload = _base_payload(span)
    store = _first_str(span.attrs, "gen_ai.memory.store")
    if store is not None:
        payload["store"] = store
    records = span.attrs.get("gen_ai.memory.record.ids")
    if isinstance(records, list):
        record_refs = [item for item in records if isinstance(item, str)]
        if record_refs:
            payload["record_refs"] = record_refs
    trust = _first_str(span.attrs, "gen_ai.memory.trust_min")
    if trust in {"trusted", "untrusted", "quarantined"}:
        payload["trust_min"] = trust
    return payload


def _session_start(span: _Span) -> dict[str, Any]:
    payload = _base_payload(span)
    environment = _first_str(
        span.attrs, "deployment.environment.name", "deployment.environment"
    )
    if environment is not None:
        payload["environment"] = environment
    return payload


def _session_end(span: _Span) -> dict[str, Any]:
    payload = _base_payload(span)
    payload["end_reason"] = _end_reason(span)
    return payload


# --- Envelope assembly. ---------------------------------------------------------------------------


def _envelope(
    span: _Span,
    *,
    event_type: str,
    event_id: str,
    time: str,
    payload: dict[str, Any],
    subject: str,
    source: str,
    source_class: str,
) -> dict[str, Any]:
    """Wrap a payload in the CloudEvents envelope with AgentCE extension attributes (SPEC 6.2.1)."""
    event: dict[str, Any] = {
        "specversion": "1.0",
        "id": event_id,
        "source": source,
        "type": f"org.agent-conformance.evidence.{event_type}.v1",
        "time": time,
        "subject": subject,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": source_class,
        "agentceconv": span.convention,
        "data": {"@context": BASE_CONTEXT, "@type": event_type, **payload},
    }
    if span.trace_id:
        event["agentcetrace"] = span.trace_id
    if span.span_id:
        event["agentcespan"] = span.span_id
    if span.parent_span_id:
        event["agentceparent"] = span.parent_span_id
    task = _first_str(span.attrs, "agentce.task", "a2a.task.id", "gen_ai.task.id")
    if task is not None:
        event["agentcetask"] = task
    return event


def _source_for(span: _Span, override: str | None) -> str:
    if override is not None:
        return override
    service = _first_str(span.attrs, "service.name")
    return f"urn:otel:{service}" if service is not None else "urn:otel:unknown"


# --- Public entry point. --------------------------------------------------------------------------


def adapt(
    payload: bytes | str,
    *,
    subject: str,
    source_class: str = "self_report",
    source: str | None = None,
) -> AdaptResult:
    """Adapt one OTLP/JSON GenAI trace export into canonical AgentCE evidence events (SPEC 12).

    ``subject`` is the assessed subject system and ``source_class`` the adapter's declared trust class
    (SPEC 6.4) -- both are properties of the deployment, supplied by the collector, never inferred
    from span contents. ``source`` overrides the per-span source URI otherwise derived from
    ``service.name``.
    """
    if source_class not in VALID_SOURCE_CLASSES:
        raise AdapterError("bad_source_class", source_class)
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AdapterError("invalid_json", exc.msg) from exc
    if not isinstance(document, dict):
        raise AdapterError("not_otlp", "top-level value is not a JSON object")

    events: list[dict[str, Any]] = []
    skipped: list[SkippedSpan] = []
    conventions: set[str] = set()
    spans_seen = 0

    for span in _iter_spans(document):
        spans_seen += 1
        operation = _operation(span)
        emitted = _map_span(
            span, operation, subject=subject, source_class=source_class, source=source
        )
        if not emitted:
            reason = (
                "missing_operation" if operation is None else "unrecognised_operation"
            )
            skipped.append(SkippedSpan(span.span_id, span.name, reason))
            continue
        conventions.add(span.convention)
        events.extend(emitted)

    events.sort(key=lambda event: (event["time"], event["id"]))
    report = AdapterReport(
        adapter="otel-genai",
        conventions=tuple(sorted(conventions)),
        spans_seen=spans_seen,
        events_emitted=len(events),
        skipped=tuple(skipped),
    )
    return AdaptResult(events=events, report=report)


def _map_span(
    span: _Span,
    operation: str | None,
    *,
    subject: str,
    source_class: str,
    source: str | None,
) -> list[dict[str, Any]]:
    """Return the zero, one, or two events a single span maps to (SPEC 12.2)."""
    if operation is None:
        return []
    resolved_source = _source_for(span, source)

    def envelope(
        event_type: str, event_id: str, time: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _envelope(
            span,
            event_type=event_type,
            event_id=event_id,
            time=time,
            payload=body,
            subject=subject,
            source=resolved_source,
            source_class=source_class,
        )

    span_time = span.start or span.end
    if span_time is None:
        return []
    base_id = f"otel:{span.trace_id}/{span.span_id}"

    if operation in {"chat", "text_completion", "generate_content", "embeddings"}:
        model_op = "embeddings" if operation == "embeddings" else "chat"
        return [envelope("ModelCall", base_id, span_time, _model_call(span, model_op))]
    if operation == "execute_tool":
        return [envelope("ToolCall", base_id, span_time, _tool_call(span))]
    if operation in {"invoke_agent", "invoke_workflow"}:
        end_time = span.end or span_time
        return [
            envelope(
                "SessionStart",
                f"{base_id}#session-start",
                span_time,
                _session_start(span),
            ),
            envelope(
                "SessionEnd", f"{base_id}#session-end", end_time, _session_end(span)
            ),
        ]
    if operation in {"retrieve", "retrieval"}:
        return [envelope("ResourceAccess", base_id, span_time, _resource_access(span))]
    if operation == "memory.write":
        return [envelope("MemoryWrite", base_id, span_time, _memory_write(span))]
    if operation == "memory.read":
        return [envelope("MemoryRead", base_id, span_time, _memory_read(span))]
    return []
