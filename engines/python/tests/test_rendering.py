"""Tests for report-rendering hardening and the message-key catalogue (SPEC §9.3, item 4.10)."""

from __future__ import annotations

import json
from pathlib import Path

from agentce import messages
from agentce.assertions import Assertion, EvidencePointer, aggregate
from agentce.report import render_report_html, render_report_md, write_report


def _assertions(subject: str = "s") -> list[Assertion]:
    return [
        Assertion(
            control="REC-01",
            control_version="2026.09",
            subject=subject,
            outcome="conformant",
            rung=2,
            mode="automated",
            window=("2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"),
            population=(1, 0),
            evidence=[
                EvidencePointer("agentce:event/x", "sha256:ab", "enforcement_point")
            ],
        )
    ]


def test_catalogue_falls_back_to_english() -> None:
    de = messages.catalogue("de")
    assert de["report.title"] == "AgentCE-Konformitätsbericht"  # translated
    assert de["outcome.conformant"] == "conformant"  # falls back to en
    assert (
        messages.catalogue("xx")["report.title"]
        == messages.catalogue("en")["report.title"]
    )


def test_html_is_self_contained_with_csp_and_landmarks() -> None:
    page = render_report_html(_assertions(), aggregate(_assertions()))
    assert "Content-Security-Policy" in page
    assert "http://" not in page and "https://" not in page
    assert page.count("<h1") == 1 and "<main>" in page and 'lang="en"' in page


def test_html_escapes_evidence_derived_strings() -> None:
    page = render_report_html(
        _assertions("s<script>alert(1)</script>"), aggregate(_assertions())
    )
    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;" in page


def test_markdown_translation_changes_text() -> None:
    counts = aggregate(_assertions())
    assert render_report_md(_assertions(), counts, language="en") != render_report_md(
        _assertions(), counts, language="de"
    )


def test_report_language_recorded_and_assertions_stable(tmp_path: Path) -> None:
    en, de = tmp_path / "en", tmp_path / "de"
    for out, language in ((en, "en"), (de, "de")):
        write_report(
            out,
            _assertions(),
            bundle_digest="sha256:0",
            catalogs=["eu-ai-act@2026.09"],
            report_language=language,
        )
    manifest = json.loads((de / "manifest.json").read_text())
    assert manifest["run"]["report_language"] == "de"
    assert (en / "assertions.json").read_bytes() == (
        de / "assertions.json"
    ).read_bytes()
    assert (en / "report.md").read_bytes() != (de / "report.md").read_bytes()
