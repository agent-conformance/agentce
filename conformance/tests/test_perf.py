"""The performance harness and P3.7 gate (SPEC §8.6): a real throughput measurement and the ADR gate."""

from __future__ import annotations

import pytest

import perf


def test_measure_reports_throughput_and_extrapolation() -> None:
    metrics = perf.measure(ingest_events=2000, assess_subjects=20)
    assert metrics["ingest_events_per_second"] > 0
    assert metrics["assess_seconds_per_subject"] > 0
    assert metrics["target_events"] == 50_000_000
    assert metrics["estimated_total_seconds"] > 0
    assert isinstance(metrics["within_targets"], bool)


def test_gate_passes_via_adr() -> None:
    # No recorded in-target run is committed, so the gate passes on the performance ADR.
    passed, reason = perf.check()
    assert passed is True
    assert "ADR" in reason


def test_main_check_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = perf.main(["--check"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PERF RECORDED" in out
    # P3.7 requires the check to print which path it took.
    assert "ADR" in out or "recorded" in out
