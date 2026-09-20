"""Validate the numerics vectors against the reference engine (SPEC §7.2, §8.2).

The JSON files in ``cases/`` are the cross-language golden for ``spec/rules/numerics.md``: every
conforming engine (Python here, TypeScript in ``engines/typescript`` from item 3.4) must reproduce
each expected value to the last published digit. This test runs the reference engine's exact
numerics kernel over them; a single differing digit fails.
"""

from __future__ import annotations

import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from agentce import numerics as N
from agentce.canonical import CanonicalizationError, canonical_string
from agentce.structural import compare_literals

CASES = Path(__file__).parent / "cases"


def _load(name: str) -> list[dict[str, Any]]:
    return list(json.loads((CASES / name).read_text(encoding="utf-8"))["cases"])


@pytest.mark.parametrize("case", _load("quantile.json"), ids=lambda c: c["name"])
def test_quantile(case: dict[str, Any]) -> None:
    values = [N.parse_rational(v) for v in case["values"]]
    got = N.nearest_rank_quantile(values, N.parse_rational(case["q"]))
    assert str(got) == case["expected"]


@pytest.mark.parametrize("case", _load("compare.json"), ids=lambda c: c["name"])
def test_compare(case: dict[str, Any]) -> None:
    lhs = Fraction(case["numerator"], case["denominator"])
    got = N.compare(lhs, case["op"], N.parse_rational(case["threshold"]))
    assert got is case["expected"]


@pytest.mark.parametrize("case", _load("clopper-pearson.json"), ids=lambda c: c["name"])
def test_clopper_pearson(case: dict[str, Any]) -> None:
    interval = N.clopper_pearson(case["k"], case["n"], Decimal(case["alpha"]))
    assert str(N.round_presentation(interval.lower)) == case["lower"]
    assert str(N.round_presentation(interval.upper)) == case["upper"]


@pytest.mark.parametrize("case", _load("incomplete-beta.json"), ids=lambda c: c["name"])
def test_incomplete_beta(case: dict[str, Any]) -> None:
    got = N.regularised_incomplete_beta(Decimal(case["x"]), case["a"], case["b"])
    assert str(N.round_presentation(got)) == case["expected"]


@pytest.mark.parametrize("case", _load("beta-quantile.json"), ids=lambda c: c["name"])
def test_beta_quantile(case: dict[str, Any]) -> None:
    got = N.beta_quantile(case["a"], case["b"], Decimal(case["p"]))
    assert str(N.round_presentation(got)) == case["expected"]


@pytest.mark.parametrize("case", _load("edge-comparison.json"), ids=lambda c: c["name"])
def test_literal_order(case: dict[str, Any]) -> None:
    order = compare_literals(case["lhs"], case["rhs"])
    got = "incomparable" if order is None else ("lt", "eq", "gt")[order + 1]
    assert got == case["expected"]


@pytest.mark.parametrize("case", _load("edge-canonical-numbers.json"), ids=lambda c: c["name"])
def test_canonical_token(case: dict[str, Any]) -> None:
    try:
        got = "canonical:" + canonical_string(json.loads(case["input"]))
    except CanonicalizationError as error:
        got = f"error:{error.reason}"
    assert got == case["expected"]
