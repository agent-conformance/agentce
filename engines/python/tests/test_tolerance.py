"""Population tolerance decisions in the structural evaluator (SPEC §9.2)."""

from __future__ import annotations

from agentce.structural import within_tolerance


def test_count_tolerance_default_is_zero() -> None:
    # With no max declared the tolerance is zero: one failure is out of tolerance.
    assert within_tolerance(10, 0, {}) is True
    assert within_tolerance(10, 1, {}) is False


def test_count_tolerance_boundary() -> None:
    tol = {"kind": "count", "max": 2}
    assert within_tolerance(10, 2, tol) is True  # exactly at the limit is within
    assert within_tolerance(10, 3, tol) is False  # one over is out


def test_ratio_tolerance_boundary() -> None:
    tol = {"kind": "ratio", "max": "0.1"}
    assert within_tolerance(10, 1, tol) is True  # 1/10 == 0.1, at the limit
    assert within_tolerance(10, 2, tol) is False  # 2/10 > 0.1


def test_ratio_and_count_paths_differ() -> None:
    # A ratio tolerance and a count tolerance disagree on the same population, so the branch that
    # selects between them matters: 3/10 is within a 0.5 ratio but 3 exceeds a count max of 2.
    assert within_tolerance(10, 3, {"kind": "ratio", "max": "0.5"}) is True
    assert within_tolerance(10, 3, {"kind": "count", "max": 2}) is False


def test_ratio_with_no_applicable_population_uses_count_path() -> None:
    # The ratio branch only applies when there is an applicable population; with none it is a count
    # decision, and zero failures out of zero is within any tolerance.
    assert within_tolerance(0, 0, {"kind": "ratio", "max": "0"}) is True
