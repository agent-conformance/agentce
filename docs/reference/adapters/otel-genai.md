# `otel-genai` adapter

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3` fixtures and support matrix) for OpenTelemetry GenAI and OpenInference trace exports.

The adapter is a pure function `bytes → (events, AdapterReport)` over an OTLP/JSON trace export (an `ExportTraceServiceRequest` document, or the equivalent shape an OpenInference exporter writes). It maps recognised spans to canonical AgentCE evidence events and reports the rest:

See the [adapter README](../../../adapters/otel-genai/README.md) for the full source-to-event mapping, fixtures, and the support matrix.
