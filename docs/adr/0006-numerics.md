# 0006 — Numerics implementation

Status: accepted
Spec refs: SPEC §7.2, §8.2, §8.7; spec/rules/numerics.md

## Context

Rung-3 statistics must agree to the last published digit across independent engines (SPEC §7.2), and
floating point may never enter an outcome (SPEC §8.2). The demanding case is the Clopper–Pearson
proportion interval, whose bounds are Beta-distribution quantiles requiring the regularised incomplete
beta function.

## Decision

Follow `spec/rules/numerics.md` exactly:

- Counts, ratios, and nearest-rank quantiles use **exact rationals** (Python `fractions.Fraction`);
  ratio comparisons are by cross-multiplication, so no rounding enters a comparison.
- Clopper–Pearson bounds are computed by **bisection on the regularised incomplete beta function**,
  which is evaluated by the DLMF 8.17.22 continued fraction with the modified Lentz method, in
  **`decimal` arithmetic at 30 significant digits**, a fixed 500-iteration cap, and an absolute bisection
  tolerance of 1e-12; results are rounded half-even to four decimals only for presentation.

No scientific-computing library is used in the outcome path.

## Alternatives considered (with why not)

- **SciPy / statsmodels.** Would compute the interval in floating point (last-digit reproducibility not
  guaranteed across platforms) and pull a large scientific stack whose transitive dependencies the
  `no_ml` denylist must clear; a heavyweight dependency for two functions.
- **Native floating-point math.** Non-deterministic across platforms and compilers; forbidden in the
  outcome path.
- **A different beta evaluation (e.g. series or Newton).** Might agree on published vectors but not in
  every digit; the specification pins one algorithm so engines cannot silently substitute another.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- **Determinism.** Every parameter (precision, tolerance, iteration cap, rounding mode) is fixed in
  `numerics.md`; the same inputs give the same digits on any platform.
- **Portability.** The TypeScript engine uses `BigInt` rationals and `decimal.js`, and the Java engine
  `BigInteger`/`BigDecimal`, with the same parameters (ADR-0008).
- **Performance.** Bounded by the 500-iteration cap; statistics run over grouped populations, off the
  per-event path.

## Verification (the test or check that proves the decision holds)

The statistical evaluator ships cross-language test vectors (item 3.1) and the ECS pins golden interval
values for every interval it exercises; an engine that differs in any digit fails conformance.
