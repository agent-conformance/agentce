---
title: CI Integration
description: Assess an agent on every change — switch the emitter on in one line, run offline, and gate on exit codes.
---

Agent Conformance is designed to run in continuous integration: it is scriptable, runs with networking
disabled, and communicates results through exit codes and standard file formats. You can assess an
agent on every change before it reaches production.

## Switch the emitter on in one line

Create the emitter with a single call. It is a no-op until you switch it on with `AGENTCE_EMIT=1`, so
it never changes what the agent does until you want evidence. That call alone hooks nothing: emit at
each chokepoint yourself, or, for a framework that already produces OTel-shaped spans, register
`agentce_emit.instrument()`'s `AgentCESpanProcessor` with its tracer instead. Fully automatic capture,
with no per-framework wiring at all, is on the roadmap and is not built:

```python
import agentce_emit

emitter = agentce_emit.auto()   # active when AGENTCE_EMIT=1; the bundle is flushed at process exit
```

Then emit at each chokepoint — one call per model call, tool call, and decision. Content is referenced
by hash, never captured, so no prompts or outputs are stored in the evidence.

## Run offline in the pipeline

An assessment needs no network. A typical job installs the pinned engine, runs the assessment over the
evidence the agent produced, and validates the artifacts. The paths below name the vendored quickstart
project so the job runs as written from a checkout; substitute your own bundle, profile, and domain
binding:

```bash
uv run --project engines/python agentce assess --bundle corpus/quickstart/evidence --profile corpus/quickstart/applicability.yaml --domain corpus/quickstart/domain.linkml.yaml --out ./out
uv run --project engines/python agentce report --validate ./out
```

## Gate on the result

Commands return documented exit codes, so a pipeline can fail a change on a non-conformant result or an
untrusted signature. For verification, exit code `0` means verified and a non-zero code means unsigned,
tampered, or signed by an untrusted key. The `results.sarif` output uploads directly to code-scanning
surfaces, and `oscal-ar.json` feeds governance tooling.

## Read next

- [Running Assessments](/docs/running-assessments/) — the commands this pipeline runs.
- [Contributing](/docs/contributing/) — the adapters and catalogs that extend coverage.
