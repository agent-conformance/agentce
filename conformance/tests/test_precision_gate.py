"""The precision/recall gate over the corpus (SPEC §11.6): recall 1.0, FPR 0.0, and GATE PASS."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import corpus.generator.generate as generate
import precision_gate
from corpus.precision_recall import compute_precision_recall


@pytest.fixture(scope="module")
def small_corpus(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    original = generate.FILLER_EVENTS
    generate.FILLER_EVENTS = 30  # a fast, representative corpus for the test run
    try:
        out = tmp_path_factory.mktemp("corpus")
        generate.build_corpus(out, "v1")
        yield out
    finally:
        generate.FILLER_EVENTS = original


def test_gate_passes(small_corpus: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = precision_gate.main(["--corpus", str(small_corpus)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "GATE PASS" in out
    assert "recall=1.0" in out and "fpr_rung2=0.0" in out


def test_metric_has_positives_and_negatives(small_corpus: Path) -> None:
    metrics = compute_precision_recall(small_corpus)
    assert metrics["recall"] == 1.0
    assert metrics["fpr_rung2"] == 0.0
    # A degenerate gate (no seeded faults or no known-pass controls) would be meaningless.
    assert metrics["seeded_faults"] > 0
    assert metrics["known_pass_controls"] > 0


def test_family_filter_scores_one_family(small_corpus: Path) -> None:
    metrics = compute_precision_recall(small_corpus, family="REC")
    # REC-04 is conformant on known-pass and non-conformant on known-fail: a real positive and
    # negative, still perfectly scored.
    assert metrics["recall"] == 1.0
    assert metrics["fpr_rung2"] == 0.0
    assert metrics["seeded_faults"] > 0


def test_scope_is_stated_and_accurate_in_the_readme(small_corpus: Path) -> None:
    """The README's scope claim must name exactly what the gate scores today (SPEC §11.6): a reader
    of "recall 1.0 / FPR 0.0" must not read broader coverage than the corpus actually seeds."""
    metrics = compute_precision_recall(small_corpus)
    scored = metrics["scored_controls"]
    assert scored, "a degenerate gate that scores nothing would be meaningless"
    automated, total = precision_gate.automated_control_count()
    readme = (
        (Path(precision_gate.__file__).resolve().parent / "README.md")
        .read_text(encoding="utf-8")
        .lower()
    )
    n = len(scored)
    patterns = [
        f"{n} of {automated}",
        f"{n}/{automated}",
        f"{n} of the {automated}",
        f"{n} of {total}",
        f"{n}/{total}",
        f"{n} of the {total}",
    ]
    assert any(p in readme for p in patterns), (
        f"README does not state the gate's real scope ({n} of {automated} automated, "
        f"{total} total): {readme}"
    )
