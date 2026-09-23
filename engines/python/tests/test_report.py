"""The report generator: schema-valid artifacts, DC-5 refusal, SARIF/OSCAL, and supersession."""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agentce.assertions import Assertion, EvidencePointer
from agentce.catalog import Catalog, load_catalog
from agentce.errors import AgentceError
from agentce.report import (
    CSV_COLUMNS,
    EMIT_FORMATS,
    render_csv,
    render_junit,
    render_oscal,
    render_oscal_xml,
    render_pdf,
    render_sarif,
    render_step_summary,
    validate_oscal_ar_nist,
    validate_report,
    validate_sarif_2_1_0,
    write_github_step_summary,
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
    assert sarif["$schema"]
    assert any(r["level"] == "error" for r in sarif["runs"][0]["results"])


def test_sarif_rules_carry_catalog_sourced_help() -> None:
    catalog = load_catalog(_BASE_CATALOG_DIR)
    sarif = render_sarif([_assertion("conformant")], catalogs=[catalog])
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["id"] == "OVS-03"
    assert rule["name"]
    assert rule["help"]["text"] and rule["help"]["text"] != "AgentCE control OVS-03."
    assert rule["helpUri"].startswith("https://")


def test_sarif_rules_still_carry_help_without_a_catalog() -> None:
    """No catalog resolved (a bare `report --format sarif` re-render): help falls back to a stable
    control-id sentence rather than an empty or missing field."""
    sarif = render_sarif([_assertion("conformant")])
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["name"] and rule["help"]["text"] and rule["helpUri"]


def test_sarif_results_carry_locations_and_fingerprints() -> None:
    sarif = render_sarif([_assertion("conformant"), _assertion("non-conformant")])
    for result in sarif["runs"][0]["results"]:
        assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert result["partialFingerprints"]


def test_sarif_fingerprints_are_deterministic_across_independent_renders() -> None:
    assertions = [_assertion("conformant"), _assertion("non-conformant")]
    a = render_sarif(list(assertions))
    b = render_sarif(list(reversed(assertions)))
    fp = lambda doc: sorted(  # noqa: E731
        (r["ruleId"], tuple(sorted(r["partialFingerprints"].items())))
        for r in doc["runs"][0]["results"]
    )
    assert fp(a) == fp(b)


def test_sarif_not_assessed_is_distinct_from_insufficient_evidence() -> None:
    sarif = render_sarif(
        [_assertion("not_assessed"), _assertion("insufficient_evidence")]
    )
    levels = {r["ruleId"]: r["level"] for r in sarif["runs"][0]["results"]}
    assert levels["OVS-03"] in (
        "note",
        "warning",
    )  # last-write for the shared control id
    na_only = render_sarif([_assertion("not_assessed")])
    ie_only = render_sarif([_assertion("insufficient_evidence")])
    na_level = na_only["runs"][0]["results"][0]["level"]
    ie_level = ie_only["runs"][0]["results"][0]["level"]
    assert na_level != ie_level


def test_sarif_validates_against_the_real_oasis_2_1_0_schema() -> None:
    sarif = render_sarif(
        [
            _assertion("conformant"),
            _assertion("non-conformant"),
            _assertion("not_assessed"),
        ]
    )
    assert validate_sarif_2_1_0(sarif) == []


def test_sarif_validates_with_no_assertions() -> None:
    assert validate_sarif_2_1_0(render_sarif([])) == []


def _pre_fix_sarif_shape() -> dict[str, object]:
    """The bare shape `render_sarif` emitted at the base commit: `{"id": control}` rules, no
    locations/partialFingerprints, no top-level $schema -- exactly finding #20's defect."""
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "agentce-py",
                        "version": "0.1.0",
                        "rules": [{"id": "OVS-03"}],
                    }
                },
                "results": [
                    {
                        "ruleId": "OVS-03",
                        "level": "warning",
                        "message": {
                            "text": "OVS-03 on spiffe://corp/agents/a: insufficient_evidence"
                        },
                    }
                ],
            }
        ],
    }


def test_todays_bare_pre_fix_shape_still_validates_against_real_oasis_sarif() -> None:
    """The real OASIS SARIF 2.1.0 format is deliberately permissive (a minimal tool can emit a bare
    rule and result), so it alone cannot catch a code-scanning-incomplete bare shape -- that is exactly why
    finding #20 widens AgentCE's own bounded profile (below) rather than editing the vendored schema."""
    assert validate_sarif_2_1_0(_pre_fix_sarif_shape()) == []


def test_widened_local_profile_rejects_the_pre_fix_shape_but_accepts_the_real_output() -> (
    None
):
    """The discriminating check: AgentCE's own `results-sarif.schema.json` profile now requires the
    code-scanning fields, so it refuses the pre-fix bare shape (arm 2) while still accepting the real
    enriched output (arm 1) -- neither a toothless nor a reject-everything schema passes both arms."""
    import jsonschema
    from importlib import resources

    schema = json.loads(
        resources.files("agentce.data.schemas")
        .joinpath("results-sarif.schema.json")
        .read_text("utf-8")
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_pre_fix_sarif_shape(), schema)

    real = render_sarif([_assertion("conformant"), _assertion("non-conformant")])
    jsonschema.validate(real, schema)  # must not raise


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


# --- render_junit -----------------------------------------------------------------------------


def test_junit_one_testcase_per_pair_and_failure_iff_not_conformant() -> None:
    conformant = _assertion("conformant")
    failing = Assertion(
        control="OVS-04",
        control_version="2026.09",
        subject="spiffe://corp/agents/a",
        outcome="non-conformant",
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=(3, 1),
        evidence=[_EVIDENCE],
    )
    doc = ET.fromstring(render_junit([conformant, failing]))
    cases = doc.findall("testcase")
    assert len(cases) == 2
    by_name = {c.get("name"): c for c in cases}
    assert by_name["OVS-03"].find("failure") is None
    failure = by_name["OVS-04"].find("failure")
    assert failure is not None
    assert failure.text and "non-conformant" in failure.text
    assert doc.get("tests") == "2"
    assert doc.get("failures") == "1"


def test_junit_control_identifiable_in_classname_or_name() -> None:
    doc = ET.fromstring(render_junit([_assertion("conformant")]))
    case = doc.find("testcase")
    assert case is not None
    combined = f"{case.get('classname')}|{case.get('name')}"
    assert combined.count("OVS-03") == 1


def test_junit_is_well_formed_and_sorted() -> None:
    a = _assertion("conformant")
    b = Assertion(
        control="AAA-01",
        control_version="2026.09",
        subject="spiffe://corp/agents/z",
        outcome="conformant",
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=(1, 0),
        evidence=[_EVIDENCE],
    )
    doc = ET.fromstring(render_junit([a, b]))
    # sorted by (subject, control): "spiffe://corp/agents/a" < "spiffe://corp/agents/z"
    pairs = [(c.get("classname"), c.get("name")) for c in doc.findall("testcase")]
    assert pairs == sorted(pairs)


def _distinct_pair() -> list[Assertion]:
    """Two assertions with distinct ``(subject, control)`` pairs (real assertions never repeat a
    pair), so reversing the input order actually exercises the renderer's own sort rather than
    Python's stable-sort tie-breaking on equal keys."""
    return [
        _assertion("conformant"),
        Assertion(
            control="AAA-01",
            control_version="2026.09",
            subject="spiffe://corp/agents/a",
            outcome="non-conformant",
            rung=2,
            mode="automated",
            window=_WINDOW,
            population=(3, 1),
            evidence=[_EVIDENCE],
        ),
    ]


def test_junit_is_byte_identical_across_independent_runs() -> None:
    assertions = _distinct_pair()
    a = render_junit(list(assertions))
    b = render_junit(list(reversed(assertions)))
    assert a == b


def test_junit_empty_is_well_formed() -> None:
    doc = ET.fromstring(render_junit([]))
    assert doc.get("tests") == "0"
    assert doc.findall("testcase") == []


# --- render_csv ---------------------------------------------------------------------------------


def test_csv_header_and_rows() -> None:
    text = render_csv([_assertion("conformant"), _assertion("non-conformant")])
    rows = list(csv.reader(io.StringIO(text)))
    header, data = rows[0], rows[1:]
    for column in ("control", "subject", "outcome"):
        assert column in header
    assert header == list(CSV_COLUMNS)
    assert len(data) == 2
    outcome_idx = header.index("outcome")
    assert {row[outcome_idx] for row in data} == {"conformant", "non-conformant"}


def test_csv_sorted_and_deterministic() -> None:
    assertions = _distinct_pair()
    a = render_csv(list(assertions))
    b = render_csv(list(reversed(assertions)))
    assert a == b
    assert "\r\n" not in a


# --- render_oscal_xml -----------------------------------------------------------------------------


def test_oscal_xml_well_formed_and_carries_target_id() -> None:
    doc = render_oscal([_assertion("conformant"), _assertion("non-conformant")])
    xml_text = render_oscal_xml(doc)
    ET.fromstring(xml_text)  # must not raise
    assert "OVS-03" in xml_text


def test_oscal_xml_escapes_untrusted_values() -> None:
    hostile = Assertion(
        control="OVS-03",
        control_version="2026.09",
        subject="spiffe://corp/agents/<script>alert(1)</script>",
        outcome="conformant",
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=(1, 0),
        evidence=[_EVIDENCE],
    )
    doc = render_oscal([hostile])
    xml_text = render_oscal_xml(doc)
    assert "<script>" not in xml_text
    ET.fromstring(xml_text)  # still well-formed


def test_oscal_xml_deterministic_across_runs() -> None:
    doc = render_oscal([_assertion("conformant")])
    assert render_oscal_xml(doc) == render_oscal_xml(doc)


# --- render_pdf ------------------------------------------------------------------------------


_STARTED_AT = "2026-01-02T03:04:05Z"


def test_pdf_has_real_pdf_structure() -> None:
    from agentce.assertions import aggregate

    assertions = [_assertion("conformant"), _assertion("non-conformant")]
    pdf = render_pdf(assertions, aggregate(assertions), started_at=_STARTED_AT)
    assert pdf.startswith(b"%PDF-")
    tail = pdf[-200:]
    assert b"%%EOF" in tail
    assert b"/BaseFont /Helvetica" in pdf
    assert b"/Type /Font" in pdf


def test_pdf_embeds_the_manifest_started_at_not_wall_clock() -> None:
    from agentce.assertions import aggregate

    assertions = [_assertion("conformant")]
    pdf = render_pdf(assertions, aggregate(assertions), started_at=_STARTED_AT)
    digits = re.sub(r"\D", "", _STARTED_AT)
    assert digits.encode("ascii") in pdf


def test_pdf_content_stream_visibly_labels_derived_non_canonical() -> None:
    from agentce.assertions import aggregate

    assertions = [_assertion("conformant")]
    pdf = render_pdf(assertions, aggregate(assertions), started_at=_STARTED_AT)
    match = re.search(rb"5 0 obj\n(.*?)endobj", pdf, re.S)
    assert match is not None
    content = match.group(1).lower()
    assert b"derived" in content
    assert b"non-canonical" in content


def test_pdf_font_object_is_byte_identical_across_independent_runs() -> None:
    from agentce.assertions import aggregate

    assertions = [_assertion("conformant"), _assertion("non-conformant")]
    a = render_pdf(list(assertions), aggregate(assertions), started_at=_STARTED_AT)
    b = render_pdf(
        list(reversed(assertions)),
        aggregate(assertions),
        started_at="2099-12-31T23:59:59Z",
    )

    def font_object(pdf: bytes) -> bytes:
        match = re.search(rb"4 0 obj\n(.*?)endobj", pdf, re.S)
        assert match is not None
        return match.group(1)

    assert font_object(a) == font_object(b)


def test_pdf_no_pdf_library_is_a_mandatory_dependency() -> None:
    pyproject = (_REPO_ROOT / "engines/python/pyproject.toml").read_text(
        encoding="utf-8"
    )
    banned = (
        "reportlab",
        "weasyprint",
        "fpdf",
        "xhtml2pdf",
        "wkhtmltopdf",
        "pdfkit",
        "pymupdf",
        "mupdf",
        "borb",
    )
    # only inspect the mandatory [project] dependencies block, not this comment/test file
    deps_block = pyproject.split("[dependency-groups]")[0]
    for name in banned:
        assert name not in deps_block.lower()


# --- $GITHUB_STEP_SUMMARY ---------------------------------------------------------------------


def test_step_summary_contains_control_id_and_assessed() -> None:
    from agentce.assertions import aggregate

    assertions = [_assertion("conformant")]
    text = render_step_summary(assertions, aggregate(assertions))
    assert "assessed" in text
    assert "OVS-03" in text


def test_write_github_step_summary_noop_without_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentce.assertions import aggregate

    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assertions = [_assertion("conformant")]
    assert write_github_step_summary(assertions, aggregate(assertions)) is False


def test_write_github_step_summary_appends_when_env_var_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agentce.assertions import aggregate

    target = tmp_path / "summary.md"
    target.write_text("existing content\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
    assertions = [_assertion("conformant")]
    assert write_github_step_summary(assertions, aggregate(assertions)) is True
    text = target.read_text(encoding="utf-8")
    assert text.startswith("existing content\n")
    assert "## AgentCE assessment summary" in text
    assert "OVS-03" in text


# --- write_report(emit=...) --------------------------------------------------------------------


def test_write_report_default_emit_is_the_legacy_fixed_bundle(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        [_assertion("conformant")],
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
        assert (tmp_path / name).is_file(), name
    for name in (
        "report.junit.xml",
        "report.csv",
        "oscal-ar.xml",
        "report.pdf",
        "public-statement.md",
    ):
        assert not (tmp_path / name).exists(), name


def test_write_report_emit_md_renders_only_report_md(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog()],
        emit=frozenset({"md"}),
    )
    assert (tmp_path / "report.md").is_file()
    for name in (
        "report.html",
        "oscal-ar.json",
        "results.sarif",
        "report.junit.xml",
        "report.csv",
        "oscal-ar.xml",
        "report.pdf",
        "public-statement.md",
    ):
        assert not (tmp_path / name).exists(), name
    assert not (tmp_path / "packs").exists()


def test_write_report_emit_new_formats(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        [_assertion("conformant"), _assertion("non-conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog()],
        emit=frozenset({"junit", "csv", "oscal_xml", "pdf", "public"}),
    )
    for name in (
        "report.junit.xml",
        "report.csv",
        "oscal-ar.xml",
        "oscal-ar.json",  # the source object oscal-ar.xml serializes; written alongside it
        "report.pdf",
        "public-statement.md",
    ):
        assert (tmp_path / name).is_file(), name
    assert "Public conformance statement" in (
        tmp_path / "public-statement.md"
    ).read_text(encoding="utf-8")
    assert (tmp_path / "report.pdf").read_bytes().startswith(b"%PDF-")
    for name in ("report.md", "report.html", "results.sarif"):
        assert not (tmp_path / name).exists(), name


def test_write_report_emit_validates_clean(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        [_assertion("conformant"), _assertion("non-conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog()],
        emit=frozenset(EMIT_FORMATS),
    )
    assert validate_report(tmp_path) == []


# --- validate_report: the new optional artifacts ----------------------------------------------


def test_validate_report_accepts_a_genuine_junit_and_csv(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog()],
        emit=frozenset(
            {"md", "html", "oscal", "sarif", "pack", "junit", "csv", "oscal_xml"}
        ),
    )
    assert validate_report(tmp_path) == []


def test_validate_report_rejects_corrupted_csv(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog()],
        emit=frozenset({"md", "html", "oscal", "sarif", "pack", "csv"}),
    )
    (tmp_path / "report.csv").write_text(
        "not a csv file at all, just garbage\x00\x01\n", encoding="utf-8"
    )
    problems = validate_report(tmp_path)
    assert any("report.csv" in p for p in problems)


def test_validate_report_rejects_malformed_junit_xml(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog()],
        emit=frozenset({"md", "html", "oscal", "sarif", "pack", "junit"}),
    )
    (tmp_path / "report.junit.xml").write_text("<not-closed>", encoding="utf-8")
    problems = validate_report(tmp_path)
    assert any("report.junit.xml" in p for p in problems)


def test_validate_report_optional_artifacts_absent_is_fine(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        [_assertion("conformant")],
        bundle_digest="sha256:" + "a" * 64,
        catalogs=[_catalog()],
    )
    assert validate_report(tmp_path) == []
