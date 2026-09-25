---
title: Trace-Store Connectors
description: Langfuse, Phoenix/Arize, Datadog LLM Observability, and LangSmith telemetry round-trips into AgentCE through the existing otel-genai adapter — no vendor-specific code.
---

If your agent's telemetry already lives in a trace store — Langfuse, Phoenix/Arize, Datadog LLM
Observability, or LangSmith — you do not need a new adapter to get it into AgentCE. All four already
export OpenTelemetry GenAI semantic-convention (`gen_ai.*`) or OpenInference-convention spans, and
[`adapters/otel-genai`](/reference/adapters/otel-genai/) is convention-based, not vendor-based (SPEC 12): it detects
the convention per span, from the OTLP schema URL or the OpenInference instrumentation scope, and maps
it to canonical AgentCE evidence events. It does not know the name of the product that produced the
export.

## What each vendor actually emits

- **Langfuse** runs its own OTel backend at `https://cloud.langfuse.com/api/public/otel` and documents
  that it renders `gen_ai.*`-convention spans as "generations" — the same convention family the base
  `otel-genai-chat` fixture exercises, not OpenInference.
- **Phoenix/Arize** is built directly on OpenInference: `openinference.span.kind`, `llm.*`, and
  `input.value`/`output.value` are its own attribute family, the one `openinference-rag` already
  demonstrates.
- **Datadog LLM Observability** supports the OTel GenAI semantic convention natively: its Agent runs an
  OTLP receiver (ports 4317/4318) and forwards through its own `datadog` exporter.
- **LangSmith** exposes an OTLP endpoint at `https://api.smith.langchain.com/otel`, documented as
  consuming the OpenLLMetry/`gen_ai.*` convention.

Each claim is proved, not just documented: `adapters/otel-genai/fixtures/{langfuse,phoenix,datadog,
langsmith}/` are real, committed OTLP/JSON exports shaped the way each vendor's own backend would
render them, with `expected.jsonl` generated directly from the adapter's real output — never
hand-written — so the mapping is correct by construction.

## Prove the round trip yourself

Each fixture ingests into a bundle `agentce validate` accepts with zero quarantines, exactly like any
other otel-genai export. This resolves the repository root portably (the same trick works whether
you're in a fresh checkout or an isolated sandbox) and ingests the committed Langfuse fixture:

```bash
REPO=$(python3 -c "import os; print(os.path.dirname(os.path.realpath('engines')))")
cd "$REPO"
OUT=$(mktemp -d)
uv run --project engines/python agentce ingest --adapter otel-genai --in adapters/otel-genai/fixtures/langfuse/input.json --out "$OUT"
uv run --project engines/python agentce validate --bundle "$OUT"
```

The same shape works for Phoenix's OpenInference-convention export — no different adapter, no different
flags, just a different fixture:

```bash
REPO=$(python3 -c "import os; print(os.path.dirname(os.path.realpath('engines')))")
cd "$REPO"
OUT=$(mktemp -d)
uv run --project engines/python agentce ingest --adapter otel-genai --in adapters/otel-genai/fixtures/phoenix/input.json --out "$OUT"
uv run --project engines/python agentce validate --bundle "$OUT"
```

`tools/trace_store_ingest_check.py` runs that same round trip for all four vendors in one pass, and
also re-runs the adapter's own `check_support_matrix` to confirm `support-matrix.yaml` still matches
what every fixture — old and new — actually exercises:

```bash
REPO=$(python3 -c "import os; print(os.path.dirname(os.path.realpath('engines')))")
cd "$REPO"
uv run --project engines/python python tools/trace_store_ingest_check.py
```

## Wire the fan-out for real

A committed fixture proves the *mapping*; a live deployment still needs telemetry to reach AgentCE.
[`adapters/otel-genai/otel-collector/config.yaml`](https://github.com/agent-conformance/agentce/blob/main/adapters/otel-genai/otel-collector/config.yaml)
is the base Collector recipe (ADR 0017): a stock `otelcol-contrib` receiving OTLP and writing a local
`file` AgentCE reads. `adapters/otel-genai/otel-collector/recipes/{langfuse,phoenix,datadog,
langsmith}.yaml` extend it: the same shared `otlp` receiver, fanned out to both the vendor's own real
exporter (so the trace store keeps working exactly as before) and the same `file` exporter, so
`agentce ingest` or a scheduled `agentce collect` source picks it up too. Each recipe's header comment
names the documentation it came from and the date it was checked.

`tools/trace_store_ingest_check.py --recipes-only` structurally validates all four recipes: that they
parse as YAML, declare a receiver/processor/exporter/pipeline shape, that their `otlp` receiver is
identical to the shared one, that their vendor exporter's endpoint is the documented one, that a `file`
exporter sits in the same pipeline, and that the header cites a source and a date:

```bash
REPO=$(python3 -c "import os; print(os.path.dirname(os.path.realpath('engines')))")
cd "$REPO"
uv run --project engines/python python tools/trace_store_ingest_check.py --recipes-only
```

Run the recipe itself with `otelcol-contrib --config adapters/otel-genai/otel-collector/recipes/
langfuse.yaml` (substituting the vendor of your choice), with that vendor's own credentials in the
environment (`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`, `PHOENIX_COLLECTOR_ENDPOINT`,
`LANGSMITH_API_KEY`/`LANGSMITH_PROJECT` — Datadog's recipe instead points at a local Datadog Agent
already running with OTLP ingestion enabled).

## What this is not

Nothing here calls a vendor's live endpoint: every fixture and every check above is a committed,
offline, deterministic file. The Collector recipes are configuration for a binary this repository does
not build, run, or vendor (ADR 0017) — they document the real wiring for a live deployment, but no
check in this repository, or in CI, ever makes a network call to Langfuse, Phoenix, Datadog, or
LangSmith.

## Read next

- [Running Assessments](/docs/running-assessments/) — what happens to these events once they're in a
  bundle.
- [CI Integration](/docs/ci-integration/) — running the same ingest-and-validate round trip on every
  change.
