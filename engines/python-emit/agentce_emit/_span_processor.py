"""``AgentCESpanProcessor``: hook a framework's own OTel-shaped instrumentation (SPEC 13.4 AX-3).

Many agent frameworks already produce spans -- through the OpenTelemetry SDK directly, or through
their own callback/hook system, which a caller can translate into the same shape -- that follow, or
can be mapped to, the OTel GenAI or OpenInference semantic conventions. :class:`AgentCESpanProcessor`
is shaped like the OTel SDK's ``SpanProcessor`` (``on_start``/``on_end``/``shutdown``/``force_flush``)
so it can be registered directly with a real ``TracerProvider``
(``tracer_provider.add_span_processor(AgentCESpanProcessor(emitter))``); it never imports the
``opentelemetry`` SDK itself; any span-like object works.

On drain, each span is re-encoded as a one-span OTLP/JSON trace export and handed to the existing
``agentce-adapter-otel-genai`` mapping (the same code path a collector-fed adapter run uses), so there
is exactly one place that decides what a ``gen_ai.*``-tagged span means. That package is not a hard
dependency of ``agentce-emit`` (the core emitter stays standard-library-only); install the ``otel``
extra (``agentce-emit[otel]``) to use this module.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ._emit import Emitter

_trace_id_counter = itertools.count(1)
_span_id_counter = itertools.count(1)


@dataclass(frozen=True)
class Span:
    """A minimal, OTel ``ReadableSpan``-shaped span: build one from any framework's own callback
    payload without installing the OpenTelemetry SDK, and pass it to
    :meth:`AgentCESpanProcessor.on_end`. A real OTel SDK ``ReadableSpan`` (``.name``, ``.attributes``,
    ``.context.trace_id``/``.context.span_id``, ``.start_time``/``.end_time`` in unix nanoseconds) is
    also accepted directly -- ``on_end`` reads either shape.
    """

    name: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    start_time: int | None = None
    end_time: int | None = None
    trace_id: int | None = None
    span_id: int | None = None
    parent_span_id: int | None = None
    ok: bool = True


def _hex(value: int | None, width: int) -> str:
    if value is None:
        return "0" * width
    return format(value & (2 ** (width * 4) - 1), f"0{width}x")


def _otel_context_ids(span: Any) -> tuple[int | None, int | None, int | None]:
    """Read trace/span/parent-span ids from either a real OTel ``ReadableSpan`` or our own
    :class:`Span`."""
    context = getattr(span, "context", None)
    if context is not None:  # a real opentelemetry.sdk.trace.ReadableSpan
        parent = getattr(span, "parent", None)
        return (
            getattr(context, "trace_id", None),
            getattr(context, "span_id", None),
            getattr(parent, "span_id", None) if parent is not None else None,
        )
    return (
        getattr(span, "trace_id", None),
        getattr(span, "span_id", None),
        getattr(span, "parent_span_id", None),
    )


def _any_value(value: Any) -> dict[str, Any]:
    """Encode one Python scalar/collection as an OTLP ``AnyValue`` (the inverse of the adapter's
    ``_any_value`` decoder in ``agentce_adapters.otel_genai``)."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_any_value(item) for item in value]}}
    if isinstance(value, Mapping):
        return {
            "kvlistValue": {
                "values": [{"key": str(k), "value": _any_value(v)} for k, v in value.items()]
            }
        }
    return {"stringValue": "" if value is None else str(value)}


def _span_status_ok(span: Any) -> bool:
    status = getattr(span, "status", None)
    if status is None:
        return bool(getattr(span, "ok", True))
    code = getattr(status, "status_code", status)
    name = getattr(code, "name", code)
    return str(name).upper() != "ERROR"


def _span_to_document(span: Any, *, schema_url: str) -> dict[str, Any]:
    """Wrap one span-like object as a one-span OTLP/JSON ``ExportTraceServiceRequest`` document, the
    input shape ``agentce_adapters.otel_genai.adapt`` already parses."""
    trace_id, span_id, parent_span_id = _otel_context_ids(span)
    attributes = dict(getattr(span, "attributes", None) or {})
    span_json: dict[str, Any] = {
        "traceId": _hex(trace_id, 32) if trace_id is not None else _hex(next(_trace_id_counter), 32),
        "spanId": _hex(span_id, 16) if span_id is not None else _hex(next(_span_id_counter), 16),
        "name": str(getattr(span, "name", "") or ""),
        "startTimeUnixNano": str(getattr(span, "start_time", None) or 0),
        "endTimeUnixNano": str(getattr(span, "end_time", None) or getattr(span, "start_time", None) or 0),
        "attributes": [{"key": k, "value": _any_value(v)} for k, v in attributes.items()],
        "status": {"code": 1 if _span_status_ok(span) else 2},  # OTLP StatusCode OK=1, ERROR=2
    }
    if parent_span_id is not None:
        span_json["parentSpanId"] = _hex(parent_span_id, 16)
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "schemaUrl": schema_url,
                "scopeSpans": [
                    {"scope": {"name": "agentce-emit"}, "schemaUrl": schema_url, "spans": [span_json]}
                ],
            }
        ]
    }


class AgentCESpanProcessor:
    """An OTel SDK ``SpanProcessor``-shaped bridge from a framework's own spans to AgentCE evidence.

    ``on_end`` buffers each span; ``force_flush``/``shutdown`` drain the buffer through the
    otel-genai mapping and :meth:`~agentce_emit.Emitter.ingest_event` each resulting event onto the
    wrapped :class:`~agentce_emit.Emitter`. A span the mapping does not recognise (no ``gen_ai.*`` /
    OpenInference operation signal) is silently skipped, exactly as the batch adapter path skips it
    (SPEC 12.1: never invent an event).
    """

    #: The schema URL the adapter recognises as "otel-genai" without a specific version pin (SPEC 12.2).
    SCHEMA_URL = "https://opentelemetry.io/schemas/1.29.0"

    def __init__(self, emitter: Emitter) -> None:
        self._emitter = emitter
        self._buffer: list[Any] = []

    def on_start(self, span: Any, parent_context: Any = None) -> None:  # noqa: D401 - OTel shape
        return None

    def on_end(self, span: Any) -> None:
        if not self._emitter.active:
            return
        self._buffer.append(span)

    def shutdown(self) -> None:
        self.force_flush()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        self._drain()
        return True

    def _drain(self) -> None:
        if not self._buffer:
            return
        spans, self._buffer = self._buffer, []
        try:
            from agentce_adapters.otel_genai import adapt
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised by the missing-extra test
            raise ModuleNotFoundError(
                "AgentCESpanProcessor needs the 'agentce-adapter-otel-genai' package; "
                "from a source checkout, install the 'otel' extra with "
                "`uv sync --project engines/python-emit --extra otel`"
            ) from exc
        for span in spans:
            document = _span_to_document(span, schema_url=self.SCHEMA_URL)
            result = adapt(
                json.dumps(document).encode("utf-8"),
                subject=self._emitter.subject,
                source_class=self._emitter.source_class,
                source=self._emitter.source,
            )
            for event in result.events:
                self._emitter.ingest_event(event)


def instrument(emitter: Emitter | None = None, **overrides: Any) -> AgentCESpanProcessor:
    """Return an :class:`AgentCESpanProcessor` attached to ``emitter`` (or a fresh :func:`auto`
    emitter) -- the entrypoint SPEC 13.4 AX-3 names for hooking a framework's own instrumentation.

    Register the result with a real ``TracerProvider``
    (``tracer_provider.add_span_processor(instrument(em))``) when the framework already runs on the
    OpenTelemetry SDK, or call ``.on_end(span)`` directly from the framework's own callback/hook
    system after building a :class:`Span` from its payload -- both are "hooking the framework's own
    instrumentation" in the sense SPEC 13.4 AX-3 means; which one applies depends on what the
    framework offers.
    """
    from ._emit import auto

    em = emitter if emitter is not None else auto(**overrides)
    return AgentCESpanProcessor(em)
