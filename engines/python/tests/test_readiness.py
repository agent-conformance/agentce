"""Tests for the report-readiness verdict and manual-protocol linters (SPEC §13.3.4)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agentce import cli
from agentce.readiness import (
    NOT_READY,
    READY,
    READY_WITH_LIMITATIONS,
    checklist_lint,
    claim_check,
    compute_readiness,
    deviation_lint,
    normalize_deviation_dates,
    parse_date,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SEVERITIES = {
    "OVS-03": "high",
    "REC-01": "high",
    "DAT-01": "medium",
    "TRN-01": "medium",
}


def _report(
    tmp_path: Path,
    *,
    assertions: list[dict] | None = None,
    integrity: list[dict] | None = None,
    coverage: dict | None = None,
    applicability: list[dict] | None = None,
) -> Path:
    out = tmp_path / "report"
    out.mkdir()
    (out / "assertions.json").write_text(json.dumps(assertions or []), encoding="utf-8")
    (out / "integrity.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (integrity or [])), encoding="utf-8"
    )
    (out / "coverage.json").write_text(
        json.dumps(coverage or {"subjects": {}}), encoding="utf-8"
    )
    (out / "applicability.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in (applicability or [])), encoding="utf-8"
    )
    return out


def test_clean_report_is_ready(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        assertions=[{"control": "OVS-03", "outcome": "conformant", "subject": "s"}],
        integrity=[{"status": "verified", "stream": "a"}],
        coverage={"subjects": {"s": {"coverage_status": "ok"}}},
    )
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["verdict"] == READY
    assert verdict["reasons"] == []


def test_broken_integrity_is_not_ready(tmp_path: Path) -> None:
    report = _report(tmp_path, integrity=[{"status": "failed", "stream": "gw"}])
    assert compute_readiness(report, severities=_SEVERITIES)["verdict"] == NOT_READY


def test_coverage_gap_is_not_ready(tmp_path: Path) -> None:
    report = _report(tmp_path, coverage={"subjects": {"s": {"coverage_status": "gap"}}})
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["verdict"] == NOT_READY
    assert any("coverage" in r for r in verdict["reasons"])


def test_unknown_coverage_is_not_a_blocker(tmp_path: Path) -> None:
    report = _report(
        tmp_path, coverage={"subjects": {"s": {"coverage_status": "unknown"}}}
    )
    assert compute_readiness(report, severities=_SEVERITIES)["verdict"] == READY


def test_below_threshold_event_type_is_not_ready(tmp_path: Path) -> None:
    # A below-threshold event type is a shortfall even when the subject roll-up is 'unknown'.
    report = _report(
        tmp_path,
        coverage={
            "subjects": {
                "s": {
                    "coverage_status": "unknown",
                    "event_types": {"ToolCall": {"status": "below_threshold"}},
                }
            }
        },
    )
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["verdict"] == NOT_READY
    assert any("shortfall" in r for r in verdict["reasons"])


def test_applicability_drift_is_not_ready(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        applicability=[
            {"drift": [{"kind": "undeclared_decision_type", "ref": "dom:X"}]}
        ],
    )
    assert compute_readiness(report, severities=_SEVERITIES)["verdict"] == NOT_READY


def test_high_insufficient_without_gap_is_not_ready(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        assertions=[
            {"control": "OVS-03", "outcome": "insufficient_evidence", "subject": "s"}
        ],
    )
    assert compute_readiness(report, severities=_SEVERITIES)["verdict"] == NOT_READY


def test_high_insufficient_with_gap_is_ready_with_limitations(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        assertions=[
            {"control": "OVS-03", "outcome": "insufficient_evidence", "subject": "s"}
        ],
    )
    verdict = compute_readiness(report, severities=_SEVERITIES, gaps={"OVS-03"})
    assert verdict["verdict"] == READY_WITH_LIMITATIONS
    assert verdict["limitations"]


def test_medium_insufficient_is_ready(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        assertions=[
            {"control": "DAT-01", "outcome": "insufficient_evidence", "subject": "s"}
        ],
    )
    assert compute_readiness(report, severities=_SEVERITIES)["verdict"] == READY


def _deviation(**over: object) -> dict:
    base: dict[str, object] = {
        "control": "OVS-03",
        "rationale": "compensated",
        "compensating_control": "manual review",
        "owner": "alice",
        "approver": "bob",
        "granted": "2026-01-01",
        "expiry": "2026-03-01",
    }
    base.update(over)
    return base


def test_valid_deviation_lints_clean() -> None:
    problems = deviation_lint(
        [_deviation()],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"non-conformant"})},
    )
    assert problems == []


def test_deviation_on_insufficient_is_refused() -> None:
    problems = deviation_lint(
        [_deviation()],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"insufficient_evidence"})},
    )
    assert any("insufficient_evidence" in p for p in problems)


def test_deviation_on_int_family_is_refused() -> None:
    problems = deviation_lint(
        [_deviation(control="INT-01")],
        control_ids={"INT-01"},
        outcomes_by_control={"INT-01": frozenset({"non-conformant"})},
    )
    assert any("INT family" in p for p in problems)


def test_deviation_requires_distinct_approver_and_fields() -> None:
    problems = deviation_lint(
        [_deviation(approver="alice", rationale="")],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"non-conformant"})},
    )
    assert any("distinct" in p for p in problems)
    assert any("missing rationale" in p for p in problems)


def test_deviation_expiry_within_max() -> None:
    problems = deviation_lint(
        [_deviation(expiry="2027-01-01")],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"non-conformant"})},
    )
    assert any("lifetime exceeds" in p for p in problems)


def test_unknown_control_is_refused() -> None:
    problems = deviation_lint(
        [_deviation(control="ZZZ-99")], control_ids={"OVS-03"}, outcomes_by_control={}
    )
    assert any("not in the catalog" in p for p in problems)


def test_deviation_lint_accepts_multi_subject_control_regardless_of_iteration_order() -> (
    None
):
    outcomes = frozenset({"non-conformant", "conformant"})
    a = deviation_lint(
        [_deviation()], control_ids={"OVS-03"}, outcomes_by_control={"OVS-03": outcomes}
    )
    b = deviation_lint(
        [_deviation()],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset(reversed(list(outcomes)))},
    )
    assert a == b == []


def test_deviation_lint_skips_the_outcome_check_for_an_already_applied_control() -> (
    None
):
    problems = deviation_lint(
        [_deviation()],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"partial"})},
        applied_controls=frozenset({"OVS-03"}),
    )
    assert problems == []


def test_deviation_lint_rejects_an_applied_deviation_that_has_expired() -> None:
    problems = deviation_lint(
        [_deviation(expiry="2020-01-01", granted="2019-08-01")],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"partial"})},
        applied_controls=frozenset({"OVS-03"}),
        as_of="2026-01-01",
    )
    assert any("applied deviation has expired" in p for p in problems)


def test_deviation_lint_does_not_reject_an_unexpired_or_not_yet_applied_entry() -> None:
    unexpired = deviation_lint(
        [_deviation(expiry="2027-01-01", granted="2026-11-01")],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"partial"})},
        applied_controls=frozenset({"OVS-03"}),
        as_of="2026-12-01",
    )
    assert not any("expired" in p for p in unexpired)
    not_yet_applied = deviation_lint(
        [_deviation(expiry="2020-01-01")],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"non-conformant"})},
        as_of="2026-01-01",
    )
    assert not any("expired" in p for p in not_yet_applied)


def test_deviation_lint_rejects_a_duplicate_entry_for_the_same_control() -> None:
    problems = deviation_lint(
        [_deviation(), _deviation()],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"non-conformant"})},
    )
    assert any("duplicate deviation entry" in p for p in problems)


def test_deviation_lint_rejects_an_unparseable_granted_or_expiry_rather_than_silently_skipping_it() -> (
    None
):
    problems = deviation_lint(
        [_deviation(expiry="not-a-date")],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"non-conformant"})},
    )
    assert any("expiry is not a valid RFC 3339 date" in p for p in problems)
    non_string = deviation_lint(
        [_deviation(expiry=20210101)],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"non-conformant"})},
    )
    assert any("expiry is not a valid RFC 3339 date" in p for p in non_string)
    granted = deviation_lint(
        [_deviation(granted="not-a-date")],
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"non-conformant"})},
    )
    assert any("granted is not a valid RFC 3339 date" in p for p in granted)


def test_parse_date_rejects_fromisoformats_extra_forms_rfc_3339_only() -> None:
    """2026-09-30 maintainer decision (`TRADEOFFS.md`/inbox row 19): one grammar in all three
    engines, RFC 3339 only -- ``parse_date`` must no longer accept the forms real
    ``datetime.fromisoformat`` does beyond RFC 3339 (confirmed against Python 3.12: each of these
    parses successfully today, before this narrowing). TypeScript's and Java's own ``parseDate``
    already reject all four; this is the cross-engine vector proving Python now agrees."""
    assert parse_date("20211231") is None  # basic format (no separators)
    assert parse_date("2021-W52-5") is None  # ISO week date
    assert (
        parse_date("2021-12-31T10") is None
    )  # hour-only precision, no minutes/seconds
    assert parse_date("2021-12-31T10:30") is None  # minute precision, no seconds
    assert parse_date("2021-12-31T10:30:00") == date(
        2021, 12, 31
    )  # RFC 3339 still accepted


def test_parse_date_accepts_an_already_parsed_date_or_datetime() -> None:
    """`parse_date` must handle a `date`/`datetime` object directly, not just a `str`: an unquoted
    YAML date (`expiry: 2026-03-01`) parses to a `date`, not a `str`, before it ever reaches this
    function. Accepting it here -- not just via `normalize_deviation_dates` at the loading boundary --
    means a caller that forgets that normalization step still gets a correct parse instead of a
    falsely-invalid or silently-absent field (round-2 regression: two `agentce-prepare-to-share`
    scripts called `deviation_lint`/`compute_readiness` directly on an un-normalized register, and
    `deviation_lint`'s own round-1 unparseable-date check treated the resulting `date` object as
    invalid)."""
    from datetime import date, datetime

    assert parse_date(date(2026, 3, 1)) == date(2026, 3, 1)
    assert parse_date(datetime(2026, 3, 1, 12, 30)) == date(2026, 3, 1)


def test_normalize_deviation_dates_converts_yaml_date_objects_to_their_iso_string() -> (
    None
):
    """`normalize_deviation_dates` additionally normalizes a register's `date`/`datetime` fields to
    their ISO string form at the loading boundary, so every consumer that renders a register entry
    verbatim (not just the ones that call `parse_date`) sees a JSON-serializable string."""
    from datetime import date, datetime

    normalized = normalize_deviation_dates(
        [
            {
                "control": "OVS-03",
                "granted": date(2026, 1, 1),
                "expiry": datetime(2026, 3, 1, 0, 0, 0),
                "rationale": "already a string",
            }
        ]
    )
    assert normalized == [
        {
            "control": "OVS-03",
            "granted": "2026-01-01",
            "expiry": "2026-03-01 00:00:00",
            "rationale": "already a string",
        }
    ]
    problems = deviation_lint(
        normalized,
        control_ids={"OVS-03"},
        outcomes_by_control={"OVS-03": frozenset({"non-conformant"})},
    )
    assert not any("not a valid ISO-8601 date" in p for p in problems)


def test_deviation_lint_rejects_insufficient_evidence_even_alongside_a_non_conformant_outcome() -> (
    None
):
    problems = deviation_lint(
        [_deviation()],
        control_ids={"OVS-03"},
        outcomes_by_control={
            "OVS-03": frozenset({"non-conformant", "insufficient_evidence"})
        },
    )
    assert any("insufficient_evidence" in p for p in problems)


def test_deviation_lint_rejects_a_deviation_for_a_control_not_in_the_run() -> None:
    problems = deviation_lint(
        [_deviation(control="OVS-99")],
        control_ids={"OVS-03"},
        outcomes_by_control={},
    )
    assert any("not in the catalog" in p for p in problems)


def test_invalid_deviation_makes_report_not_ready(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        assertions=[{"control": "OVS-03", "outcome": "non-conformant", "subject": "s"}],
    )
    verdict = compute_readiness(
        report, severities=_SEVERITIES, deviations=[_deviation(expiry="2027-06-01")]
    )
    assert verdict["verdict"] == NOT_READY


def test_compute_readiness_accepts_a_report_whose_deviation_was_already_applied(
    tmp_path: Path,
) -> None:
    """A re-lint after ``assess.apply_deviations`` sees ``outcome: partial`` and ``deviation`` set --
    the finding-1 regression: readiness must not reject the very state assess produces."""
    report = _report(
        tmp_path,
        assertions=[
            {
                "control": "OVS-03",
                "outcome": "partial",
                "deviation": "OVS-03",
                "subject": "s",
                "window": {"start": "2026-01-01", "end": "2026-02-01"},
            }
        ],
    )
    verdict = compute_readiness(
        report, severities=_SEVERITIES, deviations=[_deviation()]
    )
    assert verdict["verdict"] != NOT_READY


def test_compute_readiness_not_ready_when_an_applied_deviation_has_expired(
    tmp_path: Path,
) -> None:
    report = _report(
        tmp_path,
        assertions=[
            {
                "control": "OVS-03",
                "outcome": "partial",
                "deviation": "OVS-03",
                "subject": "s",
                "window": {"start": "2026-01-01", "end": "2026-02-01"},
            }
        ],
    )
    verdict = compute_readiness(
        report, severities=_SEVERITIES, deviations=[_deviation(expiry="2020-01-01")]
    )
    assert verdict["verdict"] == NOT_READY


def _checklist(**over: object) -> dict:
    base: dict[str, object] = {
        "checklist": "OVS-09-M",
        "control": "OVS-09",
        "items": [
            {"id": "Q1", "artifact": "log", "question": "ok?", "answers": ["yes", "no"]}
        ],
        "completed_by": {
            "principal": "assessor@corp",
            "org": "AcmeAudit",
            "independent": True,
        },
    }
    base.update(over)
    return base


def test_checklist_valid_record_lints_clean() -> None:
    assert checklist_lint([_checklist()]) == []


def test_checklist_requires_completed_by() -> None:
    record = _checklist()
    del record["completed_by"]
    assert any("completed_by" in p for p in checklist_lint([record]))


def test_checklist_rejects_unknown_item_field() -> None:
    record = _checklist()
    record["items"][0]["response"] = (
        "maybe"  # not in the schema (additionalProperties: false)
    )
    assert any("schema" in p for p in checklist_lint([record]))


def test_checklist_stale_artifact_digest() -> None:
    record = _checklist(artifact_digests={"log": "sha256:abc123"})
    problems = checklist_lint([record], current_digests={"log": "sha256:def456"})
    assert any("changed since inspection" in p for p in problems)


def test_checklist_schema_violation() -> None:
    assert checklist_lint([{"checklist": "X", "control": "Y"}])  # missing items


def _claim() -> dict:
    return {
        "claim_id": "urn:claim:1",
        "subjects": [{"id": "s", "role": "both"}],
        "observation_window": {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-04-01T00:00:00Z",
        },
        "catalogs": [{"id": "eu-ai-act", "version": "2026.09"}],
    }


def test_claim_check_matches_profile() -> None:
    claim = _claim()
    profile = {
        "subjects": [{"id": "s"}],
        "observation_window": claim["observation_window"],
        "catalogs": ["eu-ai-act@2026.09"],
    }
    assert claim_check(claim, {}, profile) == []


def test_claim_check_detects_subject_mismatch() -> None:
    claim = _claim()
    profile = {
        "subjects": [{"id": "other"}],
        "observation_window": claim["observation_window"],
        "catalogs": ["eu-ai-act@2026.09"],
    }
    assert any("subjects" in p for p in claim_check(claim, {}, profile))


def test_vendored_manual_schemas_match_spec() -> None:
    for vendored_rel, spec_rel in (
        ("deviation-register", "spec/report/deviation-register.schema.json"),
        ("checklist", "spec/rules/checklist.schema.json"),
    ):
        vendored = (
            _REPO_ROOT
            / "engines/python/agentce/data/schemas"
            / f"{vendored_rel}.schema.json"
        ).read_text(encoding="utf-8")
        assert vendored == (_REPO_ROOT / spec_rel).read_text(encoding="utf-8")


def test_readiness_cli_ready(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(
        tmp_path,
        assertions=[{"control": "OVS-03", "outcome": "conformant", "subject": "s"}],
        integrity=[{"status": "verified", "stream": "a"}],
    )
    code = cli.main(["readiness", str(report), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["verdict"] == READY
    assert (report / payload["report"].rsplit("/", 1)[-1]).is_file()


def test_readiness_cli_not_ready(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(tmp_path, integrity=[{"status": "failed", "stream": "gw"}])
    code = cli.main(["readiness", str(report), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["verdict"] == NOT_READY


def test_readiness_cli_with_gaps_and_deviations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(
        tmp_path,
        assertions=[
            {"control": "OVS-03", "outcome": "insufficient_evidence", "subject": "s"}
        ],
    )
    gaps = tmp_path / "gaps.md"
    gaps.write_text("OVS-03 owned by alice on 2026-02-01\n", encoding="utf-8")
    deviations = tmp_path / "deviations.yaml"
    deviations.write_text("deviations: []\n", encoding="utf-8")
    code = cli.main(
        [
            "readiness",
            str(report),
            "--gaps",
            str(gaps),
            "--deviations",
            str(deviations),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["verdict"] == READY_WITH_LIMITATIONS
