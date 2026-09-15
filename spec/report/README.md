# Report and claim schemas

Implements the specification: §9.1 (conformance claim), §9.4 (machine-readable outputs), §8.4
(reproducibility manifest), §6.5 (secondary inputs), and the enumerations of Appendix F.

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
| `examples/` | — | A worked example per schema. |

The applicability profile schema is published under `spec/model/generated/json-schema/` alongside the
evidence schema. The manual evaluation record (a completed checklist) is validated by
`spec/rules/checklist.schema.json`.

## Use

All schemas are JSON Schema 2020-12. `spec/validate_examples.py` validates every example above against
its schema (eval P0.6):

```
cd spec && uv run python validate_examples.py
```

Nothing here opens a network connection or uses a learned component.
