"""The run verdict: a categorical state, the six-outcome tally, and the top gaps, on every surface."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from agentce import cli, messages, verdict
from agentce.assertions import OUTCOMES, Assertion, EvidencePointer, aggregate
from agentce.report import render_report_html, render_report_md


def _assertion(control: str, outcome: str, subject: str = "s") -> Assertion:
    evidence = (
        [EvidencePointer("agentce:event/x", "sha256:ab", "enforcement_point")]
        if outcome in ("conformant", "non-conformant", "partial")
        else []
    )
    return Assertion(
        control=control,
        control_version="2026.09",
        subject=subject,
        outcome=outcome,
        rung=2,
        mode="automated",
        window=("2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"),
        population=(1, 1 if outcome == "non-conformant" else 0),
        evidence=evidence,
    )


def _run(*outcomes: str) -> list[Assertion]:
    return [_assertion(f"REC-{i:02d}", o) for i, o in enumerate(outcomes, start=1)]


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        (("conformant", "conformant", "not_applicable"), verdict.CONFORMANT),
        (("conformant", "insufficient_evidence"), verdict.INCOMPLETE),
        (("conformant", "not_assessed"), verdict.INCOMPLETE),
        (("conformant", "partial"), verdict.INCOMPLETE),
        (
            ("conformant", "non-conformant", "insufficient_evidence"),
            verdict.NON_CONFORMANT,
        ),
        (("non-conformant",), verdict.NON_CONFORMANT),
    ],
)
def test_verdict_state_follows_the_outcomes(
    outcomes: tuple[str, ...], expected: str
) -> None:
    assert verdict.summarize(_run(*outcomes))["verdict"] == expected


def test_summary_counts_are_the_report_aggregate() -> None:
    assertions = _run(
        "conformant", "conformant", "not_assessed", "insufficient_evidence"
    )
    summary = verdict.summarize(assertions)
    assert summary["counts"] == aggregate(assertions)
    assert list(summary["counts"]) == list(OUTCOMES)


def test_top_gaps_are_urgent_first_sorted_distinct_and_capped() -> None:
    assertions = [
        _assertion(f"DAT-{n:02d}", "insufficient_evidence", subject=s)
        for n in (9, 3, 1, 2, 7, 5, 4)
        for s in ("a", "b")
    ] + [_assertion("OVS-01", "non-conformant"), _assertion("DOC-01", "conformant")]
    gaps = verdict.summarize(assertions)["top_gaps"]
    assert [g["outcome"] for g in gaps] == ["non-conformant", "insufficient_evidence"]
    assert gaps[0] == {"outcome": "non-conformant", "controls": ["OVS-01"], "more": 0}
    assert gaps[1]["controls"] == ["DAT-01", "DAT-02", "DAT-03", "DAT-04", "DAT-05"]
    assert (
        gaps[1]["more"] == 2
    )  # seven distinct controls, two subjects each: distinct, not per row


def test_summary_does_not_depend_on_assertion_order() -> None:
    assertions = _run("conformant", "insufficient_evidence", "not_assessed", "partial")
    assert verdict.summarize(assertions) == verdict.summarize(
        list(reversed(assertions))
    )


def test_verdict_is_categorical_never_a_composite_score() -> None:
    summary = verdict.summarize(_run("conformant", "insufficient_evidence"))
    assert summary["verdict"] in {
        verdict.CONFORMANT,
        verdict.INCOMPLETE,
        verdict.NON_CONFORMANT,
    }
    cat = messages.catalogue("en")
    for state in ("non-conformant", "incomplete", "conformant"):
        assert not re.search(r"\d|%", cat[f"verdict.{state}"])
        assert not re.search(r"\d|%", cat[f"next.{state}"])


def test_report_md_leads_with_verdict_gaps_and_next_step() -> None:
    assertions = _run("conformant", "insufficient_evidence", "not_assessed")
    md = render_report_md(assertions, aggregate(assertions))
    lead = md.split("## Assertions")[0]
    assert (
        md.index("## Verdict")
        < md.index("## Outcome summary")
        < md.index("## Assertions")
    )
    assert "Incomplete" in lead
    assert "- insufficient evidence: REC-02" in lead
    assert "- not assessed: REC-03" in lead
    assert "Next step:" in lead


def test_report_md_with_no_gaps_says_so() -> None:
    assertions = _run("conformant", "conformant")
    md = render_report_md(assertions, aggregate(assertions))
    assert "Top gaps: none" in md
    assert "**Conformant" in md


def test_report_html_leads_with_the_verdict_and_escapes_control_ids() -> None:
    hostile = '<img src=x onerror="alert(1)">'
    assertions = [_assertion(hostile, "insufficient_evidence")]
    page = render_report_html(assertions, aggregate(assertions))
    assert '<h2 id="verdict">Verdict</h2>' in page
    assert page.index('id="verdict"') < page.index('id="summary"')
    assert "<img" not in page
    assert "&lt;img" in page


def test_translated_report_keeps_the_verdict_and_falls_back_for_missing_keys() -> None:
    assertions = _run("conformant", "insufficient_evidence")
    md = render_report_md(assertions, aggregate(assertions), language="de")
    assert (
        "## Verdict" in md
    )  # the partial de catalogue has no verdict keys yet: en fallback
    assert "## Ergebnisübersicht" in md


def _quickstart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *flags: str
) -> tuple[int, str]:
    code = cli.main(["quickstart", "--out", str(tmp_path / "out"), *flags])
    return code, capsys.readouterr().out


def test_quickstart_prints_verdict_tally_gaps_and_next_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, stdout = _quickstart(tmp_path, capsys)
    assert code == 0
    lines = stdout.splitlines()
    assert lines[0].startswith("Verdict: ")
    assert re.search(r"^Outcomes: conformant \d+, non-conformant \d+", stdout, re.M)
    assert "not assessed" in stdout and "insufficient evidence" in stdout
    assert "Top gaps:" in stdout and "Next step:" in stdout
    assert "See report.md in" in stdout
    assert lines[-1].startswith("quickstart complete:")


def test_quickstart_json_summary_equals_assertions_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, stdout = _quickstart(tmp_path, capsys, "--json")
    assert code == 0
    summary: dict[str, Any] = json.loads(stdout)["summary"]
    assertions = json.loads((tmp_path / "out" / "assertions.json").read_text("utf-8"))
    for outcome in OUTCOMES:
        assert summary["counts"][outcome] == sum(
            1 for a in assertions if a["outcome"] == outcome
        )
    assert summary["verdict"] in {"conformant", "incomplete", "non-conformant"}
    assert all(isinstance(v, int) for v in summary["counts"].values())


def test_quickstart_report_and_stdout_agree_on_the_tally(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, stdout = _quickstart(tmp_path, capsys)
    md = (tmp_path / "out" / "report.md").read_text("utf-8")
    for outcome, count in re.findall(r"^- ([a-z -]+): (\d+)$", md, re.M):
        assert f"{outcome} {count}" in stdout


def test_assess_prints_the_same_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce import bundled

    quickstart = bundled.quickstart_dir()
    code = cli.main(
        [
            "assess",
            "--bundle",
            str(quickstart / "evidence"),
            "--profile",
            str(quickstart / "applicability.yaml"),
            "--domain",
            str(quickstart / "domain.linkml.yaml"),
            "--catalog",
            "eu-ai-act@2026.09",
            "--catalog-dir",
            str(bundled.catalogs_dir() / "base" / "eu-ai-act"),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    stdout = capsys.readouterr().out
    assert code == 0
    assert stdout.startswith("Verdict: ")
    assert "Next step:" in stdout
    assert "assessed " in stdout
