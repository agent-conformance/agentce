"""The report generator: schema-valid artifacts, DC-5 refusal, SARIF/OSCAL, and supersession."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentce.assertions import Assertion, EvidencePointer
from agentce.errors import AgentceError
from agentce.report import render_oscal, render_sarif, validate_report, write_report

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
_EVIDENCE = EvidencePointer(
    "agentce:event/x", "sha256:" + "0" * 64, "enforcement_point"
)


def _assertion(outcome: str) -> Assertion:
    return Assertion(
        control="OVS-03",
        control_version="2026.09",
        subject="spiffe://corp/agents/a",
        outcome=outcome,
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=(3, 1 if outcome == "non-conformant" else 0),
        evidence=[_EVIDENCE],
    )


def test_vendored_report_schemas_match_spec() -> None:
    for name in ("assertions", "manifest", "oscal-assessment-results", "results-sarif"):
        vendored = (
            _REPO_ROOT / "engines/python/agentce/data/schemas" / f"{name}.schema.json"
        ).read_text(encoding="utf-8")
        spec = (_REPO_ROOT / "spec/report" / f"{name}.schema.json").read_text(
            encoding="utf-8"
        )
        assert vendored == spec


def test_write_report_produces_valid_artifacts(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        [_assertion("conformant"), _assertion("non-conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=["eu-ai-act@2026.09"],
    )
    for name in (
        "assertions.json",
        "report.md",
        "report.html",
        "oscal-ar.json",
        "results.sarif",
        "manifest.json",
    ):
        assert (tmp_path / name).is_file()
    assert validate_report(tmp_path) == []


def test_empty_report_is_valid(tmp_path: Path) -> None:
    write_report(
        tmp_path, [], bundle_digest="sha256:" + "a" * 64, catalogs=["eu-ai-act@2026.09"]
    )
    assert json.loads((tmp_path / "assertions.json").read_text(encoding="utf-8")) == []
    assert validate_report(tmp_path) == []


def test_assertions_json_is_deterministic(tmp_path: Path) -> None:
    write_report(
        tmp_path / "a",
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=["c@1"],
    )
    write_report(
        tmp_path / "b",
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=["c@1"],
    )
    assert (tmp_path / "a" / "assertions.json").read_bytes() == (
        tmp_path / "b" / "assertions.json"
    ).read_bytes()


def test_dc5_aborts_before_writing(tmp_path: Path) -> None:
    bad = Assertion(
        "C", "1", "s", "conformant", 2, "automated", _WINDOW, (1, 0), evidence=[]
    )
    with pytest.raises(AgentceError) as excinfo:
        write_report(tmp_path, [bad], bundle_digest="sha256:" + "a" * 64, catalogs=[])
    assert excinfo.value.key == "report.missing_evidence_pointer"
    assert not (tmp_path / "assertions.json").exists()


def test_sarif_flags_non_conformant() -> None:
    sarif = render_sarif([_assertion("conformant"), _assertion("non-conformant")])
    assert sarif["version"] == "2.1.0"
    assert any(r["level"] == "error" for r in sarif["runs"][0]["results"])


def test_oscal_maps_outcomes() -> None:
    oscal = render_oscal([_assertion("conformant")])
    finding = oscal["assessment-results"]["results"][0]["findings"][0]
    assert finding["target"]["status"]["state"] == "satisfied"


def test_supersedes_recorded_in_manifest(tmp_path: Path) -> None:
    manifest = write_report(
        tmp_path,
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=["c@1"],
        supersedes=["sha256:" + "b" * 64],
    )
    assert manifest["supersedes"] == ["sha256:" + "b" * 64]


def test_validate_report_flags_missing_artifacts(tmp_path: Path) -> None:
    problems = validate_report(tmp_path)
    assert any("assertions.json" in p for p in problems)
