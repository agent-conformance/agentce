"""The assertion model, six-outcome aggregation, and the DC-5 evidence-pointer rule."""

from __future__ import annotations

import pytest

from agentce.assertions import (
    OUTCOMES,
    Assertion,
    EvidencePointer,
    aggregate,
    check_dc5,
)
from agentce.errors import AgentceError

_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
_EVIDENCE = EvidencePointer(
    "agentce:event/x", "sha256:" + "0" * 64, "enforcement_point"
)


def _assertion(
    outcome: str, evidence: list[EvidencePointer] | None = None
) -> Assertion:
    return Assertion(
        control="OVS-03",
        control_version="2026.09",
        subject="spiffe://corp/agents/a",
        outcome=outcome,
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=(3, 1),
        severity="high",
        family="OVS",
        evidence=evidence or [],
    )


def test_aggregate_lists_all_six_outcomes() -> None:
    counts = aggregate(
        [_assertion("conformant", [_EVIDENCE]), _assertion("conformant", [_EVIDENCE])]
    )
    assert set(counts) == set(OUTCOMES)
    assert counts["conformant"] == 2
    assert counts["not_assessed"] == 0


def test_to_json_omits_empty_optional_fields() -> None:
    record = _assertion("not_applicable").to_json()
    assert record["control"] == "OVS-03"
    assert record["population"] == {"applicable": 3, "failed": 1}
    assert "evidence" not in record
    assert "violations" not in record


def test_dc5_passes_with_evidence() -> None:
    check_dc5([_assertion("conformant", [_EVIDENCE])])


def test_dc5_raises_without_evidence() -> None:
    with pytest.raises(AgentceError) as excinfo:
        check_dc5([_assertion("non-conformant", [])])
    assert excinfo.value.key == "report.missing_evidence_pointer"


def test_dc5_exempts_non_supporting_outcomes() -> None:
    check_dc5(
        [
            _assertion("not_applicable"),
            _assertion("not_assessed"),
            _assertion("insufficient_evidence"),
        ]
    )
