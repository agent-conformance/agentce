---
title: Trace-Store Connectors
description: Where Langfuse, Arize Phoenix, Datadog LLM Observability and LangSmith telemetry stands in the ingest support matrix, and how to fan OTel GenAI or OpenInference spans out to AgentCE before any of them ingest it.
---

Four of the seven trace stores in the ingest support matrix's [Trace backends](/reference/ingest-support-matrix/#trace-backends)
group have a fan-out path documented here: Langfuse, Arize Phoenix, Datadog LLM Observability and
LangSmith (the other three — Braintrust, Helicone and W&B Weave — are in the matrix but have no
fan-out recipe yet). Each grades on its own definition — the producer's export reference or API,
read exactly as published:

| Backend | Tier | Reader follows | Build item |
|---|---|---|---|
| Langfuse | Supported | [Langfuse export reference and code](https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage), v4.47.0 | 19.29 |
| Arize Phoenix | Supported | [Phoenix REST API (OpenAPI)](https://github.com/Arize-ai/phoenix/blob/arize-phoenix-v20.16.0/schemas/openapi.json), v20.16.0 | 19.29 |
| Datadog LLM Observability | Experimental | [Datadog API v2 (OpenAPI), LLM Observability spans](https://github.com/DataDog/datadog-api-client-python/blob/2.61.0/.generator/schemas/v2/openapi.yaml), client 2.61.0 | 19.34 |
| LangSmith | Supported | [LangSmith API (OpenAPI), v2 runs](https://api.smith.langchain.com/openapi.json), 0.1.0 | 19.34 |

Datadog grades Experimental, not Supported: its LLM Observability endpoints are marked preview, and span
kind, environment and service live only in prose and tags, not the schema. The other three are Supported,
but each still has a real risk the matrix names — Langfuse writes two export layouts until 16 November
2026 and casts nulls as empty strings; Phoenix's API is unversioned and its OTLP-shaped endpoint uses
snake_case keys, which the OTLP specification does not allow; LangSmith's v2 enums are upper case where
older paths and docs show lower case. Readers built to each one land in 19.29 (Langfuse, Phoenix) and
19.34 (Datadog, LangSmith); until then, AgentCE does not read any of the four vendors' own export APIs.

## What works today: fan OTel out before any vendor touches it

All four vendors also run an OTLP receiver you can point a [Collector](https://opentelemetry.io/docs/collector/)
at directly, ahead of the vendor's own ingestion. At that point the data is plain OTel GenAI semantic-
convention (`gen_ai.*`) or OpenInference-convention spans — the same two conventions
[`adapters/otel-genai`](/reference/adapters/otel-genai/) already maps (SPEC 12), convention-based rather
than vendor-based: it detects the convention per span, from the OTLP schema URL or the OpenInference
instrumentation scope, and does not know or need to know which product produced the export. This path
needs no vendor-specific code, now, because it never goes near the vendor's own schema — it is a second,
independent way to reach these four backends' telemetry, not a substitute for the matrix's own-schema
readers above.

`adapters/otel-genai/fixtures/{langfuse,phoenix,datadog,langsmith}/` are OTLP/JSON exports shaped as a
generic `gen_ai.*`- or OpenInference-convention Collector export would be — **not** a capture of any
vendor's own export API, which the matrix rows above cover instead. `expected.jsonl` is generated
directly from the adapter's real output, never hand-written, so the convention mapping is correct by
construction.

## Run the round trip yourself

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

A committed fixture exercises the convention *mapping*; a live deployment still needs telemetry to reach
AgentCE. [`adapters/otel-genai/otel-collector/config.yaml`](https://github.com/agent-conformance/agentce/blob/main/adapters/otel-genai/otel-collector/config.yaml)
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

- [Supported sources](/reference/ingest-support-matrix/) — every source AgentCE supports, and the
  definition each one follows.
- [Running Assessments](/docs/running-assessments/) — what happens to these events once they're in a
  bundle.
- [CI Integration](/docs/ci-integration/) — running the same ingest-and-validate round trip on every
  change.
