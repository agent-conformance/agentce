# Engine conformance suite

Implements the conformance-program tooling around the Engine Conformance Suite (SPEC §11.5–11.6). The
ECS runner itself is the engine's `agentce conformance run` (item 1.11); this directory holds the
release gates and, in later phases, the published implementation reports and their registry checks.

## Precision/recall gate (SPEC §11.6)

[`precision_gate.py`](precision_gate.py) scores the engine against the corpus ground truth and passes
only when every seeded fault is detected and no known-pass control is flagged:

```
cd conformance && uv run python precision_gate.py --corpus ../corpus
```

It prints the metric and `GATE PASS` (exit 0) when recall is 1.0 and the rung-2 false-positive rate is
0.0, or `GATE FAIL` (exit 1) otherwise — the failure a rule regression must surface (DC-11). Restrict
it to one control family with `--family REC`. The metric is computed by
[`corpus.precision_recall`](../corpus/precision_recall.py), which the phase-1 eval also calls directly
as `python -m corpus.precision_recall --corpus <dir> --json`.

**Scope.** The authored corpus seeds faults for `REC-04`, `OVS-03`, `INT-01`, and `INC-02` — the gate
scores 4 of the 33 automated controls the base catalog defines (49 controls total). A regression in
any of the other automated controls does not move this gate; "recall 1.0 / FPR 0.0" names that scope,
not the whole catalog. The gate prints its scored-control set (`scored_controls`) on every run so the
scope is computed live against the corpus and catalog on disk, never hard-coded. Widening the
authored ground truth to more controls is tracked as future corpus work, through the deterministic
generator.
