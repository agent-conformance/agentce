# Engine conformance suite

Implements the conformance-program tooling around the Engine Conformance Suite (SPEC §11.5–11.6). The
ECS runner itself is the engine's `agentce conformance run` (item 1.11); this directory holds the
release gates, the published implementation reports and their registry checks, and the catalog and
implementation-report registries themselves (`registry/`, see [`registry/README.md`](registry/README.md)).

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

## Depth & scale checks (item 16.4)

Four checkers extend the release gates above with richer coverage, a mutation-testing regression
floor, a fresh performance measurement, and a hardened air-gap bundle:

- [`redteam_probe_check.py`](redteam_probe_check.py) scores the three red-team probe corpora under
  `corpus/probe/redteam/{computer-use,voice,rag}/v1/` — each a modality beyond tool-calling/multi-agent
  prompt injection — and proves recall 1.0 by exact case-id identity, with zero false positives, using
  the engine's existing `absence`, `policy_violation`, and `structural` oracles unmodified:

  ```
  cd conformance && uv run python redteam_probe_check.py --self-test
  cd conformance && uv run python redteam_probe_check.py
  ```

- [`mutation_check.py`](mutation_check.py) gates the evaluator core's mutation score at the Phase-1
  floor (`≥ 0.75`, SPEC §4 P1.5), reading the real, committed `engines/python/mutation.json`, and also
  runs `redteam_probe_check.py`'s full corpus verification. Its bare (no-flag) mode re-runs the real
  `engines/python/mutation.py` harness inside a throwaway `git worktree`, so a hard kill mid-sweep can
  never leave the tracked evaluator source mutated:

  ```
  cd conformance && uv run python mutation_check.py --self-test
  cd conformance && uv run python mutation_check.py
  ```

- [`perf_gate_check.py`](perf_gate_check.py) is a thin wrapper around `perf.py`'s own `--run`/`--check`
  (the Phase-3 `P3.7` measured-or-ADR pattern, unmodified): a fresh performance measurement or a
  documented ADR gap (`docs/adr/0009-performance.md`) is recorded on every run:

  ```
  cd conformance && uv run python perf_gate_check.py --self-test
  cd conformance && uv run python perf_gate_check.py
  ```

- [`offline_bundle.py`](offline_bundle.py)'s `build_bundle`/`verify_bundle` are generalized to
  auto-discover and carry every signed base catalog under `spec/catalogs/base/*` (both EU AI Act and
  NIST AI RMF), each independently verified offline; a partial or corrupted multi-catalog bundle fails
  loudly, naming the failing catalog id. The `tools`-side wrapper
  ([`tools/offline_bundle.py`](../tools/offline_bundle.py)) builds a fresh bundle to a temp directory
  and verifies it offline, then cleans up:

  ```
  uv run --project tools --frozen python -m offline_bundle --verify --no-network
  ```
