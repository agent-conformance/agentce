"""Tests for report-rendering hardening and the message-key catalogue (SPEC §9.3, item 4.10)."""

from __future__ import annotations

import json
from pathlib import Path

from agentce import messages
from agentce.assertions import Assertion, EvidencePointer, aggregate
from agentce.catalog import Catalog, load_catalog
from agentce.report import render_report_html, render_report_md, write_report

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE_CATALOG_DIR = _REPO_ROOT / "spec/catalogs/base/eu-ai-act"
_BASE_CATALOG = Catalog(
    id="eu-ai-act",
    version="2026.09",
    directory=_BASE_CATALOG_DIR,
    controls=[],
    shapes={},
)
_LOADED_BASE_CATALOG = load_catalog(_BASE_CATALOG_DIR)


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


def _crosswalk_assertions() -> list[Assertion]:
    base = _assertions()[0]
    unverified = Assertion(
        **{
            **base.__dict__,
            "crosswalk": [
                {
                    "framework": "eu-ai-act",
                    "clause": "Art. 11 / Annex IV",
                    "verified": False,
                }
            ],
        }
    )
    verified = Assertion(
        **{
            **base.__dict__,
            "control": "DAT-01",
            "crosswalk": [
                {
                    "framework": "eu-ai-act",
                    "clause": "TEST-CLAUSE-VERIFIED-99",
                    "verified": True,
                }
            ],
        }
    )
    return [unverified, verified]


def test_crosswalk_citation_renders_unverified_label_only_when_not_verified() -> None:
    """SPEC §7.3: every rendering shows the clause citation, and labels it unverified exactly
    when its carried ``verified`` flag is not ``True`` -- never unconditionally on or off."""
    assertions = _crosswalk_assertions()
    counts = aggregate(assertions)
    md = render_report_md(assertions, counts)
    html = render_report_html(assertions, counts)
    for page in (md, html):
        assert "Art. 11 / Annex IV" in page
        assert "TEST-CLAUSE-VERIFIED-99" in page
        assert page.lower().count("unverified") == 1


def test_markdown_translation_changes_text() -> None:
    counts = aggregate(_assertions())
    assert render_report_md(_assertions(), counts, language="en") != render_report_md(
        _assertions(), counts, language="de"
    )


def _severity_findings() -> list[Assertion]:
    """DOC-01 (severity high, non-conformant with a violation) and DAT-01 (severity medium,
    conformant), each carrying real evidence -- so the enriched report has both a title/severity
    pair to order and a violation to surface."""
    return [
        Assertion(
            control="DAT-01",
            control_version="2026.09",
            subject="spiffe://corp/agents/a",
            outcome="conformant",
            rung=2,
            mode="automated",
            window=("2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"),
            population=(1, 0),
            evidence=[
                EvidencePointer("agentce:event/dat", "sha256:dat", "self_report")
            ],
        ),
        Assertion(
            control="DOC-01",
            control_version="2026.09",
            subject="spiffe://corp/agents/a",
            outcome="non-conformant",
            rung=2,
            mode="automated",
            window=("2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"),
            population=(1, 1),
            violations=[
                {
                    "focus": "agentce:node/UNDOCUMENTED-DECISION-Z9",
                    "path": "agentce:prop/documentedBy",
                    "constraint": "minCount",
                }
            ],
            evidence=[
                EvidencePointer("agentce:event/doc", "sha256:doc", "self_report")
            ],
        ),
    ]


def test_findings_grouped_by_severity_with_titles_and_remediation() -> None:
    """SPEC §9.3: with a resolved catalog, the report ranks a high-severity finding before a
    medium one by its control's real title -- never by alphabetical control id (DAT-01 < DOC-01) --
    and carries each control's remediation technique."""
    assertions = _severity_findings()
    counts = aggregate(assertions)
    md = render_report_md(assertions, counts, catalogs=[_LOADED_BASE_CATALOG])
    html_page = render_report_html(assertions, counts, catalogs=[_LOADED_BASE_CATALOG])
    for page in (md, html_page):
        assert "Operating components match the declared documentation" in page
        assert "Consequential decisions record the data they consumed" in page
        assert page.index(
            "Operating components match the declared documentation"
        ) < page.index("Consequential decisions record the data they consumed")
        assert "doc-evidence-at-source" in page
        assert "dat-evidence-at-source" in page


def test_evidence_and_violations_render_regardless_of_catalog() -> None:
    """Evidence pointers and violation focus nodes come from the assertion itself, so they render
    in both md and html whether or not a catalog was resolved for this render (SPEC §9.3, §9.4)."""
    assertions = _severity_findings()
    counts = aggregate(assertions)
    for catalogs in (None, [_LOADED_BASE_CATALOG]):
        md = render_report_md(assertions, counts, catalogs=catalogs)
        html_page = render_report_html(assertions, counts, catalogs=catalogs)
        for page in (md, html_page):
            assert "agentce:event/doc" in page
            assert "agentce:node/UNDOCUMENTED-DECISION-Z9" in page


def test_report_language_recorded_and_assertions_stable(tmp_path: Path) -> None:
    en, de = tmp_path / "en", tmp_path / "de"
    for out, language in ((en, "en"), (de, "de")):
        write_report(
            out,
            _assertions(),
            bundle_digest="sha256:0",
            catalogs=[_BASE_CATALOG],
            report_language=language,
        )
    manifest = json.loads((de / "manifest.json").read_text())
    assert manifest["run"]["report_language"] == "de"
    assert (en / "assertions.json").read_bytes() == (
        de / "assertions.json"
    ).read_bytes()
    assert (en / "report.md").read_bytes() != (de / "report.md").read_bytes()
