"""The held-out/adversarial gate over the full corpus (SPEC §11.6, P3.5): recall 1.0 and exact match."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import corpus.generator.generate as generate
import held_out
from corpus.held_out import compute_held_out


@pytest.fixture(scope="module")
def full_corpus(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    original = generate.FILLER_EVENTS
    generate.FILLER_EVENTS = 30  # a fast, representative full corpus for the test run
    try:
        out = tmp_path_factory.mktemp("full-corpus")
        generate.build_corpus(out, "full")
        yield out
    finally:
        generate.FILLER_EVENTS = original


def test_gate_reports_recall_and_match(
    full_corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = held_out.main(["--corpus", str(full_corpus), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"heldout_recall": 1.0' in out
    assert '"adversarial_match": true' in out


def test_metric_meets_p3_5(full_corpus: Path) -> None:
    metrics = compute_held_out(full_corpus)
    assert metrics["heldout_recall"] >= 0.95
    assert metrics["adversarial_match"] is True
    # A degenerate gate (no held-out faults or no adversarial controls) would be meaningless.
    assert metrics["heldout_seeded"] > 0
    assert metrics["adversarial_controls"] > 0
    assert metrics["heldout_projects"] == 9
    assert metrics["adversarial_projects"] == 9
