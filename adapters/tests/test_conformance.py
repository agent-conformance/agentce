"""Unit tests for the adapter-conformance orchestrator (SPEC 11.5, 12.3)."""

from __future__ import annotations

from pathlib import Path

import conformance

ROOT = Path(__file__).resolve().parent.parent


def test_adapter_dirs_finds_every_shipped_adapter() -> None:
    names = {d.name for d in conformance.adapter_dirs(ROOT)}
    assert names == {
        "identity",
        "mcp-gateway",
        "otel-genai",
        "oversight",
        "policy-engines",
        "supply-chain",
    }


def test_aggregate_sums_and_requires_all_round_trip() -> None:
    report = conformance.aggregate(
        [
            {"adapter": "a", "total": 3, "identical": 3, "round_trip": True},
            {"adapter": "b", "total": 2, "identical": 2, "round_trip": True},
        ]
    )
    assert report["total"] == 5
    assert report["identical"] == 5
    assert report["round_trip"] is True
    assert report["adapters"] == ["a", "b"]


def test_aggregate_is_not_round_trip_if_any_adapter_fails() -> None:
    report = conformance.aggregate(
        [
            {"adapter": "a", "total": 3, "identical": 3, "round_trip": True},
            {"adapter": "b", "total": 2, "identical": 1, "round_trip": False},
        ]
    )
    assert report["identical"] == 4
    assert report["total"] == 5
    assert report["round_trip"] is False


def test_aggregate_of_nothing_is_not_round_trip() -> None:
    report = conformance.aggregate([])
    assert report["round_trip"] is False
    assert report["total"] == 0
