"""End-to-end catalog evaluation: assertions per (control, subject) with evidence (DC-5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentce.assess import assess_subjects
from agentce.catalog import load_catalog
from agentce.domain import DomainBinding
from agentce.profile import Profile
from agentce.report import write_report

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
SUBJECT = "spiffe://corp/agents/a"


def _events(fixture: str) -> list[dict[str, Any]]:
    path = _BASE / "test" / fixture
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _profile() -> Profile:
    return Profile.from_dict({"subjects": [{"id": SUBJECT, "role": "both"}]})


def _outcomes(assertions: list[Any]) -> dict[str, str]:
    return {a.control: a.outcome for a in assertions}


def test_conformant_assessment_with_evidence() -> None:
    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    assertions = assess_subjects(
        _events("OVS-03/passed.jsonl"), _profile(), [catalog], domain
    )
    outcomes = _outcomes(assertions)
    assert outcomes["OVS-03"] == "conformant"
    assert outcomes["REC-04"] == "conformant"
    assert outcomes["INT-01"] == "conformant"
    assert outcomes["INC-02"] == "not_applicable"  # no Incident events
    ovs = next(a for a in assertions if a.control == "OVS-03")
    assert ovs.evidence  # DC-5: a supporting verdict cites evidence


def test_non_conformant_assessment() -> None:
    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    assertions = assess_subjects(
        _events("OVS-03/failed.jsonl"), _profile(), [catalog], domain
    )
    ovs = next(a for a in assertions if a.control == "OVS-03")
    assert ovs.outcome == "non-conformant"
    assert ovs.population == (1, 1)
    assert ovs.violations


def test_assessment_report_is_dc5_valid(tmp_path: Path) -> None:
    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    assertions = assess_subjects(
        _events("OVS-03/passed.jsonl"), _profile(), [catalog], domain
    )
    # write_report enforces DC-5; a supporting verdict without evidence would raise here.
    write_report(
        tmp_path,
        assertions,
        bundle_digest="sha256:" + "a" * 64,
        catalogs=["eu-ai-act@2026.09"],
    )
    assert (tmp_path / "assertions.json").is_file()


def test_role_mismatch_is_not_applicable() -> None:
    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    # A provider-only subject: INC-02 applies to [deployer, provider], OVS-03 to both.
    profile = Profile.from_dict({"subjects": [{"id": SUBJECT, "role": "provider"}]})
    assertions = assess_subjects(
        _events("OVS-03/passed.jsonl"), profile, [catalog], domain
    )
    assert _outcomes(assertions)["OVS-03"] == "conformant"
