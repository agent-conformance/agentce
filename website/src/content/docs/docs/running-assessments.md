---
title: Running Assessments
description: The command-line workflow — declare a profile, collect evidence, assess, and read the report.
---

The engine ships a single command-line tool, `agentce`. Every command supports `--json` for scripting
and returns a documented exit code. The commands below use the Python engine; the TypeScript and Java
engines expose the same interface and produce byte-identical output.

## Declare an applicability profile

Start from a generated profile — what you declare about the system under assessment:

```bash
uv run --project engines/python agentce init \
  --non-interactive --framework custom-loop \
  --subject spiffe://corp/agents/my-agent --role deployer \
  --out ./my-assessment
```

Fill in the `TODO`s in the generated `applicability-profile.yaml`, then confirm it validates.

## Provide evidence

The engine reads evidence emitted by the agent at its enforcement points, or exported from a source
adapter. Enforcement-point evidence — recorded where a decision is actually enforced — is preferred,
because it shows what happened rather than what was intended. See
[CI Integration](/docs/ci-integration/) for wiring the emitter into an agent.

## Assess

Run an assessment over a bundle of evidence and a catalog:

```bash
uv run --project engines/python agentce assess --bundle ./bundle --out ./out
```

The run writes the assertions, the human report, and the OSCAL and SARIF renderings, alongside the
coverage, integrity, and quarantine detail. Validate the artifacts against their schemas with
`agentce report --validate ./out`.

## Inspect and verify

- `agentce config show --json` prints every option, its resolved value, and where that value came from.
- `agentce doctor` reports configuration problems with the exact fix for each.
- `agentce verify --catalog` and `agentce verify --release` check a signed catalog or release offline.

## Read next

- [CI Integration](/docs/ci-integration/) — run all of this automatically, with networking disabled.
- [The Conformance Spec](/docs/specification/) — the schemas the artifacts validate against.
