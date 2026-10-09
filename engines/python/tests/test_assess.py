"""End-to-end catalog evaluation: assertions per (control, subject) with evidence (DC-5)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from agentce.assess import apply_deviations, assess_subjects
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
        catalogs=[catalog],
    )
    assert (tmp_path / "assertions.json").is_file()


def test_crosswalk_is_carried_from_the_control_and_never_verified() -> None:
    """SPEC §7.3: the assertion carries each control's own crosswalk entries, ``verified`` taken
    from ``verified_against_text`` -- and the engine never sets ``verified`` true itself."""
    import yaml

    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    assertions = assess_subjects(
        _events("OVS-03/passed.jsonl"), _profile(), [catalog], domain
    )
    doc01 = next(a for a in assertions if a.control == "DOC-01")
    source = yaml.safe_load(
        (_BASE / "controls" / "DOC-01.yaml").read_text(encoding="utf-8")
    )
    source_entry = source["crosswalk"][0]
    assert doc01.crosswalk
    entry = doc01.crosswalk[0]
    assert entry["framework"] == source_entry["framework"]
    assert entry["clause"] == source_entry["clause"]
    assert entry["verified"] is source_entry["verified_against_text"]
    assert entry["verified"] is False
    assert all(e.get("verified") is not True for a in assertions for e in a.crosswalk)


def test_role_mismatch_is_not_applicable() -> None:
    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    # A provider-only subject: INC-02 applies to [deployer, provider], OVS-03 to both.
    profile = Profile.from_dict({"subjects": [{"id": SUBJECT, "role": "provider"}]})
    assertions = assess_subjects(
        _events("OVS-03/passed.jsonl"), profile, [catalog], domain
    )
    assert _outcomes(assertions)["OVS-03"] == "conformant"


# --- 18.17 C1: `apply_deviations` (contracts/P18-18.17.md). ---


def _deviation(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "control": "OVS-03",
        "rationale": "compensated",
        "compensating_control": "manual review",
        "owner": "alice",
        "approver": "bob",
        "granted": "2026-01-01",
        "expiry": "2027-01-01",
    }
    base.update(over)
    return base


def _non_conformant_ovs03() -> Any:
    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    assertions = assess_subjects(
        _events("OVS-03/failed.jsonl"), _profile(), [catalog], domain
    )
    return next(a for a in assertions if a.control == "OVS-03")


def test_apply_deviations_flips_non_conformant_to_partial_and_stamps_control_id() -> (
    None
):
    ovs = _non_conformant_ovs03()
    applied, expired = apply_deviations([ovs], [_deviation()], as_of=ovs.window[1])
    assert expired == []
    assert applied[0].outcome == "partial"
    assert applied[0].deviation == "OVS-03"


def test_apply_deviations_leaves_conformant_and_insufficient_evidence_untouched() -> (
    None
):
    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    assertions = assess_subjects(
        _events("OVS-03/passed.jsonl"), _profile(), [catalog], domain
    )
    conformant = next(a for a in assertions if a.control == "OVS-03")
    assert conformant.outcome == "conformant"
    insufficient = dataclasses.replace(conformant, outcome="insufficient_evidence")
    applied, expired = apply_deviations(
        [conformant, insufficient], [_deviation()], as_of=conformant.window[1]
    )
    assert expired == []
    assert applied[0] == conformant
    assert applied[1] == insufficient


def test_apply_deviations_skips_and_reports_an_expired_entry() -> None:
    ovs = _non_conformant_ovs03()
    applied, expired = apply_deviations(
        [ovs], [_deviation(expiry="2020-01-01")], as_of=ovs.window[1]
    )
    assert expired == ["OVS-03"]
    assert applied[0].outcome == "non-conformant"
    assert applied[0].deviation is None


def test_apply_deviations_is_pure() -> None:
    ovs = _non_conformant_ovs03()
    before = dataclasses.replace(ovs)
    apply_deviations([ovs], [_deviation()], as_of=ovs.window[1])
    assert ovs == before


def test_apply_deviations_of_empty_inputs_is_a_no_op() -> None:
    ovs = _non_conformant_ovs03()
    applied_empty_assertions, expired_empty_assertions = apply_deviations(
        [], [_deviation()], as_of="2026-01-01"
    )
    assert applied_empty_assertions == []
    assert expired_empty_assertions == []
    applied_empty_deviations, expired_empty_deviations = apply_deviations(
        [ovs], [], as_of="2026-01-01"
    )
    assert applied_empty_deviations == [ovs]
    assert expired_empty_deviations == []


_ROB02 = _REPO_ROOT / "verification" / "gates" / "fixtures" / "rob02_untrusted"


def _rob02(case: str) -> Any:
    cases = json.loads((_ROB02 / "cases.json").read_text(encoding="utf-8"))["cases"]
    profile = Profile.from_dict(
        {"subjects": [{"id": "spiffe://corp/agents/rob02-fixture", "role": "both"}]}
    )
    catalog = load_catalog(_REPO_ROOT / "spec" / "catalogs" / "base" / "baseline")
    domain = DomainBinding.load(_ROB02 / "domain.linkml.yaml")
    assertions = assess_subjects(cases[case]["events"], profile, [catalog], domain)
    return next(a for a in assertions if a.control == "ROB-02")


def test_a_decision_the_guard_never_ruled_on_reads_insufficient_evidence() -> None:
    rob02 = _rob02("input-unguarded-record")
    assert rob02.outcome == "insufficient_evidence"
    assert rob02.population == (1, 0)
    assert _rob02("input-guarded-record").outcome == "conformant"


def test_a_tainted_decision_beats_an_unruled_one() -> None:
    assert _rob02("tainted-and-unguarded").outcome == "non-conformant"
