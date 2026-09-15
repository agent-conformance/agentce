# Numerics

Normative for rung-3 statistical evaluation. Implements the specification, §7.2 ("Metric spec"), and
§8.2 ("Determinism guarantees"). It names the exact algorithm and every parameter each statistic is
computed by, so that independent engines agree to the last published digit. An engine may not
substitute another evaluation method even if it agrees with the published golden vectors; the Engine
Conformance Suite pins golden values for every interval it exercises, and an engine that differs in
any digit fails conformance.

**Floating point is never used in an outcome.** Every value that a threshold is compared against is
computed in exact rational arithmetic or in fixed-precision decimal arithmetic as specified below.
Rendering to a decimal string for the report happens only after the outcome is decided.

## 1. Exact statistics

Counts, ratios, and proportions are computed as exact rationals (integer numerator over integer
denominator); no division to a floating value occurs. A ratio is compared to its threshold by
cross-multiplication, so no rounding enters the comparison.

**Quantiles use the nearest-rank definition.** For a population of `N` values (`N >= 1`) sorted in
ascending order as `x[1] <= x[2] <= ... <= x[N]`, the `q`-quantile for `q` in `(0, 1]` is `x[k]`
where `k = ceil(q * N)` computed in exact rational arithmetic with `k` clamped to `[1, N]`. Ordering
of equal values is immaterial because the value returned is identical. The metric spec supplies `q`
as a decimal string (for example `"0.10"`); it is parsed as an exact rational, never as a float.

## 2. Proportion confidence intervals: Clopper–Pearson

A confidence interval for a proportion (for example an attack success rate, §7.2 probe spec) uses the
**Clopper–Pearson exact interval**. For `k` successes in `N` trials at two-sided confidence
`1 - alpha`:

- lower bound `= 0` when `k = 0`, otherwise the `alpha/2` quantile of `Beta(k, N - k + 1)`;
- upper bound `= 1` when `k = N`, otherwise the `1 - alpha/2` quantile of `Beta(k + 1, N - k)`.

Each bound is a quantile of the Beta distribution, computed by algorithm 3.

## 3. Beta quantile by bisection on the regularised incomplete beta function

The `p`-quantile of `Beta(a, b)` is the value `x` in `[0, 1]` with `I_x(a, b) = p`, where `I_x(a, b)`
is the **regularised incomplete beta function**. It is found by **bisection** on `x`:

- initial bracket `[lo, hi] = [0, 1]`;
- at each step evaluate `I_mid(a, b)` for `mid = (lo + hi) / 2` (algorithm 4) and keep the half that
  brackets `p` (`I` is monotone increasing in `x`);
- **termination:** stop when `hi - lo <= 1e-12` (absolute tolerance on `x`); return `mid`.

Bisection is chosen over a Newton step because it needs no derivative and its error bound is exact and
monotone, which keeps the result identical across engines.

## 4. Regularised incomplete beta function: continued fraction, modified Lentz

`I_x(a, b)` is evaluated by the continued fraction of **DLMF 8.17.22**, summed by the **modified
Lentz method**, with these parameters:

- **decimal arithmetic at 30 significant digits** (the working precision for every operation in this
  section and in algorithm 3);
- **symmetry transform for convergence:** when `x > (a + 1) / (a + b + 2)`, evaluate
  `I_x(a, b) = 1 - I_{1-x}(b, a)` so the continued fraction is always used in its region of fast
  convergence;
- the leading factor `x^a * (1 - x)^b / (a * B(a, b))` where `B(a, b)` is the beta function, computed
  from log-gamma at the working precision;
- **modified Lentz** with a tiny floor of `1e-30` substituted for any coefficient whose magnitude
  falls below it (avoids division by zero);
- **iteration cap of 500** terms; convergence is declared when a term changes the running value by
  less than `1e-30` at the working precision. Reaching the cap without convergence is an engine error,
  never a silent result.

## 5. Rounding for presentation

A computed interval bound or statistic is rounded **half-even to four decimal places** only when it is
rendered into `report.md` / `report.html` or `assertions.json`. Outcomes are decided on the exact
(rational) or 30-significant-digit (decimal) value before this rounding; the rounded string is a
presentation of the decided outcome, not an input to it.

## 6. Determinism

Given identical inputs, every value in this document is identical on any platform and any supported
language runtime: there are no transcendental library calls in the outcome path, no floating-point
accumulation, and every parameter (precision, tolerance, iteration cap, rounding mode) is fixed here
rather than left to a library default. The reference implementation and its cross-language test
vectors land with the statistical evaluator; this document is the specification they conform to.
