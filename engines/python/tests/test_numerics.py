"""The reference numerics kernel against the cross-language vectors and its boundaries (§7.2)."""

from __future__ import annotations

import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from agentce import numerics as N

CASES = (
    Path(__file__).resolve().parents[3]
    / "spec"
    / "rules"
    / "numerics-vectors"
    / "cases"
)


def _load(name: str) -> list[dict[str, Any]]:
    return list(json.loads((CASES / name).read_text(encoding="utf-8"))["cases"])


# --- The engine reproduces every published vector (the same golden spec/rules validates). ---


@pytest.mark.parametrize("case", _load("quantile.json"), ids=lambda c: c["name"])
def test_quantile_vectors(case: dict[str, Any]) -> None:
    values = [N.parse_rational(v) for v in case["values"]]
    assert (
        str(N.nearest_rank_quantile(values, N.parse_rational(case["q"])))
        == case["expected"]
    )


@pytest.mark.parametrize("case", _load("compare.json"), ids=lambda c: c["name"])
def test_compare_vectors(case: dict[str, Any]) -> None:
    lhs = Fraction(case["numerator"], case["denominator"])
    assert (
        N.compare(lhs, case["op"], N.parse_rational(case["threshold"]))
        is case["expected"]
    )


@pytest.mark.parametrize("case", _load("clopper-pearson.json"), ids=lambda c: c["name"])
def test_clopper_pearson_vectors(case: dict[str, Any]) -> None:
    ci = N.clopper_pearson(case["k"], case["n"], Decimal(case["alpha"]))
    assert str(N.round_presentation(ci.lower)) == case["lower"]
    assert str(N.round_presentation(ci.upper)) == case["upper"]


@pytest.mark.parametrize("case", _load("incomplete-beta.json"), ids=lambda c: c["name"])
def test_incomplete_beta_vectors(case: dict[str, Any]) -> None:
    got = N.regularised_incomplete_beta(Decimal(case["x"]), case["a"], case["b"])
    assert str(N.round_presentation(got)) == case["expected"]


@pytest.mark.parametrize("case", _load("beta-quantile.json"), ids=lambda c: c["name"])
def test_beta_quantile_vectors(case: dict[str, Any]) -> None:
    got = N.beta_quantile(case["a"], case["b"], Decimal(case["p"]))
    assert str(N.round_presentation(got)) == case["expected"]


# --- Exact-arithmetic properties and boundaries. ---


def test_parse_rational_is_exact() -> None:
    assert N.parse_rational("0.10") == Fraction(1, 10)
    assert N.parse_rational("15000") == Fraction(15000)
    assert N.parse_rational("-3.5") == Fraction(-7, 2)


def test_parse_rational_rejects_scientific_notation() -> None:
    with pytest.raises(N.NumericsError):
        N.parse_rational("1e-12")


def test_quantile_empty_population_errors() -> None:
    with pytest.raises(N.NumericsError):
        N.nearest_rank_quantile([], N.parse_rational("0.5"))


def test_quantile_clamps_below_and_above() -> None:
    values = [Fraction(1), Fraction(2), Fraction(3)]
    assert N.nearest_rank_quantile(values, Fraction(0)) == Fraction(1)  # clamps k to 1
    assert N.nearest_rank_quantile(values, Fraction(1)) == Fraction(3)  # k == N


def test_compare_unknown_operator_errors() -> None:
    with pytest.raises(N.NumericsError):
        N.compare(Fraction(1, 2), "~=", Fraction(1, 2))


def test_incomplete_beta_endpoints() -> None:
    assert N.regularised_incomplete_beta(Decimal(0), 3, 4) == Decimal(0)
    assert N.regularised_incomplete_beta(Decimal(1), 3, 4) == Decimal(1)


def test_incomplete_beta_uniform_is_identity() -> None:
    # Beta(1, 1) is uniform, so I_x(1, 1) == x.
    assert N.round_presentation(
        N.regularised_incomplete_beta(Decimal("0.37"), 1, 1)
    ) == Decimal("0.3700")


def test_incomplete_beta_rejects_nonpositive_parameters() -> None:
    with pytest.raises(N.NumericsError):
        N.regularised_incomplete_beta(Decimal("0.5"), 0, 3)


def test_beta_quantile_out_of_range_probability_errors() -> None:
    with pytest.raises(N.NumericsError):
        N.beta_quantile(2, 3, Decimal("1.5"))


def test_clopper_pearson_degenerate_bounds() -> None:
    assert N.clopper_pearson(0, 12, Decimal("0.05")).lower == Decimal(0)
    assert N.clopper_pearson(12, 12, Decimal("0.05")).upper == Decimal(1)


def test_clopper_pearson_rejects_bad_counts() -> None:
    with pytest.raises(N.NumericsError):
        N.clopper_pearson(5, 3, Decimal("0.05"))


def test_presentation_rounding_is_half_even() -> None:
    assert N.round_presentation(Decimal("0.12345")) == Decimal(
        "0.1234"
    )  # 4 is even, ties down
    assert N.round_presentation(Decimal("0.12355")) == Decimal(
        "0.1236"
    )  # 5 is odd, ties up


def test_rational_presentation_rounds_a_fraction() -> None:
    assert N.rational_to_presentation(Fraction(1, 3)) == Decimal("0.3333")
    assert N.rational_to_presentation(Fraction(2, 3)) == Decimal("0.6667")


def test_kernel_is_deterministic() -> None:
    # Re-evaluation yields an identical value: no floating-point accumulation or library default.
    first = N.clopper_pearson(7, 25, Decimal("0.05"))
    second = N.clopper_pearson(7, 25, Decimal("0.05"))
    assert first == second
