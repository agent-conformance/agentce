"""Tests for the agentce-prepare-to-share skill scripts (SPEC §13.3.4)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import agentce
import checklist_lint
import claim_check
import deviation_lint
import read_outcomes
import report_readiness
import yaml
from _common import (
    _SKILL_MD,
    FINDINGS,
    INPUT_ERROR,
    OK,
    VERSION_MISMATCH,
    Finding,
    check_pins,
    read_frontmatter,
    run_guarded,
)
from agentce.commands import PRESET_EMIT

SKILL_ROOT = _SKILL_MD.parent


def _report(
    tmp_path: Path, assertions: list[dict], *, integrity: list[dict] | None = None
) -> Path:
    out = tmp_path / "report"
    out.mkdir()
    (out / "assertions.json").write_text(json.dumps(assertions), encoding="utf-8")
    (out / "integrity.jsonl").write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in (integrity or [{"status": "verified", "stream": "a"}])
        ),
        encoding="utf-8",
    )
    (out / "coverage.json").write_text(json.dumps({"subjects": {}}), encoding="utf-8")
    (out / "applicability.jsonl").write_text("", encoding="utf-8")
    return out


def _deviations_yaml_text(*, granted: str, expiry: str) -> str:
    """The shared OVS-03 deviation-register body `_unquoted_date_deviations` and
    `_calendar_invalid_deviations` both write, differing only in the literal `granted`/`expiry`
    values they splice in unquoted."""
    return (
        "deviations:\n"
        "  - control: OVS-03\n"
        '    rationale: "x"\n'
        '    compensating_control: "y"\n'
        '    owner: "a"\n'
        '    approver: "b"\n'
        f"    granted: {granted}\n"
        f"    expiry: {expiry}\n"
    )


def _unquoted_date_deviations(tmp_path: Path) -> Path:
    """A deviation register whose `granted`/`expiry` are unquoted YAML dates (parsed as `date`
    objects, not `str`), for the round-2 regression tests: the engine's own `agentce assess
    --deviations` accepts this shape via its normalizing loader (SPEC §13.3.4), so both skill scripts
    must accept it too."""
    dev = tmp_path / "dev.yaml"
    dev.write_text(
        _deviations_yaml_text(
            granted="2026-01-01", expiry="2026-03-01"
        ),  # both unquoted
        encoding="utf-8",
    )
    return dev


def test_report_readiness_ready(tmp_path: Path, capsys) -> None:
    report = _report(
        tmp_path, [{"control": "OVS-03", "outcome": "conformant", "subject": "s"}]
    )
    assert report_readiness.body(["--report", str(report), "--json"]) == OK
    assert json.loads(capsys.readouterr().out)["verdict"] == "READY"


def test_report_readiness_not_ready_on_integrity(tmp_path: Path, capsys) -> None:
    report = _report(tmp_path, [], integrity=[{"status": "failed", "stream": "gw"}])
    assert report_readiness.body(["--report", str(report), "--json"]) == FINDINGS
    assert json.loads(capsys.readouterr().out)["verdict"] == "NOT READY"


def test_report_readiness_accepts_an_unquoted_yaml_expiry_the_same_way_assess_does(
    tmp_path: Path, capsys
) -> None:
    """`compute_readiness` calls `deviation_lint` too, so the same regression as
    `deviation_lint.py`'s applies here: an unquoted (YAML date-typed, not `str`) `expiry` on an
    already-applied deviation must not be misreported as an invalid deviation, when the fresh
    `assess --deviations` run that produced this exact report already applied it and found it valid
    (18.17 round 2 finding (c))."""
    report = _report(
        tmp_path,
        [
            {
                "control": "OVS-03",
                "outcome": "partial",
                "subject": "s",
                "deviation": "OVS-03",
                "window": {"end": "2026-02-01T00:00:00Z"},
            }
        ],
    )
    dev = _unquoted_date_deviations(tmp_path)
    code = report_readiness.body(
        ["--report", str(report), "--deviations", str(dev), "--json"]
    )
    verdict = json.loads(capsys.readouterr().out)
    assert code == OK
    assert verdict["verdict"] != "NOT READY"
    assert verdict["reasons"] == []


def test_deviation_lint_refuses_insufficient(tmp_path: Path, capsys) -> None:
    report = _report(
        tmp_path,
        [{"control": "OVS-03", "outcome": "insufficient_evidence", "subject": "s"}],
    )
    dev = tmp_path / "dev.yaml"
    dev.write_text(
        yaml.safe_dump(
            {
                "deviations": [
                    {
                        "control": "OVS-03",
                        "rationale": "x",
                        "compensating_control": "y",
                        "owner": "a",
                        "approver": "b",
                        "granted": "2026-01-01",
                        "expiry": "2026-03-01",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code = deviation_lint.body(
        ["--deviations", str(dev), "--report", str(report), "--json"]
    )
    assert code == FINDINGS
    assert any(
        "insufficient_evidence" in p
        for p in json.loads(capsys.readouterr().out)["problems"]
    )


def test_deviation_lint_accepts_a_report_whose_deviation_is_already_applied(
    tmp_path: Path, capsys
) -> None:
    """The finding-1 regression on the skill's own path: a fresh `assess --deviations` run already
    flips `outcome` to `partial` and stamps `deviation`, so a re-lint against that same report must
    not re-reject it for "got partial" (18.17 round 2 B3: this script had its own copy of the bug)."""
    report = _report(
        tmp_path,
        [
            {
                "control": "OVS-03",
                "outcome": "partial",
                "subject": "s",
                "deviation": "OVS-03",
            }
        ],
    )
    dev = tmp_path / "dev.yaml"
    dev.write_text(
        yaml.safe_dump(
            {
                "deviations": [
                    {
                        "control": "OVS-03",
                        "rationale": "x",
                        "compensating_control": "y",
                        "owner": "a",
                        "approver": "b",
                        "granted": "2026-01-01",
                        "expiry": "2026-03-01",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code = deviation_lint.body(
        ["--deviations", str(dev), "--report", str(report), "--json"]
    )
    assert code == OK
    assert json.loads(capsys.readouterr().out)["problems"] == []


def test_deviation_lint_accepts_an_unquoted_yaml_date_the_same_way_the_engine_does(
    tmp_path: Path, capsys
) -> None:
    """The skill's own bare `yaml.safe_load` used to reach `readiness.deviation_lint` unnormalized,
    so an unquoted (YAML date-typed, not `str`) `granted`/`expiry` -- which `agentce assess
    --deviations` accepts via its own normalizing loader -- was misreported here as `not a valid
    ISO-8601 date` (18.17 round 2 finding (c))."""
    report = _report(
        tmp_path,
        [{"control": "OVS-03", "outcome": "non-conformant", "subject": "s"}],
    )
    dev = _unquoted_date_deviations(tmp_path)
    code = deviation_lint.body(
        ["--deviations", str(dev), "--report", str(report), "--json"]
    )
    assert code == OK
    assert json.loads(capsys.readouterr().out)["problems"] == []


def _calendar_invalid_deviations(tmp_path: Path, name: str = "dev.yaml") -> Path:
    """A deviation register whose unquoted `expiry` is YAML-shape-valid but calendar-invalid
    (`2026-02-30`) -- previously an uncaught `ValueError` on both scripts' bare `yaml.safe_load`
    (18.17c)."""
    dev = tmp_path / name
    dev.write_text(
        _deviations_yaml_text(granted='"2026-01-01"', expiry="2026-02-30"),
        encoding="utf-8",
    )
    return dev


def test_deviation_lint_flags_a_calendar_invalid_unquoted_expiry_as_a_problem(
    tmp_path: Path, capsys
) -> None:
    """A calendar-invalid unquoted expiry (`2026-02-30`) no longer crashes the YAML load (18.17c's
    permissive timestamp loader); it reaches `readiness.deviation_lint`'s own not-a-valid-RFC-3339-
    date check exactly like an already-string-typed garbage value does, reported as a problem
    (FINDINGS, exit 1), never a raw exception."""
    report = _report(
        tmp_path,
        [{"control": "OVS-03", "outcome": "non-conformant", "subject": "s"}],
    )
    dev = _calendar_invalid_deviations(tmp_path)
    code = deviation_lint.body(
        ["--deviations", str(dev), "--report", str(report), "--json"]
    )
    assert code == FINDINGS
    problems = json.loads(capsys.readouterr().out)["problems"]
    assert any("not a valid RFC 3339 date" in p and "2026-02-30" in p for p in problems)


def test_report_readiness_gives_not_ready_for_a_calendar_invalid_unquoted_expiry(
    tmp_path: Path, capsys
) -> None:
    """Mirrors `deviation_lint`'s own handling: `compute_readiness` calls `deviation_lint` too, so
    the same calendar-invalid expiry gives NOT READY (exit 1, a verdict), never a crash or an
    input_error (18.17c)."""
    report = _report(
        tmp_path,
        [{"control": "OVS-03", "outcome": "conformant", "subject": "s"}],
    )
    dev = _calendar_invalid_deviations(tmp_path)
    code = report_readiness.body(
        ["--report", str(report), "--deviations", str(dev), "--json"]
    )
    assert code == FINDINGS
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["verdict"] == "NOT READY"


def test_deviation_lint_refuses_a_shape_malformed_register_as_input_error(
    tmp_path: Path, capsys
) -> None:
    """A shape-malformed register (`deviations` a scalar, or a list of scalars) used to crash
    both scripts with a raw `AttributeError`/`TypeError` -- neither script validated the register's
    shape before indexing into it (18.17c)."""
    report = _report(
        tmp_path,
        [{"control": "OVS-03", "outcome": "non-conformant", "subject": "s"}],
    )
    for name, body in [
        ("a.yaml", "deviations: ['a']\n"),
        ("b.yaml", "deviations: 5\n"),
    ]:
        dev = tmp_path / name
        dev.write_text(body, encoding="utf-8")
        code = deviation_lint.body(
            ["--deviations", str(dev), "--report", str(report), "--json"]
        )
        assert code == INPUT_ERROR
        assert json.loads(capsys.readouterr().out)["status"] == "input_error"


def test_report_readiness_refuses_a_shape_malformed_register_as_input_error(
    tmp_path: Path, capsys
) -> None:
    report = _report(
        tmp_path,
        [{"control": "OVS-03", "outcome": "conformant", "subject": "s"}],
    )
    for name, body in [
        ("a.yaml", "deviations: ['a']\n"),
        ("b.yaml", "deviations: 5\n"),
    ]:
        dev = tmp_path / name
        dev.write_text(body, encoding="utf-8")
        code = report_readiness.body(
            ["--report", str(report), "--deviations", str(dev), "--json"]
        )
        assert code == INPUT_ERROR
        assert json.loads(capsys.readouterr().out)["status"] == "input_error"


def test_checklist_lint_valid(tmp_path: Path, capsys) -> None:
    rec = tmp_path / "OVS-09.yaml"
    rec.write_text(
        yaml.safe_dump(
            {
                "checklist": "OVS-09-M",
                "control": "OVS-09",
                "items": [
                    {
                        "id": "Q1",
                        "artifact": "log",
                        "question": "ok?",
                        "answers": ["yes", "no"],
                    }
                ],
                "completed_by": {"principal": "a@x", "org": "X", "independent": True},
            }
        ),
        encoding="utf-8",
    )
    assert checklist_lint.body(["--records", str(rec), "--json"]) == OK


def test_claim_check_matches(tmp_path: Path, capsys) -> None:
    window = {"start": "2026-01-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"}
    claim = tmp_path / "claim.json"
    claim.write_text(
        json.dumps(
            {
                "subjects": [{"id": "s"}],
                "observation_window": window,
                "catalogs": [{"id": "eu-ai-act", "version": "2026.09"}],
            }
        ),
        encoding="utf-8",
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        yaml.safe_dump(
            {
                "subjects": [{"id": "s"}],
                "observation_window": window,
                "catalogs": ["eu-ai-act@2026.09"],
            }
        ),
        encoding="utf-8",
    )
    assert (
        claim_check.body(["--claim", str(claim), "--profile", str(profile), "--json"])
        == OK
    )


def test_read_outcomes_refuses_composite(tmp_path: Path, capsys) -> None:
    report = _report(
        tmp_path, [{"control": "OVS-03", "outcome": "conformant", "subject": "s"}]
    )
    code = read_outcomes.body(["--report", str(report), "--score", "--json"])
    assert code == FINDINGS
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "refused"
    assert "composite score" in payload["reason"]


def test_read_outcomes_summarises(tmp_path: Path, capsys) -> None:
    report = _report(
        tmp_path,
        [
            {"control": "OVS-03", "outcome": "conformant", "subject": "s"},
            {"control": "OVS-01", "outcome": "insufficient_evidence", "subject": "s"},
        ],
    )
    assert read_outcomes.body(["--report", str(report), "--json"]) == OK
    families = json.loads(capsys.readouterr().out)["families"]
    assert families["OVS"]["conformant"] == 1
    assert families["OVS"]["insufficient_evidence"] == 1


def test_pins_gate_runs(tmp_path: Path, capsys) -> None:
    # run_guarded passes the pin gate against the installed engine, then runs the body.
    report = _report(
        tmp_path, [{"control": "OVS-03", "outcome": "conformant", "subject": "s"}]
    )
    from _common import run_guarded

    assert run_guarded(["--report", str(report), "--json"], report_readiness.body) == OK


def test_pins_match_the_installed_engine() -> None:
    # The skill's SKILL.md pins must match the engine it ships against, or every script would exit 3.
    assert check_pins() is None


def test_version_mismatch_gate_returns_exit_3(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # If SKILL.md pins do not match the engine, every script stops with exit 3 (S-5).
    monkeypatch.setattr(
        "_common.check_pins",
        lambda: Finding("skill.spec_version_mismatch", "pinned 9.9 != engine"),
    )
    assert run_guarded(["--json"], lambda _argv: 0) == VERSION_MISMATCH
    # When the pins hold, the body runs and its exit code is returned.
    monkeypatch.setattr("_common.check_pins", lambda: None)
    assert run_guarded([], lambda _argv: 7) == 7


def test_check_pins_catches_a_real_cli_version_mismatch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Unlike the two tests above (which monkeypatch check_pins itself to test run_guarded's
    # dispatch), this triggers check_pins()'s own detection logic against a real engine mismatch.
    monkeypatch.setattr(agentce, "__version__", "0.0.5")
    finding = check_pins()
    assert finding is not None
    assert finding.code == "skill.cli_version_mismatch"


def test_check_pins_catches_a_real_spec_version_mismatch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(agentce, "SPEC_VERSION", "9.9")
    finding = check_pins()
    assert finding is not None
    assert finding.code == "skill.spec_version_mismatch"


def test_claim_check_exits_3_as_a_subprocess_on_a_stale_pinned_skill_copy(
    tmp_path: Path,
) -> None:
    # No existing test runs a script the way an adopter does: as a subprocess against an installed
    # skill tree. Copy the skill, plant the stale pin the SKILL.md prose used to claim, and confirm
    # claim_check.py exits 3 with the version_mismatch JSON shape before it even looks at --claim.
    copy = tmp_path / "skill"
    shutil.copytree(
        SKILL_ROOT,
        copy,
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", ".mypy_cache", ".ruff_cache"
        ),
    )
    skill_md = copy / "SKILL.md"
    original = skill_md.read_text(encoding="utf-8")
    stale = original.replace(
        'cli_version: ">=0.1.0,<0.2"', 'cli_version: ">=0.0.1,<0.1"'
    )
    assert stale != original, (
        "the frontmatter's cli_version pin text no longer matches the literal this"
        " test replaces -- update the replaced string, or this test silently stops"
        " planting a stale pin"
    )
    skill_md.write_text(stale, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/claim_check.py", "--json"],
        cwd=copy,
        capture_output=True,
        text=True,
    )
    assert result.returncode == VERSION_MISMATCH
    payload = json.loads(result.stdout)
    assert payload["status"] == "version_mismatch"
    assert payload["finding"]["code"] == "skill.cli_version_mismatch"


def test_version_pins_prose_matches_the_frontmatter() -> None:
    # A future bump to spec/CLI/catalog in the frontmatter without updating the prose sentence
    # should fail here rather than leaving a reader looking at a stale enforced range.
    skill = _SKILL_MD.read_text(encoding="utf-8")
    match = re.search(r"spec `([^`]+)`, CLI `([^`]+)`, catalog `([^`]+)`", skill)
    assert match is not None, "no 'Version pins' prose line found in SKILL.md"
    prose_spec, prose_cli, prose_catalog = match.groups()
    metadata = read_frontmatter()["metadata"]
    assert prose_spec == metadata["spec_version"]
    assert prose_cli == metadata["cli_version"]
    assert [prose_catalog] == metadata["catalog_versions"]


def test_the_named_for_presets_exist_in_the_engine() -> None:
    # The "preparing for an audience" paragraph names every --for preset; a future rename, removal,
    # or addition to PRESET_EMIT should fail this test rather than leave the doc silently wrong.
    skill = _SKILL_MD.read_text(encoding="utf-8")
    named = set(re.findall(r"`--for (\w[\w-]*)`", skill))
    assert named == set(PRESET_EMIT)
    normalized = " ".join(skill.split())
    assert (
        "`--for auditor` and `--for buyer` are the two presets built for a"
        " hand-over to someone outside the team"
    ) in normalized


def test_skill_is_for_the_coding_assistant_and_never_signs() -> None:
    front = read_frontmatter()
    assert front["name"] == "agentce-prepare-to-share"
    assert "never for the agent being checked" in " ".join(front["description"].split())
    skill = _SKILL_MD.read_text(encoding="utf-8")
    assert "and never signs anything." in " ".join(skill.split())
