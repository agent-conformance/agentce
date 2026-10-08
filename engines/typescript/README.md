# @agent-conformance/cli

TypeScript/Node implementation of the **Agent Conformance Engine (AgentCE)** — a deterministic engine that evaluates the evidence an AI-agent deployment produces against executable control catalogs and emits a conformance report.

The command-line tool implements `conformance run` (the conformance suite; with `--adapters <dir>` it also runs the adapters' own conformance through `uv`, as the Python engine does), `assess`, `validate`, `report` (including `report --validate`, schema validation of the report artifacts), `quickstart`, and `--version`.

## A folder of trace records

`agentce assess <folder>` reads a folder of OpenTelemetry GenAI or OpenInference trace exports
(`.json`, `.jsonl`, `.ndjson`) and writes the same bundle, derived `applicability.yaml`, results and
refusals as the Python engine. The verification gate `VG-RECORDS-FOLDER-PARITY` runs both engines over
the same folders and compares their output.

One known difference: on Linux, a file name that is not valid UTF-8 is shown with Python's surrogate
escape by the Python engine and with U+FFFD by this one. macOS does not allow such names.

## Test seams

The cross-engine checks call a few computation seams (`numerics`, `digest-tree`, `security-view`,
`auditor-view`, `fail-on-check`, `otel-genai-fixture`). They are not `agentce` commands, and
`agentce digest-tree` is refused as an unknown command. They run from their own entry point:
`node dist/seams.js <seam> ...`, or `pnpm seams <seam> ...` in a checkout.

- Repository: https://github.com/agent-conformance/agentce
- Website: https://agent-conformance.org

This is a pre-1.0 release: interfaces may change between minor versions. Conformance to a catalog is not a legal compliance determination.
