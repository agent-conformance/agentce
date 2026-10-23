"""Tests for the report-readiness verdict and manual-protocol linters (SPEC §13.3.4)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
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
    (out / "quarantine.jsonl").write_text("", encoding="utf-8")
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


_REPORT_FILES = (
    "coverage.json",
    "integrity.jsonl",
    "applicability.jsonl",
    "assertions.json",
    "quarantine.jsonl",
)
_not_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a mode-000 file and enters a mode-600 folder"
)


def _unreadable(report: Path, name: str, shape: str, tmp_path: Path) -> Path | None:
    """Make ``report/name`` present but unreadable; returns a folder to reopen after the check."""
    target = report / name
    if shape == "link into a mode-600 folder":
        locked = tmp_path / "locked"
        locked.mkdir()
        target.rename(locked / name)
        target.symlink_to(locked / name)
        locked.chmod(0o600)
        return locked
    if shape == "mode 000":
        target.chmod(0o000)
        return None
    target.unlink()
    if shape == "dangling link":
        target.symlink_to(tmp_path / "nowhere")
    else:
        target.mkdir()
    return None


def _cannot_read(name: str, rule: str | None, line: int = 1) -> str:
    """The NOT READY reason for an unreadable report file; one outside the JSON profile names the
    rule and line (SPEC 8.7, 18.184x)."""
    if rule is None:
        return f"cannot read report file {name}"
    return f"cannot read report file {name}: outside the JSON profile: {rule} at line {line} (SPEC 8.7)"


_MALFORMED = [
    ("coverage.json", "null", None),
    ("coverage.json", "[]", None),
    ("coverage.json", '"x"', None),
    ("coverage.json", "3", None),
    ("coverage.json", "", "syntax"),
    ("coverage.json", "{", "syntax"),
    ("assertions.json", "null", None),
    ("assertions.json", "{}", None),
    ("assertions.json", "", "syntax"),
    ("assertions.json", "[3]", None),
    ("assertions.json", "[null]", None),
    ("integrity.jsonl", "not json\n", "syntax"),
    ("integrity.jsonl", "3\n", None),
    ("applicability.jsonl", "[]\n", None),
    ("applicability.jsonl", '{"drift": {"kind": "k", "ref": "r"}}\n', None),
    ("applicability.jsonl", '{"drift": "x"}\n', None),
    ("applicability.jsonl", '{"drift": null}\n', None),
    ("applicability.jsonl", '{"drift": [3]}\n', None),
    ("coverage.json", '{"subjects": {}, "x": NaN}', "nan"),
    ("assertions.json", '[{"x": Infinity}]', "infinity"),
    ("integrity.jsonl", '{"x": -Infinity}\n', "infinity"),
    ("applicability.jsonl", '{"x": NaN}\n', "nan"),
]


@pytest.mark.parametrize(("name", "text", "rule"), _MALFORMED)
def test_report_file_of_the_wrong_shape_is_not_ready(
    tmp_path: Path, name: str, text: str, rule: str | None
) -> None:
    # Not JSON of its shape reads NOT READY, never a crash and never READY (inbox row 393, 18.184h).
    report = _report(tmp_path)
    (report / name).write_text(text, encoding="utf-8")
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["verdict"] == NOT_READY
    assert verdict["reasons"] == [_cannot_read(name, rule)]


def test_report_file_not_utf8_is_not_ready(tmp_path: Path) -> None:
    report = _report(tmp_path)
    (report / "coverage.json").write_bytes(b'{"subjects": {}}\xff')
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["reasons"] == [_cannot_read("coverage.json", "invalid_utf8")]


_JSONL_RECORD = {
    "integrity.jsonl": b'{"stream": "s", "status": "verified"}',
    "applicability.jsonl": b'{"drift": []}',
    "quarantine.jsonl": b'{"record": "r"}',
}
_NBSP, _BOM = "\u00a0".encode(), "\ufeff".encode()


_SEPARATORS = {
    "vt": b"\x0b",
    "ff": b"\x0c",
    "nel": "\u0085".encode(),
    "ls": "\u2028".encode(),
    "ps": "\u2029".encode(),
    "lone_cr": b"\r",
}


def _joined_by(sep: bytes) -> Callable[[bytes], bytes]:
    return lambda r: r + sep + r + b"\n"


_NOT_JSONL: dict[str, Callable[[bytes], bytes]] = {
    **{k: _joined_by(sep) for k, sep in _SEPARATORS.items()},
    "lone_cr_no_lf": lambda r: r + b"\r" + r,
    "lead_nbsp": lambda r: _NBSP + r + b"\n",
    "lead_bom": lambda r: _BOM + r + b"\n",
    "trail_nbsp": lambda r: r + _NBSP + b"\n",
    "nbsp_line": lambda r: _NBSP + b"\n" + r + b"\n",
    "not_utf8": lambda r: r[:-1] + b', "x": "\xff"}\n',
}
#: The JSON profile rule each line above breaks (SPEC 8.7): FF, NEL, LS, PS and a lone CR are not
#: line ends, so the line runs on past them; VT and NBSP are not whitespace.
_NOT_JSONL_RULE = {
    **dict.fromkeys(
        ("ff", "nel", "ls", "ps", "lone_cr", "lone_cr_no_lf"), "jsonl_line_split"
    ),
    **dict.fromkeys(("vt", "trail_nbsp"), "extra_value"),
    **dict.fromkeys(("lead_nbsp", "nbsp_line"), "syntax"),
    "lead_bom": "bom",
    "not_utf8": "invalid_utf8",
}
_JSONL_OK: dict[str, Callable[[bytes], bytes]] = {
    "crlf": lambda r: r + b"\r\n",
    "space_tab": lambda r: b"\t " + r + b" \t\n",
    "blank_lines": lambda r: b"\n \n\r\n" + r + b"\n\n",
    "cr_between_tokens": lambda r: r.replace(b": ", b":\r", 1) + b"\n",
    "u2028_in_string": lambda r: r[:-1] + ', "x": "a\u2028b"}\n'.encode(),
    "empty": lambda r: b"",
}


@pytest.mark.parametrize("name", sorted(_JSONL_RECORD))
@pytest.mark.parametrize("case", sorted(_NOT_JSONL))
def test_jsonl_line_outside_the_line_rule_is_not_ready(
    tmp_path: Path, name: str, case: str
) -> None:
    """LF alone ends a line; any other separator or a no-break space or byte-order mark leaves a line
    not JSON (18.184n), in every engine alike."""
    report = _report(tmp_path)
    (report / name).write_bytes(_NOT_JSONL[case](_JSONL_RECORD[name]))
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["reasons"] == [_cannot_read(name, _NOT_JSONL_RULE[case])]


@pytest.mark.parametrize("name", sorted(_JSONL_RECORD))
@pytest.mark.parametrize("lines", _JSONL_OK.values(), ids=_JSONL_OK.keys())
def test_jsonl_line_inside_the_line_rule_stays_ready(
    tmp_path: Path, name: str, lines
) -> None:
    report = _report(tmp_path)
    (report / name).write_bytes(lines(_JSONL_RECORD[name]))
    assert compute_readiness(report, severities=_SEVERITIES)["verdict"] == READY


@pytest.mark.parametrize(
    ("name", "record", "reason"),
    [
        (
            "integrity.jsonl",
            b'{"stream": "s", "status": "failed"}',
            "integrity failed on stream s",
        ),
        (
            "applicability.jsonl",
            b'{"drift": [{"kind": "k", "ref": "r"}]}',
            "applicability drift: k r",
        ),
    ],
)
@pytest.mark.parametrize("tail", [b"", b"\r"], ids=["no_lf", "lone_cr_at_end"])
def test_last_jsonl_record_without_lf_is_read(
    tmp_path: Path, name: str, record: bytes, reason: str, tail: bytes
) -> None:
    report = _report(tmp_path)
    (report / name).write_bytes(b"\n" + record + tail)
    assert compute_readiness(report, severities=_SEVERITIES)["reasons"] == [reason]


def test_empty_coverage_object_stays_ready(tmp_path: Path) -> None:
    verdict = compute_readiness(_report(tmp_path, coverage={}), severities=_SEVERITIES)
    assert verdict["verdict"] == READY


@pytest.mark.parametrize(
    "coverage",
    [
        {"subjects": []},
        {"subjects": None},
        {"subjects": {"s": 3}},
        {"subjects": {"s": None}},
        {"subjects": {"s": {"event_types": []}}},
        {"subjects": {"s": {"event_types": {"t": 3}}}},
    ],
)
def test_inner_coverage_entry_of_the_wrong_shape_is_not_ready(
    tmp_path: Path, coverage: dict
) -> None:
    # Skipping an unusable entry loses what deleting it would, so it blocks like a malformed file
    # (R5; watch ruling 2026-10-21 on 18.184h, inbox row 447).
    verdict = compute_readiness(
        _report(tmp_path, coverage=coverage), severities=_SEVERITIES
    )
    assert verdict["verdict"] == NOT_READY
    assert verdict["reasons"] == ["cannot read report file coverage.json"]


_WRONG_TYPED = [
    ("integrity.jsonl", '{"status": ["broken"], "stream": "s"}\n'),
    ("integrity.jsonl", '{"status": null, "stream": "s"}\n'),
    (
        "assertions.json",
        '[{"control": ["DOC-01"], "outcome": "insufficient_evidence"}]',
    ),
    ("assertions.json", '[{"control": "DOC-01", "outcome": 3}]'),
    ("assertions.json", '[{"control": "DOC-01", "window": "x"}]'),
    ("assertions.json", '[{"control": "DOC-01", "window": {"end": 3}}]'),
    ("assertions.json", '[{"control": "DOC-01", "deviation": ["D-1"]}]'),
    ("coverage.json", '{"subjects": {"s": {"coverage_status": ["gap"]}}}'),
    ("coverage.json", '{"subjects": {"s": {"event_types": {"t": {"status": true}}}}}'),
]


@pytest.mark.parametrize("deviations", [None, [{"control": "DOC-01"}]])
@pytest.mark.parametrize(("name", "text"), _WRONG_TYPED)
def test_field_decided_on_of_the_wrong_type_is_not_ready(
    tmp_path: Path, name: str, text: str, deviations: list | None
) -> None:
    # A field the verdict is decided on that is not a JSON string (null included) makes its file
    # unreadable, with or without a deviation register, never read loosely or crashed on (18.184m).
    report = _report(tmp_path)
    (report / name).write_text(text, encoding="utf-8")
    verdict = compute_readiness(report, severities=_SEVERITIES, deviations=deviations)
    assert verdict["verdict"] == NOT_READY
    assert f"cannot read report file {name}" in verdict["reasons"]


def test_null_deviation_stays_readable(tmp_path: Path) -> None:
    # assess writes deviation: null when none applies; that is not a wrong type.
    report = _report(tmp_path)
    (report / "assertions.json").write_text(
        '[{"control": "DOC-01", "outcome": "conformant", "deviation": null}]',
        encoding="utf-8",
    )
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["verdict"] == READY


@_not_root
@pytest.mark.parametrize("name", _REPORT_FILES)
@pytest.mark.parametrize(
    "shape", ["link into a mode-600 folder", "mode 000", "dangling link", "folder"]
)
def test_unreadable_report_file_is_not_ready(
    tmp_path: Path, name: str, shape: str
) -> None:
    # An entry that is there but cannot be read blocks; it never reads as absent, which would
    # let a recorded gap stop blocking (18.165). Same answer on every supported Python.
    report = _report(tmp_path)
    locked = _unreadable(report, name, shape, tmp_path)
    try:
        verdict = compute_readiness(report, severities=_SEVERITIES)
    finally:
        if locked is not None:
            locked.chmod(0o700)
    assert verdict["verdict"] == NOT_READY
    assert verdict["reasons"] == [f"cannot read report file {name}"]


@_not_root
def test_unreadable_report_folder_is_not_ready(tmp_path: Path) -> None:
    report = _report(tmp_path)
    report.chmod(0o600)
    try:
        verdict = compute_readiness(report, severities=_SEVERITIES)
    finally:
        report.chmod(0o700)
    assert verdict["reasons"] == [
        f"cannot read report file {name}" for name in sorted(_REPORT_FILES)
    ]


@pytest.mark.parametrize("name", _REPORT_FILES)
def test_absent_report_file_blocks(tmp_path: Path, name: str) -> None:
    """Deleting a stage-4 file never clears what it recorded (18.166)."""
    report = _report(tmp_path)
    (report / name).unlink()
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["verdict"] == NOT_READY
    assert verdict["reasons"] == [f"missing report file {name}"]


def test_absent_report_file_outweighs_a_recorded_gap(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        assertions=[
            {"control": "OVS-03", "outcome": "insufficient_evidence", "subject": "s"}
        ],
    )
    (report / "coverage.json").unlink()
    verdict = compute_readiness(report, severities=_SEVERITIES, gaps={"OVS-03"})
    assert verdict["verdict"] == NOT_READY
    assert "missing report file coverage.json" in verdict["reasons"]


def test_every_absent_report_file_is_named(tmp_path: Path) -> None:
    report = _report(tmp_path)
    for name in _REPORT_FILES:
        (report / name).unlink()
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["reasons"] == [
        f"missing report file {n}" for n in sorted(_REPORT_FILES)
    ]


@_not_root
def test_sign_refuses_a_report_with_an_unreadable_coverage_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "q"
    assert cli.main(["quickstart", "--out", str(report)]) == 0
    locked = _unreadable(
        report, "coverage.json", "link into a mode-600 folder", tmp_path
    )
    capsys.readouterr()
    try:
        code = cli.main(
            [
                "sign",
                str(report),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(tmp_path / "none.pem"),
                "--dry-run",
                "--json",
            ]
        )
    finally:
        assert locked is not None
        locked.chmod(0o700)
    doc = json.loads(capsys.readouterr().out)
    assert code == 3
    assert doc["error"]["key"] == "sign.not_ready"
    assert "cannot read report file coverage.json" in doc["error"]["cause"]


def test_broken_integrity_is_not_ready(tmp_path: Path) -> None:
    report = _report(tmp_path, integrity=[{"status": "failed", "stream": "gw"}])
    assert compute_readiness(report, severities=_SEVERITIES)["verdict"] == NOT_READY


def test_coverage_gap_is_not_ready(tmp_path: Path) -> None:
    report = _report(tmp_path, coverage={"subjects": {"s": {"coverage_status": "gap"}}})
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["verdict"] == NOT_READY
    assert any("coverage" in r for r in verdict["reasons"])


def test_unknown_coverage_is_not_ready(tmp_path: Path) -> None:
    # No independent denominator reconciles the subject's records (SPEC §6.5, §13.3.4).
    report = _report(
        tmp_path, coverage={"subjects": {"s": {"coverage_status": "unknown"}}}
    )
    verdict = compute_readiness(report, severities=_SEVERITIES)
    assert verdict["verdict"] == NOT_READY
    assert verdict["reasons"] == [
        "coverage unknown for subject s: no independent denominator"
    ]


def test_ok_coverage_adds_no_coverage_reason(tmp_path: Path) -> None:
    report = _report(tmp_path, coverage={"subjects": {"s": {"coverage_status": "ok"}}})
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


@pytest.mark.parametrize("flag", ["--deviation", "--gap", "--catalog", "--js"])
def test_readiness_cli_refuses_an_abbreviated_flag(tmp_path: Path, flag: str) -> None:
    """No prefix matching: TS and Java refuse an abbreviated flag, so Python does too, rather than
    silently reading `--deviation` as `--deviations` (18.25 round 3)."""
    report = _report(tmp_path, integrity=[{"status": "verified", "stream": "a"}])
    register = tmp_path / "deviations.yaml"
    register.write_text("deviations: []\n", encoding="utf-8")
    gaps = tmp_path / "gaps.md"
    gaps.write_text("", encoding="utf-8")
    catalog = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
    # Each value is valid for the flag the abbreviation expands to, so only the refusal gives 3.
    extra = {
        "--deviation": [str(register)],
        "--gap": [str(gaps)],
        "--catalog": [str(catalog)],
    }
    assert cli.main(["readiness", str(report), flag, *extra.get(flag, [])]) == 3


@pytest.mark.parametrize(
    ("argv", "cause"),
    [
        (["--deviation", "x"], "unrecognized flag '--deviation'."),
        (["--gasp=x"], "unrecognized flag '--gasp=x'."),
        (["--gaps"], "flag '--gaps' needs a value."),
        (["--gaps", "--quiet"], "flag '--gaps' needs a value."),
        (["--quiet=1"], "flag '--quiet' takes no value."),
        (["second"], "unrecognized argument 'second'."),
    ],
)
def test_readiness_cli_usage_error_is_the_keyed_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str], cause: str
) -> None:
    """A readiness usage error is the keyed `--json` envelope TS and Java give, worded alike, not
    argparse's usage text (18.25 round 3; the 18.24 parity rule)."""
    report = _report(tmp_path, integrity=[{"status": "verified", "stream": "a"}])
    assert cli.main(["--json", "readiness", str(report), *argv]) == 3
    error = json.loads(capsys.readouterr().out)["error"]
    assert (error["key"], error["cause"]) == (
        "input.readiness_unrecognized_flag",
        cause,
    )


def test_an_unknown_flag_on_another_command_carries_that_command_s_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every command keys its usage errors with its own key, not readiness's (18.107)."""
    assert cli.main(["diff", "a.json", "b.json", "--bogus"]) == 3
    assert "error [input.diff_unrecognized_flag]: unrecognized flag '--bogus'." in (
        capsys.readouterr().err
    )


_RULE_CASES = json.loads(
    (
        _REPO_ROOT
        / "verification"
        / "gates"
        / "fixtures"
        / "report_file_rule"
        / "cases.json"
    ).read_text(encoding="utf-8")
)["cases"]


def rule_case_content(case: dict) -> str | None:
    """A shared report-file-rule case's content, ``@@`` expanded (cases.json's description)."""
    content = case["content"]
    repeat = case.get("repeat")
    if content is None or repeat is None:
        return content
    expansion = repeat["open"] * repeat["times"] + repeat["close"] * repeat["times"]
    return content.replace("@@", expansion)


@pytest.mark.parametrize("case", _RULE_CASES, ids=[c["id"] for c in _RULE_CASES])
def test_report_file_rule(tmp_path: Path, case: dict) -> None:
    # One report-file rule in every engine (18.184q): the same table drives readiness.test.ts and
    # ReadinessTest.java.
    report = _report(tmp_path)
    content = rule_case_content(case)
    if content is None:
        (report / case["file"]).unlink()
    else:
        (report / case["file"]).write_text(content, encoding="utf-8")
    assert (
        compute_readiness(report, severities=_SEVERITIES)["reasons"]
        == case["readiness"]
    )
