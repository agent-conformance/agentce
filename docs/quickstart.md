# Quickstart

Assess a complete example and see a full conformance report in one command, before instrumenting
anything of your own (SPEC §13.4). Everything here runs offline against artifacts in this repository.

## Run the quickstart

```bash
uv run --project engines/python agentce quickstart --out ./out
```

This assesses the bundled [`corpus/quickstart`](../corpus/quickstart) project — a small
credit-decisioning agent with full enforcement-point evidence — against the base EU AI Act catalog and
writes a report to `./out`:

- `assertions.json` — one machine-readable verdict per (control, subject); the source of truth.
- `report.md` / `report.html` — the human report.
- `oscal-ar.json`, `results.sarif` — the OSCAL Assessment Results and SARIF renderings.
- `coverage.json`, `integrity.jsonl`, `quarantine.jsonl` — the coverage, integrity, and quarantine detail.

Every base-catalog control (REC, OVS, INT, INC) is conformant, each citing the evidence that supports
it. Validate the report artifacts against their schemas with:

```bash
uv run --project engines/python agentce report --validate ./out
```

## Start your own profile

`agentce init` writes a starter applicability profile — what you declare about the system under
assessment (SPEC §6.5):

```bash
uv run --project engines/python agentce init \
  --non-interactive --framework custom-loop --subject spiffe://corp/agents/my-agent --role deployer \
  --out ./my-assessment
```

Fill in the `TODO`s in `./my-assessment/agentce/applicability.yaml`, then check it validates:

```bash
uv run --project engines/python python -m agentce.tools.validate_profile ./my-assessment/agentce/applicability.yaml
```

## Inspect the configuration

`agentce config show` prints each engine option, its resolved value, and where that value came from —
a built-in default, an `agentce.toml` file, an `AGENTCE_<KEY>` environment variable, or a flag:

```bash
uv run --project engines/python agentce config show --json
```
