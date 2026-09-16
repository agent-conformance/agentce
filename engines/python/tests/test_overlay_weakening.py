"""Tests for the overlay weakening lint (SPEC §7.3, item 4.2/P4.1)."""

from __future__ import annotations

from agentce.catalog import ControlSpec, overlay_weakens_base


def _control(cid: str, severity: str, tolerance: dict) -> ControlSpec:
    return ControlSpec(
        id=cid,
        version="2026.09",
        title="t",
        applies_to_roles=["both"],
        mode="automated",
        rung=2,
        severity=severity,
        min_source_class="self_report",
        minimum_evidence=[],
        shape_path=None,
        tolerance=tolerance,
        test_cases=[],
    )


_BASE = [_control("OVS-03", "high", {"kind": "count", "max": 0})]


def test_new_control_is_not_weakening() -> None:
    assert (
        overlay_weakens_base(
            _BASE, [_control("CND-01", "high", {"kind": "count", "max": 0})]
        )
        == []
    )


def test_lower_severity_is_weakening() -> None:
    problems = overlay_weakens_base(
        _BASE, [_control("OVS-03", "medium", {"kind": "count", "max": 0})]
    )
    assert any("severity" in p for p in problems)


def test_looser_tolerance_is_weakening() -> None:
    problems = overlay_weakens_base(
        _BASE, [_control("OVS-03", "high", {"kind": "ratio", "max": "0.1"})]
    )
    assert any("tolerance" in p for p in problems)


def test_tightening_is_allowed() -> None:
    base = [_control("OVS-03", "medium", {"kind": "ratio", "max": "0.01"})]
    assert (
        overlay_weakens_base(
            base, [_control("OVS-03", "high", {"kind": "count", "max": 0})]
        )
        == []
    )
