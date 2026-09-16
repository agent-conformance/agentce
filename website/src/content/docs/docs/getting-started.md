---
title: Getting Started
description: Install an Agent Conformance engine and run your first assessment over the bundled example.
---

Agent Conformance assesses how an AI-agent deployment behaves against a shared, executable
specification. The engine is deterministic, read-only, and model-free: it collects evidence about an
agent, evaluates that evidence against a control catalog, and writes a report you can reproduce and
verify. No learned component decides an outcome.

:::note
This is pre-general-availability documentation. The commands below are stable; some links point to the
specification and reference material that ship alongside the engine.
:::

## Run the bundled example

The fastest way to see a full assessment is the quickstart, which runs the vendored
`corpus/quickstart` project — a small credit-decisioning agent with complete enforcement-point
evidence — against the base EU AI Act catalog and writes a report:

```bash
uv run --project engines/python agentce quickstart --out ./out
```

Every base-catalog control is conformant on this project, each citing the evidence that supports it.
The run writes, into `./out`:

- `assertions.json` — one machine-readable verdict per control and subject; the source of truth.
- `report.md` and `report.html` — the human-readable report.
- `results.sarif` and `oscal-ar.json` — SARIF and OSCAL Assessment Results renderings.
- `coverage.json`, `integrity.jsonl`, `quarantine.jsonl` — coverage, integrity, and quarantine detail.

## Next steps

- [Concepts](/docs/concepts/) — the mental model: subjects, evidence, controls, and verdicts.
- [Running Assessments](/docs/running-assessments/) — the full command-line workflow for your own agent.
- [CI Integration](/docs/ci-integration/) — assess an agent on every change, offline.
