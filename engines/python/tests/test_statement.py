"""Tests for the public conformance statement and role-aware evidence packs (SPEC §9.5, §9.4)."""

from __future__ import annotations

import json
from pathlib import Path

from agentce import cli
from agentce.assertions import Assertion, EvidencePointer
from agentce.report import render_evidence_pack, render_public_statement


def _assertion(
    control: str, outcome: str, *, subject: str = "s", evidence: list[str] | None = None
) -> Assertion:
    return Assertion(
        control=control,
        control_version="2026.09",
        subject=subject,
        outcome=outcome,
        rung=2,
        mode="automated",
        window=("2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"),
        population=(1, 0 if outcome == "conformant" else 1),
        evidence=[
            EvidencePointer(ref=r, digest="sha256:ab", source_class="enforcement_point")
            for r in (evidence or [])
        ],
    )


def test_public_statement_has_scope_outcomes_and_disclaimer() -> None:
    text = render_public_statement(
        [_assertion("OVS-03", "conformant"), _assertion("REC-04", "non-conformant")],
        catalogs=["eu-ai-act@2026.09"],
        statement_date="2026-09-16",
    )
    assert "spiffe" not in text  # subject id is opaque; still, scope names it
    assert "## Scope" in text and "eu-ai-act@2026.09" in text and "2026-09-16" in text
    assert "OVS" in text and "REC" in text
    assert "not a legal compliance determination" in text


def test_public_statement_conduct_line_only_with_cnd() -> None:
    without = render_public_statement([_assertion("OVS-03", "conformant")])
    assert "## Conduct" not in without
    with_cnd = render_public_statement([_assertion("CND-01", "conformant")])
    assert "## Conduct" in with_cnd and "authorised instructions" in with_cnd


def test_public_statement_lists_accepted_deviations() -> None:
    text = render_public_statement(
        [_assertion("OVS-03", "non-conformant")], deviations=["OVS-03"]
    )
    assert "OVS-03" in text.split("## Accepted deviations", 1)[1]


def test_evidence_pack_role_variant_is_complete() -> None:
    assertions = [_assertion("OVS-03", "conformant", evidence=["agentce:event/tc1"])]
    pack = render_evidence_pack("s", assertions, role="provider")
    assert pack["role"] == "provider"
    assert pack["assertions"][0]["evidence"] == ["agentce:event/tc1"]
    assert pack["evidence"] == ["agentce:event/tc1"]


def test_assertion_from_json_round_trips() -> None:
    original = _assertion("OVS-03", "non-conformant", evidence=["agentce:event/x"])
    assert Assertion.from_json(original.to_json()).to_json() == original.to_json()


def test_report_from_public_via_cli(tmp_path: Path, capsys) -> None:
    src = tmp_path / "assertions.json"
    src.write_text(
        json.dumps([_assertion("CND-01", "conformant").to_json()]), encoding="utf-8"
    )
    code = cli.main(["report", "--from", str(src), "--format", "public", "--json"])
    assert code == 0
    rendering = json.loads(capsys.readouterr().out)["rendering"]
    assert "Public conformance statement" in rendering and "## Conduct" in rendering


def test_report_from_pack_via_cli(tmp_path: Path, capsys) -> None:
    src = tmp_path / "assertions.json"
    src.write_text(
        json.dumps([_assertion("OVS-03", "conformant").to_json()]), encoding="utf-8"
    )
    code = cli.main(
        [
            "report",
            "--from",
            str(src),
            "--format",
            "pack",
            "--role",
            "deployer",
            "--json",
        ]
    )
    assert code == 0
    packs = json.loads(json.loads(capsys.readouterr().out)["rendering"])
    assert packs["s"]["role"] == "deployer"
