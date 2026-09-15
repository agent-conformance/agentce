# 0007 — Corpus generator design

Status: accepted
Spec refs: SPEC §11, §8.2, §3.1; ADR-0003

## Context

The simulated corpus (SPEC §11) is the shared dataset for the Engine Conformance Suite and for
documentation snippets. It must be reproducible byte for byte, contain no learned component (SPEC §3.1,
HR-1/HR-2), carry ground-truth labels and expected outcomes, and be safe to publish (no real personal
data). It also has to scale to roughly 150 projects across domains, implementation styles, and variants.

## Decision

The corpus is produced by a **deterministic generator that is a pure function of an explicit seed**. Each
project is generated from its own seeded pseudo-random stream; the generator reads no wall clock, opens
no network connection, and uses no learned model. It emits canonical evidence events through the
canonical form (ADR-0003) together with a ground-truth README and the expected six-outcome results per
control. Seeded faults are injected by rule, not by sampling real traffic.

## Alternatives considered (with why not)

- **Recording and anonymising real agent traffic.** Not reproducible, raises privacy and licensing
  problems, and cannot be shared as a stable conformance dataset.
- **An LLM-based synthesizer.** Introduces a learned component into the toolchain, violating HR-1/HR-2,
  and is non-deterministic; a fork could not reproduce the corpus.
- **Fixed hand-written fixtures only.** Do not scale to ~150 projects or exercise the statistical and
  multi-agent paths; retained as small rule fixtures alongside the generator.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- **Determinism.** The same seed yields a byte-identical corpus, checked the way model generation is
  (generate twice, `diff -r` empty); published corpus versions carry pinned digests.
- **Portability.** The corpus is a frozen artifact every engine's ECS consumes, so it does not depend on
  the generator's language; the generator is specified precisely enough to reproduce.
- **Performance.** Generation is linear in the number of events and parallelisable per project.

## Verification (the test or check that proves the decision holds)

A determinism CI job regenerates the corpus and requires `diff -r` to be empty; the `no_ml` check clears
the generator's dependency tree; the ECS runs against the frozen, digest-pinned corpus.
