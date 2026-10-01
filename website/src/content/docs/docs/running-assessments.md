---
title: Running Assessments
description: The command-line workflow — declare a profile, collect evidence, assess, and read the report.
---

The engine ships a single command-line tool, `agentce`. Every command supports `--json` for scripting
and returns a documented exit code. The commands below use the Python engine. The TypeScript and Java
engines implement the same commands (`assess`, `validate`, `report`, `report --validate`, and
`quickstart`) on the same evaluation path, and their `conformance run` command produces byte-identical
`assertions.json` files over the simulated corpus. One difference remains. On a damaged report file, such
as one with bytes that are not valid UTF-8 or a CSV, XML or JSONL file in an unusual shape,
`report --validate` in TypeScript or Java can disagree with Python. Check such a file with the
Python engine.

## Declare an applicability profile

Start from a generated profile — what you declare about the system under assessment:

```bash
uv run --project engines/python agentce init --out ./my-assessment
```

With no other flag the profile is for a `custom-loop` agent with the `deployer` role; name your own with
`--framework`, `--subject` and `--role`.

Fill in the `TODO`s in the generated `agentce/applicability.yaml`, then confirm it validates.

## Provide evidence

The engine reads evidence emitted by the agent at its enforcement points, or exported from a source
adapter. Enforcement-point evidence — recorded where a decision is actually enforced — is preferred,
because it shows what happened rather than what was intended. See
[CI Integration](/docs/ci-integration/) for wiring the emitter into an agent.

## Assess

Run an assessment over a [bundle](/docs/concepts/) of evidence, the profile you declared, and its domain
binding. This form runs as written from a checkout of the repository, against the vendored quickstart
project, so you can see a full assessment before you have evidence of your own:

```bash
uv run --project engines/python agentce assess --bundle corpus/quickstart/evidence --profile corpus/quickstart/applicability.yaml --domain corpus/quickstart/domain.linkml.yaml --out ./out
```

For your own agent, pass your bundle and the profile and domain binding `agentce init` wrote:
`--bundle ./my-assessment/evidence --profile ./my-assessment/agentce/applicability.yaml --domain ./my-assessment/agentce/domain.linkml.yaml`.

The profile's `catalogs:` list names what to assess against, and each `id@version` resolves to a catalog
that ships inside the engine package, so an installed engine finds the base and sector-overlay catalogs
without a checkout. Pass `--catalog <id@version>` to choose others, or `--catalog-dir <dir>` for a catalog
on disk. A catalog that resolves to nothing stops the run with exit
code `3` and the message key `input.catalog_unresolved` instead of writing an empty report.

The run writes the assertions, the human report, and the OSCAL and SARIF renderings, alongside the
coverage, integrity, and quarantine detail. Validate the artifacts against their schemas with
`agentce report --validate ./out`.

## Assess a folder of traces

If your agent already writes OTLP/JSON traces (OpenTelemetry GenAI spans, including OpenInference spans), you do not need a bundle or a
profile to see a first report. Point `assess` at the folder that holds the exports (`.json` files with one
trace document each, or `.jsonl` files with one document per line):

```bash no-run needs the reader's own folder of trace exports
uv run --project engines/python agentce assess ./traces --out ./out
```

The engine reads every trace export it recognises (vendor-native export formats are not read yet), writes an evidence bundle and a default profile under
`./out` (`./out/applicability.yaml`), and assesses against the baseline lens. It lists each file it could
not read with the reason, and it stops with exit code `3` and the message key `input.records_none_recognised`
when the folder holds nothing it can read.

Traces record model calls and tool calls, not decisions, and nothing yet says which tool calls are
consequential. So a records run never calls a check "not applicable" on the strength of records that could not
show it: every check the records give no population for reports *insufficient evidence*, and the manifest
records that the run was assessed from trace records only. A profile you pass with `--profile` is used as you
wrote it and is never overwritten; `./out/applicability.yaml` is the starting point to copy and edit.

## Inspect and verify

- `agentce config show --json` prints every option, its resolved value, and where that value came from.
- `agentce doctor` reports configuration problems with the exact fix for each.
- `agentce verify --catalog` and `agentce verify --release` check a signed catalog or release offline.

## Read next

- [CI Integration](/docs/ci-integration/) — run all of this automatically, with networking disabled.
- [The Conformance Spec](/docs/specification/) — the schemas the artifacts validate against.
