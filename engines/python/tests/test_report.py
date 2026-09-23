"""The report generator: schema-valid artifacts, DC-5 refusal, SARIF/OSCAL, and supersession."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentce.assertions import Assertion, EvidencePointer
from agentce.catalog import Catalog
from agentce.errors import AgentceError
from agentce.report import (
    render_oscal,
    render_sarif,
    validate_oscal_ar_nist,
    validate_report,
    write_report,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE_CATALOG_DIR = _REPO_ROOT / "spec/catalogs/base/eu-ai-act"
_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
_EVIDENCE = EvidencePointer(
    "agentce:event/x", "sha256:" + "0" * 64, "enforcement_point"
)


def _catalog(id: str = "eu-ai-act", version: str = "2026.09") -> Catalog:
    return Catalog(
        id=id, version=version, directory=_BASE_CATALOG_DIR, controls=[], shapes={}
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
        catalogs=[_catalog()],
    )
    for name in (
        "assertions.json",
        "report.md",
        "report.html",
        "oscal-ar.json",
        "results.sarif",
        "manifest.json",
        "claim.json",
    ):
        assert (tmp_path / name).is_file()
    assert validate_report(tmp_path) == []


def test_empty_report_is_valid(tmp_path: Path) -> None:
    write_report(
        tmp_path, [], bundle_digest="sha256:" + "a" * 64, catalogs=[_catalog()]
    )
    assert not (tmp_path / "claim.json").exists()
    assert json.loads((tmp_path / "assertions.json").read_text(encoding="utf-8")) == []
    assert validate_report(tmp_path) == []


def test_assertions_json_is_deterministic(tmp_path: Path) -> None:
    write_report(
        tmp_path / "a",
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog("c", "1")],
    )
    write_report(
        tmp_path / "b",
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog("c", "1")],
    )
    assert (tmp_path / "a" / "assertions.json").read_bytes() == (
        tmp_path / "b" / "assertions.json"
    ).read_bytes()


def test_dc5_aborts_before_writing(tmp_path: Path) -> None:
    bad = Assertion(
        "C", "1", "s", "conformant", 2, "automated", _WINDOW, (1, 0), evidence=[]
    )
    with pytest.raises(AgentceError) as excinfo:
        write_report(
            tmp_path, [bad], bundle_digest="sha256:" + "a" * 64, catalogs=[_catalog()]
        )
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


def test_oscal_finding_resolves_to_an_observation_with_its_evidence() -> None:
    oscal = render_oscal([_assertion("conformant")])
    result = oscal["assessment-results"]["results"][0]
    finding = result["findings"][0]
    obs_uuid = finding["related-observations"][0]["observation-uuid"]
    observations = {o["uuid"]: o for o in result["observations"]}
    assert obs_uuid in observations
    evidence_hrefs = [e["href"] for e in observations[obs_uuid]["relevant-evidence"]]
    assert _EVIDENCE.ref in evidence_hrefs


def test_oscal_finding_without_evidence_still_resolves_to_an_observation() -> None:
    bare = Assertion(
        control="OVS-03",
        control_version="2026.09",
        subject="spiffe://corp/agents/a",
        outcome="not_assessed",
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=(3, 0),
        evidence=[],
    )
    oscal = render_oscal([bare])
    result = oscal["assessment-results"]["results"][0]
    finding = result["findings"][0]
    obs_uuid = finding["related-observations"][0]["observation-uuid"]
    observations = {o["uuid"]: o for o in result["observations"]}
    assert obs_uuid in observations
    assert "relevant-evidence" not in observations[obs_uuid]


def test_oscal_finding_links_to_its_real_control_id() -> None:
    oscal = render_oscal([_assertion("conformant")])
    finding = oscal["assessment-results"]["results"][0]["findings"][0]
    assert any("OVS-03" in link["href"] for link in finding["links"]), (
        "finding must trace back to a real control id, per SPEC §9"
    )


def test_oscal_ar_validates_against_the_real_nist_1_1_2_schema() -> None:
    oscal = render_oscal([_assertion("conformant"), _assertion("non-conformant")])
    assert validate_oscal_ar_nist(oscal) == []


def test_oscal_ar_validates_with_no_assertions() -> None:
    assert validate_oscal_ar_nist(render_oscal([])) == []


def test_todays_bare_pre_fix_shape_fails_the_real_nist_schema() -> None:
    """Proves the check discriminates: the pre-fix shape (target-id + status + title + uuid, no
    observations, no import-ap, no reviewed-controls) is what `render_oscal` emitted at the base
    commit, and it must fail real NIST validation -- that failure is exactly finding #23."""
    pre_fix_shape = {
        "assessment-results": {
            "uuid": "12345678-1234-5678-89ab-1234567890ab",
            "metadata": {"title": "x", "version": "0.1.0", "oscal-version": "1.1.2"},
            "results": [
                {
                    "uuid": "11111111-1234-5678-89ab-1234567890ab",
                    "title": "t",
                    "findings": [
                        {
                            "uuid": "44444444-1234-5678-89ab-1234567890ab",
                            "title": "OVS-03 for x",
                            "target": {
                                "type": "objective-id",
                                "target-id": "OVS-03",
                                "status": {"state": "satisfied"},
                            },
                        }
                    ],
                }
            ],
        }
    }
    assert validate_oscal_ar_nist(pre_fix_shape) != []


def test_supersedes_recorded_in_manifest(tmp_path: Path) -> None:
    manifest = write_report(
        tmp_path,
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog("c", "1")],
        supersedes=["sha256:" + "b" * 64],
    )
    assert manifest["supersedes"] == ["sha256:" + "b" * 64]


def test_validate_report_flags_missing_artifacts(tmp_path: Path) -> None:
    problems = validate_report(tmp_path)
    assert any("assertions.json" in p for p in problems)
