"""Exact numerics for rung-3 statistical evaluation (SPEC §7.2; ``spec/rules/numerics.md``).

This is the reference implementation the cross-language vectors in
``spec/rules/numerics-vectors/`` conform to. Every value a threshold is compared against is
computed in exact rational arithmetic (:class:`fractions.Fraction`) or in fixed
30-significant-digit decimal arithmetic (:class:`decimal.Decimal`); no floating point enters an
outcome. Rendering to a decimal string happens only after the outcome is decided.

The parameters below are fixed by ``numerics.md`` rather than left to a library default, so any
conforming engine agrees to the last published digit. Clopper–Pearson always evaluates the Beta
quantile at positive-integer parameters, so ``B(a, b) = (a-1)! (b-1)! / (a+b-1)!`` is computed
exactly; this is the true beta constant the leading factor requires and is identical across
languages.
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from typing import NamedTuple, Sequence

#: Working precision for every decimal operation in the Clopper–Pearson path (numerics.md §4).
WORKING_PRECISION = 30
#: Absolute tolerance on ``x`` for the Beta-quantile bisection (numerics.md §3).
BISECTION_TOLERANCE = Decimal("1e-12")
#: Tiny floor substituted for any near-zero modified-Lentz coefficient (numerics.md §4).
LENTZ_FLOOR = Decimal("1e-30")
#: Convergence delta: stop when a term changes the running value by less than this (numerics.md §4).
LENTZ_CONVERGENCE = Decimal("1e-30")
#: Iteration cap for the continued fraction; reaching it without convergence is an error (§4).
LENTZ_MAX_TERMS = 500
#: Presentation rounding: half-even to four decimal places (numerics.md §5).
PRESENTATION_QUANTUM = Decimal("0.0001")


class NumericsError(Exception):
    """A numerics computation did not converge; never a silent result (numerics.md §4)."""


def parse_rational(text: str) -> Fraction:
    """Parse a plain decimal string (e.g. ``"0.10"``, ``"15000"``, ``"-3.5"``) as an exact rational.

    Scientific notation is rejected: metric specs supply decimals with a declared scale.
    """
    cleaned = text.strip()
    if "e" in cleaned or "E" in cleaned:
        raise NumericsError(f"not a plain decimal: {text!r}")
    return Fraction(cleaned)


def nearest_rank_quantile(values: Sequence[Fraction], q: Fraction) -> Fraction:
    """The ``q``-quantile of a population by the nearest-rank definition (numerics.md §1).

    For ``N`` values sorted ascending, returns ``x[k]`` with ``k = ceil(q * N)`` clamped to
    ``[1, N]``. Ordering of equal values is immaterial because the value returned is identical.
    """
    n = len(values)
    if n < 1:
        raise NumericsError("quantile of an empty population")
    ordered = sorted(values)
    k = math.ceil(q * n)  # exact: q is a Fraction, so q * n is a Fraction
    k = max(1, min(k, n))
    return ordered[k - 1]


def compare(lhs: Fraction, op: str, rhs: Fraction) -> bool:
    """Compare two exact rationals. Fraction ordering is cross-multiplication; no rounding enters."""
    if op == ">=":
        return lhs >= rhs
    if op == "<=":
        return lhs <= rhs
    if op == ">":
        return lhs > rhs
    if op == "<":
        return lhs < rhs
    if op == "==":
        return lhs == rhs
    if op == "!=":
        return lhs != rhs
    raise NumericsError(f"unknown comparison operator {op!r}")


def _beta_constant(a: int, b: int) -> Fraction:
    """``B(a, b)`` for positive-integer parameters, exactly (numerics.md §4)."""
    return Fraction(
        math.factorial(a - 1) * math.factorial(b - 1), math.factorial(a + b - 1)
    )


def _decimal_power(base: Decimal, exponent: int) -> Decimal:
    """``base ** exponent`` for a non-negative integer exponent by repeated multiplication.

    Naive repeated multiplication (rather than exponentiation-by-squaring or the decimal
    ``**`` operator) fixes the sequence of correctly-rounded 30-digit operations so an
    independent engine reproduces it exactly.
    """
    result = Decimal(1)
    for _ in range(exponent):
        result *= base
    return result


def _lentz_continued_fraction(x: Decimal, a: int, b: int) -> Decimal:
    """The DLMF 8.17.22 continued fraction by the modified Lentz method (numerics.md §4).

    Assumes the working decimal context is already established by the caller.
    """
    qab = Decimal(a + b)
    qap = Decimal(a + 1)
    qam = Decimal(a - 1)
    c = Decimal(1)
    d = Decimal(1) - qab * x / qap
    if abs(d) < LENTZ_FLOOR:
        d = LENTZ_FLOOR
    d = Decimal(1) / d
    h = d
    for m in range(1, LENTZ_MAX_TERMS + 1):
        m2 = 2 * m
        # even step
        aa = (
            Decimal(m)
            * (Decimal(b) - Decimal(m))
            * x
            / ((qam + Decimal(m2)) * (Decimal(a) + Decimal(m2)))
        )
        d = Decimal(1) + aa * d
        if abs(d) < LENTZ_FLOOR:
            d = LENTZ_FLOOR
        c = Decimal(1) + aa / c
        if abs(c) < LENTZ_FLOOR:
            c = LENTZ_FLOOR
        d = Decimal(1) / d
        h = h * d * c
        # odd step
        aa = (
            -(Decimal(a) + Decimal(m))
            * (qab + Decimal(m))
            * x
            / ((Decimal(a) + Decimal(m2)) * (qap + Decimal(m2)))
        )
        d = Decimal(1) + aa * d
        if abs(d) < LENTZ_FLOOR:
            d = LENTZ_FLOOR
        c = Decimal(1) + aa / c
        if abs(c) < LENTZ_FLOOR:
            c = LENTZ_FLOOR
        d = Decimal(1) / d
        delta = d * c
        h = h * delta
        if abs(delta - Decimal(1)) < LENTZ_CONVERGENCE:
            return h
    raise NumericsError(f"incomplete beta did not converge for a={a}, b={b}, x={x}")


def regularised_incomplete_beta(x: Decimal, a: int, b: int) -> Decimal:
    """``I_x(a, b)``, the regularised incomplete beta function (numerics.md §4).

    Evaluated by the modified-Lentz continued fraction with the symmetry transform
    ``I_x(a, b) = 1 - I_{1-x}(b, a)`` when ``x > (a+1)/(a+b+2)`` so the fraction is always used
    in its region of fast convergence.
    """
    if a < 1 or b < 1:
        raise NumericsError(f"integer beta parameters must be >= 1, got a={a}, b={b}")
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        ctx.rounding = ROUND_HALF_EVEN
        if x <= 0:
            return Decimal(0)
        if x >= 1:
            return Decimal(1)
        pivot = (Decimal(a) + Decimal(1)) / (Decimal(a) + Decimal(b) + Decimal(2))
        if x > pivot:
            return Decimal(1) - regularised_incomplete_beta(Decimal(1) - x, b, a)
        beta = _beta_constant(a, b)
        # leading factor x^a (1-x)^b / (a * B(a, b))
        numerator = _decimal_power(x, a) * _decimal_power(Decimal(1) - x, b)
        leading = numerator / (
            Decimal(a) * (Decimal(beta.numerator) / Decimal(beta.denominator))
        )
        return leading * _lentz_continued_fraction(x, a, b)


def beta_quantile(a: int, b: int, p: Decimal) -> Decimal:
    """The ``p``-quantile of ``Beta(a, b)`` by bisection on ``I_x(a, b)`` (numerics.md §3)."""
    if not (Decimal(0) <= p <= Decimal(1)):
        raise NumericsError(f"quantile probability out of range: {p}")
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        ctx.rounding = ROUND_HALF_EVEN
        lo = Decimal(0)
        hi = Decimal(1)
        while hi - lo > BISECTION_TOLERANCE:
            mid = (lo + hi) / 2
            if regularised_incomplete_beta(mid, a, b) < p:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2


class Interval(NamedTuple):
    """A two-sided confidence interval, as exact decimals before presentation rounding."""

    lower: Decimal
    upper: Decimal


def clopper_pearson(k: int, n: int, alpha: Decimal) -> Interval:
    """The Clopper–Pearson exact interval for ``k`` successes in ``n`` trials (numerics.md §2).

    ``alpha`` is the two-sided error (confidence ``1 - alpha``). The lower bound is ``0`` when
    ``k == 0`` and the upper bound is ``1`` when ``k == n``; each other bound is a Beta quantile.
    """
    if not (0 <= k <= n) or n < 1:
        raise NumericsError(
            f"clopper_pearson needs 0 <= k <= n and n >= 1, got k={k}, n={n}"
        )
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        ctx.rounding = ROUND_HALF_EVEN
        half = alpha / 2
        lower = Decimal(0) if k == 0 else beta_quantile(k, n - k + 1, half)
        upper = Decimal(1) if k == n else beta_quantile(k + 1, n - k, Decimal(1) - half)
        return Interval(lower, upper)


def round_presentation(value: Decimal) -> Decimal:
    """Round a decimal half-even to four decimal places for the report (numerics.md §5)."""
    return value.quantize(PRESENTATION_QUANTUM, rounding=ROUND_HALF_EVEN)


def rational_to_presentation(value: Fraction) -> Decimal:
    """Render an exact rational statistic half-even to four decimal places (numerics.md §5)."""
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        ctx.rounding = ROUND_HALF_EVEN
        exact = Decimal(value.numerator) / Decimal(value.denominator)
    return round_presentation(exact)
