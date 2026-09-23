# Report and claim schemas

Implements the specification: §9.1 (conformance claim), §9.4 (machine-readable outputs), §8.4
(reproducibility manifest), §6.5 (secondary inputs), §2.1 and §7.3 (OSCAL standards alignment), and
the enumerations of Appendix F.

## Contents

| File | Implements | Purpose |
|---|---|---|
| `claim.schema.json` | §9.1 | The conformance claim object: what was assessed, by whom, against what, with what methods. |
| `assertions.schema.json` | §9.4 | `assertions.json`: one record per (control, subject); the source of truth for every rendering. |
| `manifest.schema.json` | §8.4 | `manifest.json`: engine, input and output digests, run metadata; the basis of reproducibility. |
| `deviation-register.schema.json` | §6.5, App F | `deviations.yaml`: accepted or temporary deviations with owner, approver, and expiry. |
| `applicability-statement.schema.json` | App F | `applicability-statement.json`: which controls apply and why, drift, class justifications. |
| `quarantine.schema.json` | App F | One record of `quarantine.jsonl`: a refused item and its stable reason code. |
| `integrity-result.schema.json` | §6.6, App F | An `IntegrityResult`: per-stream verification status, strength, and anchors. |
| `oscal-assessment-results.schema.json` | §9 | `oscal-ar.json`: the AgentCE profile of OSCAL Assessment Results a governance platform consumes. |
| `results-sarif.schema.json` | §9.4 | `results.sarif`: the SARIF a CI system gates on. |
| `oscal-component-definition.schema.json` | §2.1, §7.3 | The AgentCE profile of an OSCAL Component Definition. |
| `oscal-component-definition.json` | §2.1, §7.3 | AgentCE as an OSCAL `validation` component: one implemented requirement per base-catalog control. Generated. |
| `examples/` | — | A worked example per schema. |
| `vendor/` | §9 | Third-party schemas AgentCE validates against but does not author (the real NIST OSCAL 1.1.2 assessment-results schema), each with a recorded source, retrieval date, and digest. |

The applicability profile schema is published under `spec/model/generated/json-schema/` alongside the
evidence schema. The manual evaluation record (a completed checklist) is validated by
`spec/rules/checklist.schema.json`.

## Use

All schemas are JSON Schema 2020-12. `spec/validate_examples.py` validates every example above against
its schema (eval P0.6):

```
cd spec && uv run python validate_examples.py
```

The OSCAL component definition is generated from the base catalog and validated against its profile
schema. Regenerate it after the catalog's control list changes, then validate:

```
cd spec/report && uv run python build_oscal_component.py
cd spec/report && uv run python validate_oscal.py
```

`validate_oscal.py` refuses a committed file that a fresh generation would not reproduce, so the
document never drifts from the catalog. The obligation crosswalks to regulatory and standards clauses
live beside the base catalog under `spec/catalogs/base/eu-ai-act/crosswalk/`.

`oscal-assessment-results.schema.json` is a bounded local profile; each engine additionally validates
its emitted `oscal-ar.json` against the real, vendored NIST OSCAL 1.1.2 schema (`vendor/`) -- in the
Python engine, `agentce.report.validate_oscal_ar_nist`, reachable via `agentce report --validate` and
guarded in CI by the `three-engine.yml` `validate-schemas` job.

Nothing here opens a network connection or uses a learned component.
