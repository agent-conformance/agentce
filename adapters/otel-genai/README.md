# otel-genai adapter

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3`
fixtures and support matrix) for OpenTelemetry GenAI and OpenInference trace exports.

The adapter is a pure function `bytes → (events, AdapterReport)` over an OTLP/JSON trace export (an
`ExportTraceServiceRequest` document, or the equivalent shape an OpenInference exporter writes). It
maps recognised spans to canonical AgentCE evidence events and reports the rest:

| Source span | AgentCE event(s) |
|---|---|
| `invoke_agent` / `invoke_workflow`; OpenInference `AGENT` | `SessionStart` (span start) + `SessionEnd` (span end) |
| `chat` / `text_completion` / `generate_content`; OpenInference `LLM` | `ModelCall` |
| `embeddings`; OpenInference `EMBEDDING` | `ModelCall` (operation `embeddings`) |
| `execute_tool`; OpenInference `TOOL` | `ToolCall` |
| `retrieve` / `retrieval`; OpenInference `RETRIEVER` | `ResourceAccess` |
| `memory.write` / `memory.read` | `MemoryWrite` / `MemoryRead` |

## Contract (SPEC §12.1)

- **Deterministic ids.** Event ids derive from the source trace and span ids
  (`otel:<traceId>/<spanId>`, with `#session-start` / `#session-end` for the two lifecycle events an
  agent span brackets), so re-adapting the same export yields the same ids.
- **Preserved timestamps.** Span `startTimeUnixNano` / `endTimeUnixNano` become RFC 3339 UTC with
  millisecond precision (SPEC §6.7). Sub-millisecond nanoseconds are truncated, never rounded, so no
  precision is invented.
- **Declared trust class (SPEC §6.4).** `agentcesourceclass` is the adapter's declared class, applied
  to every event and never inferred per event. OTel GenAI is `self_report` by default, or
  `enforcement_point` when the collector sits at a gateway (pass `source_class`).
- **Recorded convention.** `agentceconv` is detected per span: `otel-genai:<major.minor>` from the
  OTLP schema URL, or `openinference:<version>` from the instrumentation scope. The `gen_ai.*`
  mapping is version-aware — it accepts both the current `usage.input_tokens` / `output_tokens` and
  the legacy `usage.prompt_tokens` / `completion_tokens` spellings.
- **Never invents.** A span the adapter does not recognise is recorded in the `AdapterReport`
  (`missing_operation` / `unrecognised_operation`), never emitted.
- **References content, never captures it (SPEC R12).** Prompt, completion, tool argument, and tool
  result content is referenced by an opaque locator back to the source span
  (`otel:<traceId>/<spanId>#input`), not copied into the event.

The adapter runtime and `check_support_matrix` use only the standard library and PyYAML — no network,
no learned component. W3C Trace Context (`traceId` / `spanId` / `parentSpanId`) is carried on the
envelope for correlation.

## Layout

- `src/agentce_adapters/otel_genai.py` — the adapter (`adapt`).
- `src/agentce_adapters/check_support_matrix.py` — the `SUPPORT MATRIX OK` consistency check.
- `support-matrix.yaml` — which members the adapter can populate, per event type (SPEC §12.3).
- `fixtures/<case>/` — a native export (`input.json`), the deployment context (`adapt.json`), and the
  expected canonical events (`expected.jsonl`).

## Commands

```bash
uv run pytest -q
uv run python -m agentce_adapters.check_support_matrix .
```

`pytest` proves each fixture maps to its expected events byte-for-byte under the shared canonical
form, that every event is schema-valid, and that the contract properties above hold.
`check_support_matrix` proves `support-matrix.yaml` stays exactly consistent with what the adapter
emits over the fixtures.
