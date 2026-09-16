"""Tests for the agentce-check-report skill scripts (SPEC §13.3.4)."""

from __future__ import annotations

import json
from pathlib import Path

import checklist_lint
import claim_check
import deviation_lint
import read_outcomes
import report_readiness
import yaml
from _common import FINDINGS, OK


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
