"""Corpus correctness against the reference engine (SPEC §11.3, §11.6).

The generator authors each project's ground truth to what the reference engine produces; these tests
prove it. They generate the corpus (with a small filler for speed), run every project end to end
through the engine via :func:`corpus.assess_one.assess_project`, and check that the assertions match
``expected/outcomes.json`` exactly — so the precision/recall gate sees every seeded fault detected
(recall 1.0) and no known-pass control flagged (false-positive rate 0.0). A few targeted checks cover
the integrity-tamper detection and the coverage shortfall the corresponding variants seed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

import corpus.generator.generate as generate
from corpus.assess_one import assess_project


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    original = generate.FILLER_EVENTS
    generate.FILLER_EVENTS = (
        40  # a fast, representative high-volume bundle for the test run
    )
    try:
        out = tmp_path_factory.mktemp("corpus-v1")
        generate.build_corpus(out, "v1")
        yield out
    finally:
        generate.FILLER_EVENTS = original


def _manifest(corpus_dir: Path) -> dict:
    return json.loads((corpus_dir / "corpus-manifest.json").read_text())


def _assess(corpus_dir: Path, project_id: str, tmp_path: Path) -> Path:
    out = tmp_path / "report"
    rc = assess_project(corpus_dir, project_id, out)
    assert rc == 0, f"assess_one on {project_id} exited {rc}"
    return out


def _outcomes(report: Path, subject: str) -> dict[str, str]:
    assertions = json.loads((report / "assertions.json").read_text())
    return {a["control"]: a["outcome"] for a in assertions if a["subject"] == subject}


def _expected(corpus_dir: Path, project_id: str) -> dict[str, str]:
    payload = json.loads(
        (
            corpus_dir / "projects" / project_id / "expected" / "outcomes.json"
        ).read_text()
    )
    return {o["control"]: o["outcome"] for o in payload["outcomes"]}


def test_every_project_matches_authored_ground_truth(
    corpus_dir: Path, tmp_path: Path
) -> None:
    manifest = _manifest(corpus_dir)
    assert len(manifest["projects"]) == 30
    mismatches: list[str] = []
    for project in manifest["projects"]:
        report = _assess(
            corpus_dir, project["id"], tmp_path / project["id"].replace("/", "_")
        )
        got = _outcomes(report, project["subject"])
        want = _expected(corpus_dir, project["id"])
        if got != want:
            mismatches.append(f"{project['id']}: expected {want}, got {got}")
    assert not mismatches, "\n".join(mismatches)


def test_precision_recall_gate(corpus_dir: Path) -> None:
    # The detection/precision metric (SPEC §11.6), computed by the real gate function: recall over
    # seeded faults and false-positive rate over known-pass controls. Must be recall 1.0 and FPR 0.0.
    from corpus.precision_recall import compute_precision_recall

    metrics = compute_precision_recall(corpus_dir)
    assert metrics["seeded_faults"] > 0 and metrics["known_pass_controls"] > 0
    assert metrics["recall"] == 1.0
    assert metrics["fpr_rung2"] == 0.0


def test_tampered_stream_is_detected(corpus_dir: Path, tmp_path: Path) -> None:
    report = _assess(corpus_dir, "credit/langgraph/tampered", tmp_path)
    integrity = [
        json.loads(line)
        for line in (report / "integrity.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert any(r["status"] == "failed" for r in integrity)


def test_known_pass_integrity_is_clean(corpus_dir: Path, tmp_path: Path) -> None:
    report = _assess(corpus_dir, "credit/langgraph/known-pass", tmp_path)
    integrity = [
        json.loads(line)
        for line in (report / "integrity.jsonl").read_text().splitlines()
        if line.strip()
    ]
    clean = {"verified", "verified_weak"}
    assert integrity and all(r["status"] in clean for r in integrity)


def test_coverage_gap_is_below_threshold(corpus_dir: Path, tmp_path: Path) -> None:
    report = _assess(corpus_dir, "credit/langgraph/coverage-gap", tmp_path)
    coverage = json.loads((report / "coverage.json").read_text())
    subject = coverage["subjects"]["spiffe://corp/agents/credit-langgraph"]
    assert subject["event_types"]["ToolCall"]["status"] == "below_threshold"


def test_benign_events_are_quarantined(corpus_dir: Path, tmp_path: Path) -> None:
    report = _assess(corpus_dir, "credit/langgraph/known-pass", tmp_path)
    quarantine = [
        json.loads(line)
        for line in (report / "quarantine.jsonl").read_text().splitlines()
        if line.strip()
    ]
    reasons = {q["reason"] for q in quarantine}
    assert {"duplicate_id", "unknown_type"} <= reasons


@pytest.fixture(scope="module")
def full_corpus(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    original = generate.FILLER_EVENTS
    generate.FILLER_EVENTS = 40
    try:
        out = tmp_path_factory.mktemp("corpus-full")
        generate.build_corpus(out, "full")
        yield out
    finally:
        generate.FILLER_EVENTS = original


def test_full_new_project_types_match_engine(full_corpus: Path, tmp_path: Path) -> None:
    # The full set's novel project types — the two added variants and the multi-agent bundles — must
    # match their authored ground truth under the engine (the held-out and adversarial subsets are
    # proven by conformance/tests/test_held_out.py).
    targets = [
        "credit/langgraph/minor-only",
        "hiring/custom-loop/dual-clean",
        "multi-agent/credit/clean-and-faulty",
        "multi-agent/benefits/both-clean",
    ]
    mismatches: list[str] = []
    for pid in targets:
        report = _assess(full_corpus, pid, tmp_path / pid.replace("/", "_"))
        exp_payload = json.loads(
            (full_corpus / "projects" / pid / "expected" / "outcomes.json").read_text()
        )
        exp = {
            (o["subject"], o["control"]): o["outcome"] for o in exp_payload["outcomes"]
        }
        assertions = json.loads((report / "assertions.json").read_text())
        got = {
            (a["subject"], a["control"]): a["outcome"]
            for a in assertions
            if (a["subject"], a["control"]) in exp
        }
        if got != exp:
            mismatches.append(f"{pid}: expected {exp}, got {got}")
    assert not mismatches, "\n".join(mismatches)
