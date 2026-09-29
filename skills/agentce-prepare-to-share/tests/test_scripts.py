"""Tests for the agentce-prepare-to-share skill scripts (SPEC §13.3.4)."""

from __future__ import annotations

import json
from pathlib import Path

import checklist_lint
import claim_check
import deviation_lint
import read_outcomes
import report_readiness
import yaml
from _common import _SKILL_MD, FINDINGS, OK, read_frontmatter


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


def _unquoted_date_deviations(tmp_path: Path) -> Path:
    """A deviation register whose `granted`/`expiry` are unquoted YAML dates (parsed as `date`
    objects, not `str`), for the round-2 regression tests: the engine's own `agentce assess
    --deviations` accepts this shape via its normalizing loader (SPEC §13.3.4), so both skill scripts
    must accept it too."""
    dev = tmp_path / "dev.yaml"
    dev.write_text(
        "deviations:\n"
        "  - control: OVS-03\n"
        '    rationale: "x"\n'
        '    compensating_control: "y"\n'
        '    owner: "a"\n'
        '    approver: "b"\n'
        "    granted: 2026-01-01\n"  # unquoted -- a YAML date, not a str
        "    expiry: 2026-03-01\n",  # unquoted -- a YAML date, not a str
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


def test_skill_is_for_the_coding_assistant_and_never_signs() -> None:
    front = read_frontmatter()
    assert front["name"] == "agentce-prepare-to-share"
    assert "never for the agent being checked" in " ".join(front["description"].split())
    skill = _SKILL_MD.read_text(encoding="utf-8")
    assert "and never signs anything." in " ".join(skill.split())
