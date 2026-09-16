# 0009 — Performance harness and the path to the 50M-event scale target

Status: accepted
Spec refs: §8.6, §8.2, B4

## Context

SPEC §8.6 sets the reference engine's scale targets: a 50M-event bundle over 10,000 subjects and a
90-day window, assessed against the base catalog, in ≤ 60 minutes of wall clock on a 16-core host
within ≤ 32 GB of RAM, with an on-disk store; incremental nightly runs over a trailing window in
≤ 10 minutes. Other engines must be within 2× to claim conformance. The targets explicitly assume the
Portable Shape Profile is *compiled to query plans* over the store (native graph, SQL, or SPARQL)
rather than interpreted by a general-purpose SHACL validator, which is used only for cross-checking on
small graphs and in the ECS.

Item 3.7 asks for a performance harness (a synthetic scale test, nightly) and PSP plan optimisation.
Running a genuine 50M-event nightly on a 16-core host is out of scope for the governed build (there is
no such host in the loop), so this ADR records the harness, the measured per-stage throughput, the
extrapolation to the target, the resulting gap, and the plan that closes it — the alternative the item
acceptance permits ("a documented run within targets, or an ADR documenting the gap and plan").

## Decision

Ship `conformance/perf.py` as the harness and P3.7 gate. `perf.py --run` measures two costs on a
synthetic stream — per-event ingest throughput (schema validation, the dominant per-event cost) and
per-subject assessment cost (graph construction plus structural evaluation) — extrapolates each to the
50M-event / 10k-subject target, and records the result. `perf.py --check` passes when a recorded run
is within targets, otherwise when this ADR is present, and prints which path it took.

Measured on a developer workstation, single-threaded (not the reference host):

- **Ingest**: ≈ 3,400 events/second. Extrapolated single-core, 50M events ≈ 4 hours.
- **Assess**: ≈ 0.5 ms/subject. Extrapolated, 10k subjects ≈ 5 seconds.

The assessment path is already well within budget: the PSP is compiled to store queries over the
materialised subclass closure (ADR-0002, ADR-0001), not interpreted by a SHACL library, so structural
evaluation over the full population is cheap. The gap is entirely in single-threaded ingest.

The plan that closes the gap, in priority order:

1. **Parallelise ingest across the 16 cores.** Validation, canonical-hashing, and quarantine are
   independent per event and per integrity stream, so ingest is embarrassingly parallel by source
   file. 50M events at ≈ 3,400 events/second/core over 16 cores is ≈ 15 minutes — within the 60-minute
   target with headroom for the ≈ 5-second assessment.
2. **Stream ingest into the on-disk store** (ADR-0001) rather than holding events in memory, bounding
   RAM to the working set and the store's page cache well under 32 GB at 50M events.
3. **Keep the PSP compiled to query plans** (already the case): the structural evaluator issues
   byte-ordered store queries, never a general-purpose SHACL validator, on the full population.
4. **Incremental nightly runs** assess only a trailing window, a small fraction of 50M events, so the
   ≤ 10-minute nightly target follows from (1)–(3).

## Alternatives considered (with why not)

- **Interpret the PSP with a SHACL library on the full population.** Rejected by SPEC §8.6 and ADR-0002:
  a general-purpose validator does not meet the target and is used only for small-graph cross-checks.
- **A single-threaded ingest.** Measured at ≈ 4 hours for 50M events — over target. Parallelism is the
  cheapest lever because ingest is independent per stream.
- **Committing a passing perf-results.json.** Rejected: there is no 16-core reference host in the build
  loop, so a recorded in-target run would be fabricated. The gate passes honestly on this ADR, and
  `--run` records the real (out-of-target, single-core) measurement when invoked.

## Consequences (including determinism, portability to TypeScript/Java, performance)

Determinism is unaffected: the harness measures wall-clock throughput and never feeds an assessment
outcome. Parallel ingest must preserve the per-stream time-order and duplicate-id checks, which are
per-stream and so composable without changing results (the two-engine byte-identity gate, P3.4, guards
against any regression). The plan is portable: the TypeScript and Java engines parallelise ingest with
their own runtimes and keep the same compiled-PSP assessment. Until parallel ingest lands, the engine
meets the target only on the assessment path; the largest committed corpus project (200k events) is the
standing smoke test that the pipeline runs at volume.

## Verification (the test or check that proves the decision holds)

`cd conformance && uv run python perf.py --check` passes (via this ADR) and prints which path it took;
`perf.py --run` records the measured throughput and its extrapolation to the target.
`conformance/tests/test_perf.py` exercises both: that `measure` reports a positive ingest rate and an
extrapolation, and that the gate passes with this ADR present. The 50M / 60-min / 16-core target itself
is verified when a nightly run on a reference host is recorded (HUMAN action, tracked for GA).
