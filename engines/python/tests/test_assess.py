"""End-to-end catalog evaluation: assertions per (control, subject) with evidence (DC-5)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from agentce.store import GraphStore
from agentce.assess import (
    _reaches_human_actor_failure,
    apply_deviations,
    assess_subjects,
)
from agentce.psp import parse_shapes_ttl
from agentce.structural import property_reads
from agentce.catalog import load_catalog
from agentce.domain import DomainBinding
from agentce.profile import Profile
from agentce.report import write_report

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
_CONDUCT = _REPO_ROOT / "spec" / "catalogs" / "overlays" / "conduct"
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
        _events("OVS-03/passed.jsonl"),
        _profile(),
        [catalog],
        domain,
        withheld=(),
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
        _events("OVS-03/failed.jsonl"),
        _profile(),
        [catalog],
        domain,
        withheld=(),
    )
    ovs = next(a for a in assertions if a.control == "OVS-03")
    assert ovs.outcome == "non-conformant"
    assert ovs.population == (1, 1)
    assert ovs.violations


def test_assessment_report_is_dc5_valid(tmp_path: Path) -> None:
    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    assertions = assess_subjects(
        _events("OVS-03/passed.jsonl"),
        _profile(),
        [catalog],
        domain,
        withheld=(),
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
        _events("OVS-03/passed.jsonl"),
        _profile(),
        [catalog],
        domain,
        withheld=(),
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
        _events("OVS-03/passed.jsonl"),
        profile,
        [catalog],
        domain,
        withheld=(),
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
        _events("OVS-03/failed.jsonl"),
        _profile(),
        [catalog],
        domain,
        withheld=(),
    )
    return next(a for a in assertions if a.control == "OVS-03")


def test_apply_deviations_flips_non_conformant_to_partial_and_stamps_control_id() -> (
    None
):
    ovs = _non_conformant_ovs03()
    applied, expired, not_waivable = apply_deviations(
        [ovs], [_deviation()], as_of=ovs.window[1]
    )
    assert not_waivable == []
    assert expired == []
    assert applied[0].outcome == "partial"
    assert applied[0].deviation == "OVS-03"


def test_apply_deviations_leaves_conformant_and_insufficient_evidence_untouched() -> (
    None
):
    catalog = load_catalog(_BASE)
    domain = DomainBinding.load(_BASE / "test" / "domain.yaml")
    assertions = assess_subjects(
        _events("OVS-03/passed.jsonl"),
        _profile(),
        [catalog],
        domain,
        withheld=(),
    )
    conformant = next(a for a in assertions if a.control == "OVS-03")
    assert conformant.outcome == "conformant"
    insufficient = dataclasses.replace(conformant, outcome="insufficient_evidence")
    applied, expired, _ = apply_deviations(
        [conformant, insufficient], [_deviation()], as_of=conformant.window[1]
    )
    assert expired == []
    assert applied[0] == conformant
    assert applied[1] == insufficient


def test_apply_deviations_skips_and_reports_an_expired_entry() -> None:
    ovs = _non_conformant_ovs03()
    applied, expired, _ = apply_deviations(
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
    applied_empty_assertions, expired_empty_assertions, _ = apply_deviations(
        [], [_deviation()], as_of="2026-01-01"
    )
    assert applied_empty_assertions == []
    assert expired_empty_assertions == []
    applied_empty_deviations, expired_empty_deviations, _ = apply_deviations(
        [ovs], [], as_of="2026-01-01"
    )
    assert applied_empty_deviations == [ovs]
    assert expired_empty_deviations == []


# --- 18.186: the deviation register cannot waive the SPEC §10.4 human-actor rule. ---

_HUMAN_ACTOR = (
    _REPO_ROOT / "verification" / "gates" / "fixtures" / "deviations_human_actor"
)


def _human_actor_case(
    name: str, drop: tuple[str, ...] = ()
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    """The case's assertions by control for its fixture subject, and its register's entries."""
    from agentce.bundle import load_bundle
    from agentce.commands import _load_deviation_register
    from agentce.ingest import ingest

    case = _HUMAN_ACTOR / name
    accepted = [
        e for e in ingest(load_bundle(case / "bundle")).accepted if e["id"] not in drop
    ]
    catalogs = [
        load_catalog(_BASE),
        load_catalog(_CONDUCT),
    ]
    profile = Profile.from_dict(
        {"subjects": [{"id": "spiffe://corp/agents/cnd05-fixture", "role": "both"}]}
    )
    domain = DomainBinding.load(case / "domain.linkml.yaml")
    assertions = assess_subjects(accepted, profile, catalogs, domain, withheld=())
    return {a.control: a for a in assertions}, _load_deviation_register(
        case / "deviations.yaml"
    )


def test_a_failing_approval_is_not_waivable_on_the_controls_that_read_it() -> None:
    by_control, register = _human_actor_case("approval")
    readers = ("CND-02", "OVS-01", "OVS-08", "RSK-02")
    chosen = [by_control[c] for c in (*readers, "OVS-05")]
    applied, expired, not_waivable = apply_deviations(
        chosen, register, as_of=chosen[0].window[1]
    )
    assert expired == []
    assert not_waivable == list(readers)
    for a in applied[:4]:
        assert (a.outcome, a.deviation) == ("non-conformant", None), a.control
    # The decision's missing authorization is no §10.4 failure: the register still waives it.
    assert (applied[4].outcome, applied[4].deviation) == ("partial", "OVS-05")


def test_an_expired_entry_on_a_failing_approval_is_reported_not_waivable() -> None:
    by_control, register = _human_actor_case("approval")
    cnd02 = by_control["CND-02"]
    entry = {
        **next(d for d in register if d["control"] == "CND-02"),
        "expiry": "2020-01-01",
    }
    applied, expired, not_waivable = apply_deviations(
        [cnd02], [entry], as_of=cnd02.window[1]
    )
    assert (expired, not_waivable) == ([], ["CND-02"])
    assert applied[0].outcome == "non-conformant"


def test_a_decision_with_no_approval_is_waivable_not_not_waivable() -> None:
    by_control, register = _human_actor_case("approval", drop=("ap1",))
    cnd02 = by_control["CND-02"]
    assert cnd02.outcome == "non-conformant"
    assert not cnd02.human_actor_failed
    applied, _, not_waivable = apply_deviations(
        [cnd02], register, as_of=cnd02.window[1]
    )
    assert not_waivable == []
    assert (applied[0].outcome, applied[0].deviation) == ("partial", "CND-02")


def test_an_override_by_the_agent_itself_is_not_waivable_on_ovs07() -> None:
    by_control, register = _human_actor_case("override")
    ovs07 = by_control["OVS-07"]
    applied, _, not_waivable = apply_deviations(
        [ovs07], register, as_of=ovs07.window[1]
    )
    assert not_waivable == ["OVS-07"]
    assert (applied[0].outcome, applied[0].deviation) == ("non-conformant", None)


def test_not_waivable_is_per_assertion_across_subjects() -> None:
    by_control, register = _human_actor_case("approval")
    failing = by_control["CND-02"]
    clean = dataclasses.replace(
        failing, subject="spiffe://corp/agents/other", human_actor_failed=False
    )
    applied, _, not_waivable = apply_deviations(
        [failing, clean], register, as_of=failing.window[1]
    )
    assert not_waivable == ["CND-02"]
    assert [a.outcome for a in applied] == ["non-conformant", "partial"]


def test_not_waivable_reads_match_by_predicate() -> None:
    with GraphStore() as store:
        store.human_actor_failed.add("d1")
        for reads in (
            ["agentce:approvedBy"],
            ["agentce:decidedBy", "^agentce:reviewedBy"],
            ["agentce:x", "agentce:interventionByHuman"],
        ):
            assert _reaches_human_actor_failure(store, "d1", reads)
        assert not _reaches_human_actor_failure(
            store, "d1", ["agentce:approvedByProxy"]
        )
        assert not _reaches_human_actor_failure(store, "d1", ["agentce:authorizedBy"])
        assert not _reaches_human_actor_failure(store, "d2", ["agentce:approvedBy"])


def test_not_waivable_when_the_shape_reaches_the_failing_decision_from_another_focus() -> (
    None
):
    """Third-party shapes on ToolCall that reach the failing decision (verifier 18.186 r1, and nested shapes)."""
    with GraphStore() as store:
        store.human_actor_failed.add("d1")
        store.add_edge("tc1", "agentce:executes", "d1")
        store.add_edge("tc2", "agentce:executes", "d2")
        store.add_edge("a1", "agentce:approves", "d1")
        # Two hops on one predicate, given once (property_reads dedupes): the walk repeats it.
        store.add_edge("i3", "agentce:derivedFrom", "i2")
        store.add_edge("i2", "agentce:derivedFrom", "d1")
        assert _reaches_human_actor_failure(
            store, "i3", ["agentce:derivedFrom", "agentce:approvedBy"]
        )
        assert _reaches_human_actor_failure(
            store, "tc1", ["agentce:executes", "agentce:approvedBy"]
        )
        assert _reaches_human_actor_failure(
            store, "tc1", ["agentce:executes", "agentce:reviewedBy"]
        )
        assert _reaches_human_actor_failure(
            store, "a1", ["agentce:approves", "^agentce:executes", "agentce:approvedBy"]
        )
        assert not _reaches_human_actor_failure(
            store, "tc2", ["agentce:executes", "agentce:approvedBy"]
        )
        assert not _reaches_human_actor_failure(
            store, "tc1", ["agentce:executes", "agentce:authorizedBy"]
        )


def test_not_waivable_property_reads_include_nested_shapes() -> None:
    shapes = parse_shapes_ttl(
        """@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .
agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;
  sh:property [ sh:path agentce:executes ; sh:node agentce:N ] ;
  sh:property [ sh:path ( agentce:executes [ sh:inversePath agentce:about ] ) ; sh:equals agentce:x ] ;
  sh:property [ sh:path agentce:calls ; sh:qualifiedValueShape agentce:Q ; sh:qualifiedMinCount 1 ] .
agentce:N a sh:NodeShape ; sh:property [ sh:path agentce:approvedBy ; sh:node agentce:S ] .
agentce:Q a sh:NodeShape ; sh:property [ sh:path agentce:reviewedBy ; sh:minCount 1 ] .
"""
    )
    shape = next(s for s in shapes.values() if s.iri.endswith("#S"))
    assert property_reads(shape, shapes) == {
        # N's sh:node back to S is followed once, so S's own predicates join through the cycle.
        "agentce:executes": [
            "agentce:executes",
            "agentce:approvedBy",
            "^agentce:about",
            "agentce:x",
            "agentce:calls",
            "agentce:reviewedBy",
        ],
        "(agentce:executes ^agentce:about)": [
            "agentce:executes",
            "^agentce:about",
            "agentce:x",
        ],
        "agentce:calls": ["agentce:calls", "agentce:reviewedBy"],
    }


_ROB02 = _REPO_ROOT / "verification" / "gates" / "fixtures" / "rob02_untrusted"


def _assess_rob02(events: list[Any], withheld: Any = ()) -> Any:
    profile = Profile.from_dict(
        {"subjects": [{"id": "spiffe://corp/agents/rob02-fixture", "role": "both"}]}
    )
    catalog = load_catalog(_REPO_ROOT / "spec" / "catalogs" / "base" / "baseline")
    domain = DomainBinding.load(_ROB02 / "domain.linkml.yaml")
    assertions = assess_subjects(events, profile, [catalog], domain, withheld=withheld)
    return next(a for a in assertions if a.control == "ROB-02")


def _rob02(case: str) -> Any:
    cases = json.loads((_ROB02 / "cases.json").read_text(encoding="utf-8"))["cases"]
    return _assess_rob02(cases[case]["events"])


def test_a_decision_the_guard_never_ruled_on_reads_insufficient_evidence() -> None:
    rob02 = _rob02("input-unguarded-record")
    assert rob02.outcome == "insufficient_evidence"
    assert rob02.population == (1, 0)
    assert _rob02("input-guarded-record").outcome == "conformant"


def test_a_tainted_decision_beats_an_unruled_one() -> None:
    assert _rob02("tainted-and-unguarded").outcome == "non-conformant"


_QUARANTINE = _REPO_ROOT / "verification" / "gates" / "fixtures" / "quarantine_no_pass"
_CLASSES = ("enforcement_point", "independent_system", "self_report")


def _quarantine_rob02(make_bundle: Any, case: str) -> Any:
    from agentce.bundle import load_bundle
    from agentce.ingest import ingest

    cases = json.loads((_QUARANTINE / "cases.json").read_text(encoding="utf-8"))[
        "cases"
    ]
    bundle = make_bundle(
        cases[case]["events"],
        source_classes={f"urn:src:{cls}": cls for cls in _CLASSES},
    )
    ingested = ingest(load_bundle(bundle))
    return _assess_rob02(ingested.accepted, ingested.withheld)


def test_a_late_record_that_reads_non_conformant_is_marked_for_the_exit_code(
    make_bundle: Any,
) -> None:
    late = _quarantine_rob02(
        make_bundle, "rob02-untrusted-write-quarantined-time-order"
    )
    assert late.outcome == "insufficient_evidence"
    assert late.withheld_non_conformant is True
    # Run state for the exit code only: assertions.json keeps its shape.
    assert "withheld_non_conformant" not in late.to_json()
    benign = _quarantine_rob02(make_bundle, "rob02-benign-time-order")
    assert benign.outcome == "conformant"
    assert benign.withheld_non_conformant is False
