# 0017 — An OpenTelemetry Collector recipe, not a custom plugin

Status: accepted
Spec refs: §5.4, §12.1

## Context

A team whose agent traces already flow through a self-hosted OpenTelemetry Collector has no path from
that pipeline into AgentCE: nothing in this repository names an OTel Collector component, and
`agentce collect`'s example config modelled an OTLP source as a URL a job polls on a schedule, which
OTLP's own push model cannot satisfy (SPEC §5.4 already describes the real mechanism: "The OTel
Collector exports traces to files or object storage ... AgentCE reads these exports").

The upstream Collector's plugin architecture builds a custom processor or exporter as a Go artifact,
compiled with the OpenTelemetry Collector Builder (`ocb`). `AGENTS.md`'s Toolchains section lists only
Python/`uv`, Node/`pnpm`, and Java/Gradle — no Go and no Collector-builder toolchain — so shipping a
bespoke component would first need an ADR, a new toolchain entry, new commands, and new CI jobs (the
constraint this finding names directly).

## Decision

Ship a **recipe**, not a plugin: `adapters/otel-genai/otel-collector/config.yaml` configures a stock
`otelcol-contrib` distribution — an `otlp` receiver, a `batch` processor, and a `file` exporter, all
components the upstream project already ships — so a deployment's own Collector writes GenAI spans to
a local file in the same OTLP/JSON shape `agentce_adapters.otel_genai.adapt` already consumes (proved
by `adapters/otel-genai/fixtures/otel-genai-chat/input.json`). No plugin is compiled and no new
toolchain is introduced.

That exported file is then read either directly, with `agentce ingest --adapter otel-genai --in
<export> --out <bundle>`, or on a schedule, by naming it as an `export` on a `agentce collect` config
source (`agentce.collect.SourceSpec.export`); `adapters/fixtures/collect-dry-run.yaml`'s three sources
each now do the latter, against this repository's own committed adapter fixtures.

A custom Collector processor or exporter remains open for a deployment whose evidence shape the stock
`file`/`otlphttp`/object-storage exporters cannot reach; that is deferred until real demand justifies
the Go/`ocb` toolchain this ADR avoids adding today.

## Alternatives considered (with why not)

- **A custom Go processor/exporter component**, built with `ocb`. Rejected for now: it needs a
  toolchain `AGENTS.md` does not list (Go, `ocb`), and the stock `file` exporter already reaches the
  file/object-storage mechanism SPEC §5.4 describes — the custom component would duplicate it.
- **Modelling the OTLP source as a polled HTTP endpoint** (the prior example config). Rejected: OTLP
  export is push-based; there is nothing to poll at a `/v1/traces`-style endpoint on a schedule.
- **Requiring a live, deployed Collector for every conformance check.** Rejected for this phase: the
  live, network-sourced round trip through a deployed `otelcol-contrib` is an integration concern this
  offline environment cannot stand up; the recipe's correctness rests on its components being stock,
  documented behaviour of the upstream project, and on the fixture proving the *reading* half already
  works end to end.

## Consequences (including determinism, portability to TS/Java, performance)

- No new dependency enters the engine, adapter, or tools dependency trees: the recipe is a
  configuration file for a binary this repository does not build or vendor.
- `ingest`/`collect`'s file-based path is scoped to the Python reference engine (matching the pattern
  established for other CLI-mechanism findings at this phase); porting it to TypeScript/Java is future
  work, not required by this decision.
- Determinism and offline operation are unaffected: reading a committed export file is offline and
  order-independent; nothing here runs the Collector binary itself in CI.
- Building the deferred custom component later requires its own ADR (recording the Go/`ocb` toolchain,
  its commands, and its CI jobs) before it ships, per the constraint this ADR itself satisfies.

## Verification (the test or check that proves the decision holds)

`agentce ingest --adapter otel-genai --in adapters/otel-genai/fixtures/otel-genai-chat/input.json --out
<dir>` followed by `agentce validate --bundle <dir>` exits 0 with zero quarantines, proving the export
shape this recipe's `file` exporter produces round-trips through the engine. `agentce collect --config
adapters/fixtures/collect-dry-run.yaml --out <dir>` records every source `complete` by reading each
one's committed `export`, proving the collect-side reframing this recipe motivates. The
`collect-ingest` CI job (`.github/workflows/ci.yml`) re-runs both on every push and pull request.
