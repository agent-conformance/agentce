---
title: Authoring a Catalog
description: Add a control catalog for your own standard or internal rule set without forking the engine — author, lint, sign, and load it.
---

A control catalog is a directory of data: `catalog.yaml`, one YAML file per control under `controls/`,
and the [Portable Shape Profile](/docs/specification/) shapes the structural controls reference.
Adding a catalog for a standard the engine does not ship — an internal policy, a sector rule set, a
different standards body's framework — never requires a change to engine code. This page walks the
author → lint → sign → load path using the engine's own second base catalog, `nist-ai-rmf`, as the
worked example: it maps the same six controls the `eu-ai-act` base catalog already evaluates to the
NIST AI Risk Management Framework's subcategories, reusing every shape verbatim, because the detection
logic — did a consequential decision carry an actor and a timestamp, was it reviewed, did untrusted
content reach it — is framework-neutral. Only the crosswalk target changes.

## The catalog layout

```text
spec/catalogs/base/nist-ai-rmf/
  catalog.yaml            # id, version, provenance digest, the control list
  controls/REC-04.yaml     # one control: crosswalk, evaluation mode, expectations, test cases
  shapes/REC-04.ttl         # the Portable Shape Profile shape a structural control references
  test/REC-04/*.jsonl       # passed, failed, and inapplicable fixtures for that control
```

A control's `crosswalk` entry names the framework and clause it maps to and carries
`verified_against_text: false` until a human with the licensed standard text confirms the mapping — the
engine never sets that flag itself.

## Lint the catalog

The real command line validates every control against its schema, parses its shape, and re-runs each
test-case fixture through the structural evaluator to confirm it produces the outcome it claims:

```bash
uv run --project engines/python agentce catalog lint spec/catalogs/base/nist-ai-rmf
```

`--require-provenance` additionally recomputes the catalog's content digest, so a rebranded or
tampered catalog either shows its real origin or fails to validate:

```bash
uv run --project engines/python agentce catalog lint --require-provenance spec/catalogs/base/nist-ai-rmf
```

A rule that reaches structural evaluation (rung 2) must reference a shape built only from the small,
declarative Portable Shape Profile vocabulary — never a SPARQL- or script-based constraint, which would
let one catalog carry logic the other two engines cannot run identically.

## Sign and verify

A catalog an assessment loads by id (`--catalog <id>@<version>`) must carry a detached signature the
effective trust root verifies before a single control runs (SPEC §8.7); a catalog loaded by directory
(`--catalog-dir`) may instead be assessed with `--allow-unverified-catalog`, which records the override
as a limitation rather than refusing the run. Verification runs fully offline, against the vendored trust
root by default:

```bash
uv run --project engines/python agentce verify --catalog spec/catalogs/base/nist-ai-rmf
```

## Load it in an assessment

Once a catalog id resolves and its signature verifies, evaluating it is the same `assess` a reader has
already seen for `eu-ai-act` — only the `--catalog` value changes:

```bash
uv run --project engines/python agentce assess --catalog nist-ai-rmf@2026.09 --bundle corpus/quickstart/evidence --profile corpus/quickstart/applicability.yaml --domain corpus/quickstart/domain.linkml.yaml --out ./out
```

`nist-ai-rmf@2026.09` currently substantiates six controls — the subcategories the existing
`eu-ai-act` crosswalk (`spec/catalogs/base/eu-ai-act/crosswalk/nist-ai-rmf.yaml`) already maps with
runtime evidence: accountability (GOVERN 4.2), safety and deactivation mechanisms (MEASURE 2.6, MANAGE
2.3), security and resilience (MEASURE 2.7), transparency (MEASURE 2.8), performance monitoring (MEASURE
2.3), and tracked risk communication (MEASURE 3.1, MANAGE 4.3). Broader AI RMF coverage, and the
organisational GOVERN and MAP obligations the crosswalk marks process-only, are roadmap, not shipped —
the same honesty the catalog's own crosswalk file states.

## Building your own catalog

The path above is exactly what a bring-your-own catalog follows: write `catalog.yaml` and one file per
control under `controls/`, add a shape under `shapes/` for anything evaluated structurally, add
`passed`/`failed`/`inapplicable` fixtures under `test/<control-id>/`, then run `agentce catalog lint`
against the directory. Point an assessment at it with `--catalog-dir <path>`, and either sign it against
your own key and trust root (below) or pass `--allow-unverified-catalog` while you are iterating. No
engine change, in any of the three engines, is needed at any step.

Beyond `catalog lint`'s own schema, evidence, and test-case checks, `tools/catalog_authoring_check.py`
(run in CI on every catalog change) rejects a Portable Shape Profile shape that uses a SPARQL- or
script-based constraint (`sh:sparql`, `sh:js`) — logic `catalog lint` itself does not catch, but which
would let one catalog carry evaluation logic the other two engines cannot run identically.

## Guided path: init and sign

A scaffold gets you to a lint-clean, signed catalog in four commands, from an empty directory:

```bash
uv run --project engines/python agentce catalog init my-catalog
uv run --project engines/python agentce catalog lint my-catalog --support-matrix support-matrix.json
uv run --project engines/python agentce catalog sign my-catalog --new-key my-key.pem --write-trust-root my-trust-root.json
```

`catalog init` scaffolds a `mode: automated` control that already lints clean. `catalog lint
--support-matrix` writes a preview of what each control needs and who can supply it today — rung, owner,
and the adapters that already produce the event, if any — before you sign anything. `catalog sign
--new-key` generates a fresh Ed25519 key and writes a trust root naming it, so the assessment below
verifies against your own root rather than the vendored one:

```bash no-run needs the reader's own evidence bundle, applicability profile, and domain binding
uv run --project engines/python agentce assess --bundle <your evidence bundle> --profile <your applicability profile> --domain <your domain binding> --catalog-dir my-catalog --trust-root my-trust-root.json --out ./out
```

The `--bundle`/`--profile`/`--domain` there are your own evidence bundle, applicability profile, and
domain binding — if you don't have one yet, [Getting Started](/docs/getting-started/) walks through
`agentce quickstart --out ./out`, which assesses the small sample project bundled with the package end
to end, so you can see a full report before pointing this sequence at your own evidence.
