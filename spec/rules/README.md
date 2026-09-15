# Rule format and expression profiles

Implements the specification, §7.1 ("Rule format") and §7.2 ("Expression languages"). This directory
defines how a control is written and how its expectations are expressed at each rung of the
correctness ladder.

## Contents

| File | Implements | Purpose |
|---|---|---|
| `control.schema.json` | §7.1 | JSON Schema (2020-12) for one control document. References the metric, probe, and checklist schemas. |
| `metric.schema.json` | §7.2 | Rung-3 statistical metric specification. |
| `probe.schema.json` | §7.2 | Rung-3 effectiveness probe specification. |
| `checklist.schema.json` | §7.2, Appendix D | Manual / semi-automated checklist. |
| `psp.md` | §7.2, Appendix B | The Portable Shape Profile: the SHACL Core subset every engine executes identically. |
| `psp_check.py` | §7.2 | Enforces the profile: `PSP OK` (exit 0) or `REFUSED: <feature>` (exit 1). |
| `numerics.md` | §7.2, §8.2 | The exact algorithms and parameters for every statistic; no floating point in an outcome. |
| `examples/` | — | The specification's worked control, metric, checklist, probe, and shape. |
| `check_rules.py` | — | Validates the examples against the schemas and the shape against the profile. |

## Use

```
uv sync
uv run python psp_check.py <shape.ttl>        # check a shape against the Portable Shape Profile
uv run python check_rules.py                  # validate the worked examples against the schemas
uv run --group dev pytest -q tests            # the psp_check contract tests
```

A control's `evaluation.shape` points at a Portable Shape Profile file (`psp.md`); its `metric`,
`probe`, and `checklist` members are validated by the sibling schemas. Nothing here opens a network
connection or uses a learned component: the schemas are declarative and `psp_check` is a structural
check over the shapes graph.
