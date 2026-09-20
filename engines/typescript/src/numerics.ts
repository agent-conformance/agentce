/**
 * Exact numerics for rung-3 statistical evaluation (SPEC §7.2; `spec/rules/numerics.md`).
 *
 * A faithful port of the Python reference `numerics.py`: every value a threshold is compared against
 * is computed in exact rational arithmetic (a BigInt `Frac`) or in fixed 30-significant-digit decimal
 * arithmetic (decimal.js configured to precision 30, half-even — the same correctly-rounded General
 * Decimal Arithmetic Python's `decimal` performs), so a conforming engine agrees to the last published
 * digit. The cross-language vectors in `spec/rules/numerics-vectors/` are the golden both engines meet.
 */

import Decimal from "decimal.js";

import { CanonicalizationError, canonicalString } from "./canonical";
import { parseJson } from "./json";
import { compareLiterals } from "./structural";

const D = Decimal.clone({ precision: 30, rounding: Decimal.ROUND_HALF_EVEN });
const LENTZ_FLOOR = new D("1e-30");
const LENTZ_CONVERGENCE = new D("1e-30");
const LENTZ_MAX_TERMS = 500;
const BISECTION_TOLERANCE = new D("1e-12");

export class NumericsError extends Error {}

// --- exact rationals ---------------------------------------------------------------------------

function gcd(a: bigint, b: bigint): bigint {
  let x = a < 0n ? -a : a;
  let y = b < 0n ? -b : b;
  while (y !== 0n) {
    [x, y] = [y, x % y];
  }
  return x;
}

/** A reduced rational with a positive denominator, matching Python's `fractions.Fraction`. */
export class Frac {
  readonly n: bigint;
  readonly d: bigint;

  constructor(n: bigint, d: bigint) {
    if (d === 0n) {
      throw new NumericsError("zero denominator");
    }
    const sign = d < 0n ? -1n : 1n;
    const num = n * sign;
    const den = d * sign;
    const g = gcd(num, den) || 1n;
    this.n = num / g;
    this.d = den / g;
  }

  static parse(text: string): Frac {
    const cleaned = text.trim();
    if (cleaned.includes("e") || cleaned.includes("E")) {
      throw new NumericsError(`not a plain decimal: ${JSON.stringify(text)}`);
    }
    const negative = cleaned.startsWith("-");
    const body = cleaned.replace(/^[+-]/, "");
    const [intPart, fracPart = ""] = body.split(".");
    const digits = `${intPart}${fracPart}` || "0";
    const num = BigInt(digits) * (negative ? -1n : 1n);
    return new Frac(num, 10n ** BigInt(fracPart.length));
  }

  mulInt(k: bigint): Frac {
    return new Frac(this.n * k, this.d);
  }

  /** ceil(this) as a BigInt (this is non-negative wherever the kernel calls it). */
  ceil(): bigint {
    const q = this.n / this.d;
    return this.n % this.d !== 0n && this.n > 0n ? q + 1n : q;
  }

  cmp(other: Frac): number {
    const lhs = this.n * other.d;
    const rhs = other.n * this.d;
    return lhs < rhs ? -1 : lhs > rhs ? 1 : 0;
  }

  toString(): string {
    return this.d === 1n ? this.n.toString() : `${this.n}/${this.d}`;
  }
}

export function parseRational(text: string): Frac {
  return Frac.parse(text);
}

/** The `q`-quantile of a population by the nearest-rank definition (numerics.md §1). */
export function nearestRankQuantile(values: Frac[], q: Frac): Frac {
  const n = values.length;
  if (n < 1) {
    throw new NumericsError("quantile of an empty population");
  }
  const ordered = [...values].sort((a, b) => a.cmp(b));
  let k = q.mulInt(BigInt(n)).ceil();
  if (k < 1n) {
    k = 1n;
  } else if (k > BigInt(n)) {
    k = BigInt(n);
  }
  return ordered[Number(k) - 1] as Frac;
}

export function compare(lhs: Frac, op: string, rhs: Frac): boolean {
  const c = lhs.cmp(rhs);
  switch (op) {
    case ">=":
      return c >= 0;
    case "<=":
      return c <= 0;
    case ">":
      return c > 0;
    case "<":
      return c < 0;
    case "==":
      return c === 0;
    case "!=":
      return c !== 0;
    default:
      throw new NumericsError(`unknown comparison operator ${JSON.stringify(op)}`);
  }
}

// --- the Clopper–Pearson decimal path ----------------------------------------------------------

function factorial(n: number): bigint {
  let result = 1n;
  for (let i = 2n; i <= BigInt(n); i += 1n) {
    result *= i;
  }
  return result;
}

/** B(a, b) for positive-integer parameters, exactly, as a decimal.js value (numerics.md §4). */
function betaConstant(a: number, b: number): Decimal {
  const num = factorial(a - 1) * factorial(b - 1);
  const den = factorial(a + b - 1);
  return new D(num.toString()).div(new D(den.toString()));
}

function decimalPower(base: Decimal, exponent: number): Decimal {
  let result = new D(1);
  for (let i = 0; i < exponent; i += 1) {
    result = result.times(base);
  }
  return result;
}

function lentzContinuedFraction(x: Decimal, a: number, b: number): Decimal {
  const qab = new D(a + b);
  const qap = new D(a + 1);
  const qam = new D(a - 1);
  let c = new D(1);
  let d = new D(1).minus(qab.times(x).div(qap));
  if (d.abs().lt(LENTZ_FLOOR)) {
    d = LENTZ_FLOOR;
  }
  d = new D(1).div(d);
  let h = d;
  for (let m = 1; m <= LENTZ_MAX_TERMS; m += 1) {
    const m2 = 2 * m;
    // even step
    let aa = new D(m)
      .times(new D(b).minus(new D(m)))
      .times(x)
      .div(qam.plus(new D(m2)).times(new D(a).plus(new D(m2))));
    d = new D(1).plus(aa.times(d));
    if (d.abs().lt(LENTZ_FLOOR)) {
      d = LENTZ_FLOOR;
    }
    c = new D(1).plus(aa.div(c));
    if (c.abs().lt(LENTZ_FLOOR)) {
      c = LENTZ_FLOOR;
    }
    d = new D(1).div(d);
    h = h.times(d).times(c);
    // odd step
    aa = new D(a)
      .plus(new D(m))
      .times(qab.plus(new D(m)))
      .times(x)
      .div(new D(a).plus(new D(m2)).times(qap.plus(new D(m2))))
      .negated();
    d = new D(1).plus(aa.times(d));
    if (d.abs().lt(LENTZ_FLOOR)) {
      d = LENTZ_FLOOR;
    }
    c = new D(1).plus(aa.div(c));
    if (c.abs().lt(LENTZ_FLOOR)) {
      c = LENTZ_FLOOR;
    }
    d = new D(1).div(d);
    const delta = d.times(c);
    h = h.times(delta);
    if (delta.minus(new D(1)).abs().lt(LENTZ_CONVERGENCE)) {
      return h;
    }
  }
  throw new NumericsError(`incomplete beta did not converge for a=${a}, b=${b}, x=${x}`);
}

/** I_x(a, b), the regularised incomplete beta function (numerics.md §4). */
export function regularisedIncompleteBeta(x: Decimal, a: number, b: number): Decimal {
  if (a < 1 || b < 1) {
    throw new NumericsError(`integer beta parameters must be >= 1, got a=${a}, b=${b}`);
  }
  if (x.lte(0)) {
    return new D(0);
  }
  if (x.gte(1)) {
    return new D(1);
  }
  const pivot = new D(a).plus(new D(1)).div(new D(a).plus(new D(b)).plus(new D(2)));
  if (x.gt(pivot)) {
    return new D(1).minus(regularisedIncompleteBeta(new D(1).minus(x), b, a));
  }
  const numerator = decimalPower(x, a).times(decimalPower(new D(1).minus(x), b));
  const leading = numerator.div(new D(a).times(betaConstant(a, b)));
  return leading.times(lentzContinuedFraction(x, a, b));
}

/** The p-quantile of Beta(a, b) by bisection on I_x(a, b) (numerics.md §3). */
export function betaQuantile(a: number, b: number, p: Decimal): Decimal {
  if (!(p.gte(0) && p.lte(1))) {
    throw new NumericsError(`quantile probability out of range: ${p}`);
  }
  let lo = new D(0);
  let hi = new D(1);
  while (hi.minus(lo).gt(BISECTION_TOLERANCE)) {
    const mid = lo.plus(hi).div(2);
    if (regularisedIncompleteBeta(mid, a, b).lt(p)) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return lo.plus(hi).div(2);
}

export interface Interval {
  lower: Decimal;
  upper: Decimal;
}

/** The Clopper–Pearson exact interval for k successes in n trials (numerics.md §2). */
export function clopperPearson(k: number, n: number, alpha: Decimal): Interval {
  if (!(k >= 0 && k <= n) || n < 1) {
    throw new NumericsError(`clopper_pearson needs 0 <= k <= n and n >= 1, got k=${k}, n=${n}`);
  }
  const half = alpha.div(2);
  const lower = k === 0 ? new D(0) : betaQuantile(k, n - k + 1, half);
  const upper = k === n ? new D(1) : betaQuantile(k + 1, n - k, new D(1).minus(half));
  return { lower, upper };
}

/** Round a decimal half-even to four decimal places and render it (numerics.md §5). */
export function roundPresentation(value: Decimal): string {
  return value.toDecimalPlaces(4, Decimal.ROUND_HALF_EVEN).toFixed(4);
}

// --- vector-file dispatcher (drives the P3.1 cross-language check) ------------------------------

interface VectorCase {
  name: string;
  [key: string]: unknown;
}

/** Compute every case in a `numerics-vectors` file; return {caseName: result} for comparison. */
export function computeVectorFile(data: { algorithm: string; cases: VectorCase[] }): Record<
  string,
  unknown
> {
  const out: Record<string, unknown> = {};
  for (const c of data.cases) {
    out[c.name] = computeCase(data.algorithm, c);
  }
  return out;
}

function computeCase(algorithm: string, c: VectorCase): unknown {
  switch (algorithm) {
    case "nearest_rank_quantile": {
      const values = (c.values as string[]).map(parseRational);
      return nearestRankQuantile(values, parseRational(c.q as string)).toString();
    }
    case "compare": {
      const lhs = new Frac(BigInt(c.numerator as number), BigInt(c.denominator as number));
      return compare(lhs, c.op as string, parseRational(c.threshold as string));
    }
    case "clopper_pearson": {
      const interval = clopperPearson(c.k as number, c.n as number, new D(c.alpha as string));
      return { lower: roundPresentation(interval.lower), upper: roundPresentation(interval.upper) };
    }
    case "regularised_incomplete_beta":
      return roundPresentation(
        regularisedIncompleteBeta(new D(c.x as string), c.a as number, c.b as number),
      );
    case "beta_quantile":
      return roundPresentation(betaQuantile(c.a as number, c.b as number, new D(c.p as string)));
    case "canonical_token": {
      try {
        return `canonical:${canonicalString(parseJson(c.input as string))}`;
      } catch (exc) {
        if (exc instanceof CanonicalizationError) {
          return `error:${exc.reason}`;
        }
        throw exc;
      }
    }
    case "literal_order": {
      const order = compareLiterals(c.lhs as string, c.rhs as string);
      return order === null ? "incomparable" : order < 0 ? "lt" : order > 0 ? "gt" : "eq";
    }
    default:
      throw new NumericsError(`unknown algorithm ${JSON.stringify(algorithm)}`);
  }
}
