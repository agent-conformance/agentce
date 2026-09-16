"""The rung-3 statistical evaluator: population, statistics, thresholds, sampling (SPEC §7.2)."""

from __future__ import annotations

from typing import Any

import pytest

from agentce import statistical
from agentce.domain import DomainBinding
from agentce.numerics import NumericsError

DOMAIN = DomainBinding.from_dict(
    {
        "decision_types": [
            {
                "id": "credit:LoanApproval",
                "subclass_of": "credit:CreditDecision",
                "consequential": True,
            },
            {"id": "credit:CreditDecision", "subclass_of": "agentce:Decision"},
            {"id": "credit:AddressChange", "subclass_of": "agentce:Decision"},
        ]
    }
)


def approval(
    eid: str, latency: int, actor: str, dtype: str, failed: bool = False
) -> dict[str, Any]:
    return {
        "id": eid,
        "data": {
            "@type": "ApprovalDecided",
            "latency_ms": latency,
            "actor": {"id": actor},
            "refs": {"decision": {"decision_type": dtype}},
            "failed": failed,
        },
    }


# --- Population selection. ---


def test_population_filters_by_event_type() -> None:
    events = [
        approval("e1", 1, "a", "credit:LoanApproval"),
        {"id": "x", "data": {"@type": "ToolCall"}},
    ]
    rows = statistical.select_population(events, {"event": "ApprovalDecided"}, DOMAIN)
    assert [r["id"] for r in rows] == ["e1"]


def test_population_where_equality() -> None:
    events = [
        approval("e1", 1, "a", "credit:LoanApproval"),
        approval("e2", 1, "b", "credit:AddressChange"),
    ]
    pop = {
        "event": "ApprovalDecided",
        "where": {"refs.decision.decision_type": "credit:LoanApproval"},
    }
    assert [r["id"] for r in statistical.select_population(events, pop, DOMAIN)] == [
        "e1"
    ]


def test_population_where_subclass_of_uses_domain_hierarchy() -> None:
    events = [
        approval("e1", 1, "a", "credit:LoanApproval"),  # subclass of CreditDecision
        approval("e2", 1, "b", "credit:AddressChange"),  # not
    ]
    pop = {
        "event": "ApprovalDecided",
        "where": {"refs.decision.decision_type": "subClassOf credit:CreditDecision"},
    }
    assert [r["id"] for r in statistical.select_population(events, pop, DOMAIN)] == [
        "e1"
    ]


def test_population_where_consequential_marker() -> None:
    events = [
        approval("e1", 1, "a", "credit:LoanApproval"),  # declared consequential
        approval("e2", 1, "b", "credit:AddressChange"),
    ]
    pop = {
        "event": "ApprovalDecided",
        "where": {
            "refs.decision.decision_type": "subClassOf agentce:ConsequentialDecision"
        },
    }
    assert [r["id"] for r in statistical.select_population(events, pop, DOMAIN)] == [
        "e1"
    ]


def test_field_value_missing_segment_is_none() -> None:
    assert (
        statistical.field_value(
            approval("e1", 1, "a", "credit:LoanApproval"), "refs.missing.x"
        )
        is None
    )


# --- Deterministic sampling. ---


def test_census_returns_all() -> None:
    rows = [approval(f"e{i}", i, "a", "credit:LoanApproval") for i in range(5)]
    assert statistical.apply_sampling(rows, {"kind": "census"}) == rows


def test_hash_ordered_sample_is_deterministic_and_order_independent() -> None:
    rows = [approval(f"e{i}", i, "a", "credit:LoanApproval") for i in range(20)]
    spec = {"kind": "hash_ordered_sample", "n": 5}
    forward = statistical.apply_sampling(rows, spec)
    reverse = statistical.apply_sampling(list(reversed(rows)), spec)
    assert [r["id"] for r in forward] == [r["id"] for r in reverse]
    assert len(forward) == 5


def test_hash_ordered_sample_larger_than_population() -> None:
    rows = [approval(f"e{i}", i, "a", "credit:LoanApproval") for i in range(3)]
    assert (
        len(statistical.apply_sampling(rows, {"kind": "hash_ordered_sample", "n": 10}))
        == 3
    )


# --- Statistics. ---


def _rows(latencies: list[int]) -> list[dict[str, Any]]:
    return [
        approval(f"e{i}", v, "a", "credit:LoanApproval")
        for i, v in enumerate(latencies)
    ]


def test_count_sum_mean() -> None:
    rows = _rows([10, 20, 30])
    assert statistical.compute_statistic(rows, {"kind": "count"}).value == 3
    assert (
        statistical.compute_statistic(
            rows, {"kind": "sum", "field": "latency_ms"}
        ).value
        == 60
    )
    assert (
        statistical.compute_statistic(
            rows, {"kind": "mean", "field": "latency_ms"}
        ).value
        == 20
    )


def test_quantile_statistic() -> None:
    rows = _rows([10000, 12000, 20000, 25000, 30000])
    stat = statistical.compute_statistic(
        rows, {"kind": "quantile", "q": "0.10", "field": "latency_ms"}
    )
    assert stat.value == 10000  # k = ceil(0.10 * 5) = 1


def test_proportion_carries_clopper_pearson_interval() -> None:
    rows = [
        approval(f"e{i}", 1, "a", "credit:LoanApproval", failed=(i < 2))
        for i in range(10)
    ]
    stat = statistical.compute_statistic(
        rows, {"kind": "proportion", "field": "failed"}
    )
    assert stat.value.numerator == 1 and stat.value.denominator == 5  # 2/10
    assert stat.interval is not None
    assert stat.to_json()["interval"] == {"lower": "0.0252", "upper": "0.5561"}


def test_ratio_statistic() -> None:
    rows = [
        {"id": "a", "data": {"@type": "E", "num": True, "den": True}},
        {"id": "b", "data": {"@type": "E", "num": False, "den": True}},
    ]
    stat = statistical.compute_statistic(
        rows, {"kind": "ratio", "numerator": "num", "denominator": "den"}
    )
    assert stat.value.numerator == 1 and stat.value.denominator == 2


# --- End-to-end metric outcomes. ---

QUANTILE_METRIC: dict[str, Any] = {
    "id": "latency-q10",
    "population": {"event": "ApprovalDecided"},
    "statistic": {"kind": "quantile", "q": "0.10", "field": "latency_ms"},
    "threshold": {"op": ">=", "value": 15000},
    "min_population": 3,
    "outcome_map": {"pass": "conformant", "fail": "non-conformant"},
}


def test_metric_passes_when_threshold_met() -> None:
    events = _rows([16000, 17000, 18000, 19000, 20000])
    result = statistical.evaluate_metric(events, QUANTILE_METRIC, DOMAIN)
    assert result.outcome == "conformant"


def test_metric_fails_when_threshold_missed() -> None:
    events = _rows([10000, 12000, 20000, 25000, 30000])  # q0.10 = 10000 < 15000
    result = statistical.evaluate_metric(events, QUANTILE_METRIC, DOMAIN)
    assert result.outcome == "non-conformant"


def test_metric_not_assessed_below_min_population() -> None:
    metric = {**QUANTILE_METRIC, "min_population": 10}
    result = statistical.evaluate_metric(_rows([16000, 17000]), metric, DOMAIN)
    assert result.outcome == "not_assessed"
    assert result.groups[0].reason == "below_min_population"


def test_group_by_fails_if_any_group_fails() -> None:
    events = [
        approval("p1", 20000, "good", "credit:LoanApproval"),
        approval("p2", 21000, "good", "credit:LoanApproval"),
        approval("p3", 22000, "good", "credit:LoanApproval"),
        approval("f1", 1000, "bad", "credit:LoanApproval"),
        approval("f2", 1200, "bad", "credit:LoanApproval"),
        approval("f3", 1400, "bad", "credit:LoanApproval"),
    ]
    metric = {**QUANTILE_METRIC, "group_by": ["actor.id"]}
    result = statistical.evaluate_metric(events, metric, DOMAIN)
    assert result.outcome == "non-conformant"
    outcomes = {tuple(g.group.items()): g.outcome for g in result.groups}
    assert outcomes[(("actor.id", "good"),)] == "conformant"
    assert outcomes[(("actor.id", "bad"),)] == "non-conformant"


def test_metric_is_order_independent() -> None:
    events = _rows([10000, 12000, 20000, 25000, 30000])
    forward = statistical.evaluate_metric(events, QUANTILE_METRIC, DOMAIN).to_json()
    reverse = statistical.evaluate_metric(
        list(reversed(events)), QUANTILE_METRIC, DOMAIN
    ).to_json()
    assert forward == reverse


# --- Error and edge branches. ---


def test_as_fraction_rejects_boolean_and_non_numeric() -> None:
    boolean = [{"id": "a", "data": {"@type": "E", "v": True}}]
    with pytest.raises(NumericsError):
        statistical.compute_statistic(boolean, {"kind": "sum", "field": "v"})
    mapping = [{"id": "a", "data": {"@type": "E", "v": {"x": 1}}}]
    with pytest.raises(NumericsError):
        statistical.compute_statistic(mapping, {"kind": "sum", "field": "v"})


def test_as_fraction_reads_declared_scale_decimal_string() -> None:
    rows = [
        {"id": "a", "data": {"@type": "E", "v": "1.5"}},
        {"id": "b", "data": {"@type": "E", "v": "2.5"}},
    ]
    assert statistical.compute_statistic(rows, {"kind": "sum", "field": "v"}).value == 4


def test_subclass_of_on_non_class_value_does_not_match() -> None:
    events = [approval("e1", 1, "a", "credit:LoanApproval")]
    pop = {
        "event": "ApprovalDecided",
        "where": {"latency_ms": "subClassOf credit:CreditDecision"},
    }
    assert statistical.select_population(events, pop, DOMAIN) == []


def test_ancestors_handles_a_cyclic_domain() -> None:
    cyclic = DomainBinding.from_dict(
        {
            "decision_types": [
                {"id": "A", "subclass_of": "B"},
                {"id": "B", "subclass_of": "A"},
            ]
        }
    )
    events = [{"id": "e", "data": {"@type": "E", "t": "A"}}]
    pop = {"event": "E", "where": {"t": "subClassOf B"}}
    assert [r["id"] for r in statistical.select_population(events, pop, cyclic)] == [
        "e"
    ]


def test_unknown_sampling_kind_errors() -> None:
    with pytest.raises(NumericsError):
        statistical.apply_sampling([], {"kind": "stratified"})


def test_statistic_error_branches() -> None:
    with pytest.raises(NumericsError):
        statistical.compute_statistic([], {"kind": "mean", "field": "latency_ms"})
    with pytest.raises(NumericsError):
        statistical.compute_statistic([], {"kind": "proportion", "field": "failed"})
    empty_denominator = [{"id": "a", "data": {"@type": "E", "den": False}}]
    with pytest.raises(NumericsError):
        statistical.compute_statistic(
            empty_denominator,
            {"kind": "ratio", "numerator": "num", "denominator": "den"},
        )
    with pytest.raises(NumericsError):
        statistical.compute_statistic([], {"kind": "median"})


def test_all_groups_below_min_population_aggregate_not_assessed() -> None:
    events = [
        approval("g1", 1, "x", "credit:LoanApproval"),
        approval("g2", 1, "y", "credit:LoanApproval"),
    ]
    metric = {**QUANTILE_METRIC, "group_by": ["actor.id"], "min_population": 5}
    assert statistical.evaluate_metric(events, metric, DOMAIN).outcome == "not_assessed"


def test_empty_population_is_not_assessed() -> None:
    assert (
        statistical.evaluate_metric([], QUANTILE_METRIC, DOMAIN).outcome
        == "not_assessed"
    )
