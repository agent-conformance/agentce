"""compute_buyer_view: the questionnaire-answer selection of already-computed assertions (18.18).
The CLI-level `--for buyer` wiring, render functions, and build gate are C2/C3's work
(verification/gates/buyer_view.sh); this file proves this session's real compute-layer code against
real Assertion objects, including schema validation, the `blind_spots`-derived gap step, and a real
run against the shipped default baseline lens (closing critic B7/N1)."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from agentce import cli
from agentce.assertions import Assertion, EvidencePointer, aggregate
from agentce.buyer_view import compute_buyer_view
from agentce.report import render_buyer_html, render_buyer_md

_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
_SCHEMA = json.loads(
    (_REPO_ROOT / "spec/report/buyer.schema.json").read_text(encoding="utf-8")
)
_EMPTY_BLIND_SPOTS: dict[str, Any] = {"blind_spots": [], "no_population": []}


def _assertion(
    control: str,
    subject: str = "spiffe://corp/agents/a",
    *,
    outcome: str = "conformant",
    mode: str = "automated",
    crosswalk: list[dict[str, Any]] | None = None,
    evidence: list[EvidencePointer] | None = None,
) -> Assertion:
    return Assertion(
        control=control,
        control_version="2026.09",
        subject=subject,
        outcome=outcome,
        rung=2,
        mode=mode,
        window=_WINDOW,
        population=(1, 1 if outcome == "non-conformant" else 0),
        severity="high",
        family=control.split("-", 1)[0],
        evidence=evidence or [],
        crosswalk=crosswalk or [],
    )


def _check_ref(a: Assertion) -> dict[str, str]:
    return {
        "subject": a.subject,
        "catalog": "baseline",
        "control": a.control,
        "control_version": a.control_version,
    }


def test_compute_buyer_view_returns_one_entry_per_assertion_crosswalk_pair_in_scope() -> (
    None
):
    a = _assertion(
        "REC-01",
        crosswalk=[
            {"framework": "caiq", "clause": "IAM-13.1", "verified": False},
            {"framework": "eu-ai-act", "clause": "Art. 14", "verified": True},
        ],
    )
    view = compute_buyer_view([a], _EMPTY_BLIND_SPOTS)
    assert len(view["answers"]) == 1
    assert view["answers"][0]["framework"] == "caiq"
    assert view["answers"][0]["question"] == "IAM-13.1"
    assert view["answers"][0]["control"] == "REC-01"


def test_compute_buyer_view_omits_a_control_with_no_caiq_or_aicm_crosswalk_entry() -> (
    None
):
    a = _assertion("OVS-03", crosswalk=[])
    view = compute_buyer_view([a], _EMPTY_BLIND_SPOTS)
    assert view["answers"] == []
    assert view["by_question"] == {"caiq": {}, "ai-controls-matrix": {}}


def test_compute_buyer_view_omits_a_crosswalk_entry_for_an_out_of_scope_framework() -> (
    None
):
    a = _assertion(
        "REC-01",
        crosswalk=[
            {"framework": "eu-ai-act", "clause": "Art. 14", "verified": True},
            {"framework": "iso-42001", "clause": "5.2", "verified": True},
        ],
    )
    view = compute_buyer_view([a], _EMPTY_BLIND_SPOTS)
    assert view["answers"] == []


def test_by_question_indexes_answers_without_copying_them() -> None:
    a = _assertion("REC-01", crosswalk=[{"framework": "caiq", "clause": "IAM-13.1"}])
    view = compute_buyer_view([a], _EMPTY_BLIND_SPOTS)
    index = view["by_question"]["caiq"]["IAM-13.1"]
    assert index == [0]
    view["answers"][0]["outcome"] = "mutated"
    assert view["answers"][view["by_question"]["caiq"]["IAM-13.1"][0]]["outcome"] == (
        "mutated"
    )


def test_insufficient_evidence_answer_carries_its_blind_spot_gap_step() -> None:
    a = _assertion(
        "REC-01",
        outcome="insufficient_evidence",
        crosswalk=[{"framework": "caiq", "clause": "IAM-13.1"}],
    )
    blind_spots = {
        "blind_spots": [
            {
                "event": "Decision",
                "class": "self_report",
                "ladder_rung": 2,
                "owner_key": "agent_team",
                "step_kind": "code_change",
                "supplying_adapters": [],
                "checks_unlocked": 1,
                "unlocked_checks": [_check_ref(a)],
                "needed_by": 0,
                "needed_by_checks": [],
            }
        ],
        "no_population": [],
    }
    view = compute_buyer_view([a], blind_spots)
    gap_step = view["answers"][0]["gap_step"]
    assert gap_step == {
        "kind": "blind_spot",
        "missing": [
            {
                "event": "Decision",
                "class": "self_report",
                "ladder_rung": 2,
                "owner_key": "agent_team",
                "step_kind": "code_change",
            }
        ],
    }


def test_insufficient_evidence_in_no_population_gets_no_population_marker() -> None:
    a = _assertion(
        "REC-01",
        outcome="insufficient_evidence",
        crosswalk=[{"framework": "caiq", "clause": "IAM-13.1"}],
    )
    blind_spots = {"blind_spots": [], "no_population": [_check_ref(a)]}
    view = compute_buyer_view([a], blind_spots)
    assert view["answers"][0]["gap_step"] == {"kind": "no_population"}


def test_insufficient_evidence_matching_neither_bucket_gets_no_gap_step() -> None:
    """An honest `None` (nothing classified), never a bug to assert against -- N3's own fix."""
    a = _assertion(
        "REC-01",
        outcome="insufficient_evidence",
        crosswalk=[{"framework": "caiq", "clause": "IAM-13.1"}],
    )
    view = compute_buyer_view([a], _EMPTY_BLIND_SPOTS)
    assert view["answers"][0]["gap_step"] is None


def test_manual_mode_not_assessed_carries_the_disclosure_note() -> None:
    a = _assertion(
        "DOC-01",
        mode="manual",
        outcome="not_assessed",
        crosswalk=[{"framework": "caiq", "clause": "CCC-07.1"}],
    )
    view = compute_buyer_view([a], _EMPTY_BLIND_SPOTS)
    assert "manual_checklist_note" in view["answers"][0]
    assert view["answers"][0]["manual_checklist_note"]


def test_counts_matches_aggregate_of_the_same_assertions_when_omitted() -> None:
    assertions = [
        _assertion("REC-01", outcome="conformant"),
        _assertion("REC-04", "spiffe://corp/agents/b", outcome="non-conformant"),
    ]
    view = compute_buyer_view(assertions, _EMPTY_BLIND_SPOTS)
    assert view["counts"] == aggregate(assertions)


def test_counts_passthrough_is_used_verbatim_when_supplied() -> None:
    assertions = [_assertion("REC-01")]
    sentinel = {
        "conformant": 99,
        "non-conformant": 0,
        "partial": 0,
        "not_applicable": 0,
        "not_assessed": 0,
        "insufficient_evidence": 0,
    }
    view = compute_buyer_view(assertions, _EMPTY_BLIND_SPOTS, counts=sentinel)
    assert view["counts"] == sentinel


def test_buyer_json_validates_against_its_schema() -> None:
    a = _assertion(
        "REC-01",
        outcome="insufficient_evidence",
        crosswalk=[{"framework": "caiq", "clause": "IAM-13.1", "verified": False}],
        evidence=[],
    )
    blind_spots = {"blind_spots": [], "no_population": [_check_ref(a)]}
    view = compute_buyer_view([a], blind_spots)
    jsonschema.validate(view, _SCHEMA)  # must not raise


def test_render_buyer_labels_an_unverified_clause_but_not_a_verified_one() -> None:
    """SPEC §7.3 (verifier round-1 F1): a crosswalk entry carrying ``verified_against_text: false``
    must show the "(clause reference unverified)" label next to its answer row, and a verified one
    must not -- per row, not per question heading, since two rows of one question can differ."""
    unverified = _assertion(
        "REC-01",
        crosswalk=[{"framework": "caiq", "clause": "IAM-13.1", "verified": False}],
    )
    verified = _assertion(
        "REC-04",
        crosswalk=[{"framework": "caiq", "clause": "IAM-13.1", "verified": True}],
    )
    view = compute_buyer_view([unverified, verified], _EMPTY_BLIND_SPOTS)
    md = render_buyer_md(view)
    html_out = render_buyer_html(view)
    rec01_line = next(line for line in md.splitlines() if "REC-01" in line)
    rec04_line = next(line for line in md.splitlines() if "REC-04" in line)
    assert "clause reference unverified" in rec01_line
    assert "clause reference unverified" not in rec04_line
    rec01_html = html_out[html_out.index("REC-01") : html_out.index("REC-01") + 200]
    rec04_html = html_out[html_out.index("REC-04") : html_out.index("REC-04") + 200]
    assert "clause reference unverified" in rec01_html
    assert "clause reference unverified" not in rec04_html


def test_compute_buyer_view_is_order_independent_over_many_assertions() -> None:
    assertions = [
        _assertion(
            control,
            crosswalk=[{"framework": "caiq", "clause": f"IAM-{n}.1"}],
        )
        for n, control in enumerate(
            ["REC-01", "REC-04", "DOC-01", "INC-01", "INC-02"], start=1
        )
    ]
    forward = compute_buyer_view(assertions, _EMPTY_BLIND_SPOTS)
    reversed_view = compute_buyer_view(list(reversed(assertions)), _EMPTY_BLIND_SPOTS)
    shuffled = list(assertions)
    random.Random(0).shuffle(shuffled)
    shuffled_view = compute_buyer_view(shuffled, _EMPTY_BLIND_SPOTS)
    assert reversed_view == forward
    assert shuffled_view == forward
    assert len(forward["answers"]) == 5


def test_hostile_evidence_and_clause_text_passes_through_unescaped_at_compute_time() -> (
    None
):
    """Escaping is the render layer's job (18.21's sanitiser) -- `compute_buyer_view` is a pure data
    selection and must not mangle or pre-escape a hostile string, only pass it through verbatim."""
    hostile = "<script>alert(1)</script>"
    a = _assertion(
        "REC-01",
        crosswalk=[{"framework": "caiq", "clause": hostile}],
        evidence=[
            EvidencePointer(
                ref=hostile, digest="sha256:" + "a" * 64, source_class="self_report"
            )
        ],
    )
    view = compute_buyer_view([a], _EMPTY_BLIND_SPOTS)
    assert view["answers"][0]["question"] == hostile
    assert view["answers"][0]["evidence"][0]["ref"] == hostile


def test_buyer_view_over_the_bundled_default_lens_shows_the_real_caiq_answers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Closes critic B7/N1: a real `agentce assess --for buyer` subprocess run against the shipped
    `baseline@2026.09` default lens (not only the dedicated small gate fixture) must show the six real
    CAIQ answers this item's catalog change added -- the check that would have caught B1's schema-enum
    gap before a critic had to. `corpus/quickstart/applicability.yaml` pins `eu-ai-act@2026.09` only,
    so the profile's own `catalogs` key is dropped first (the `_assess_without_a_catalog` idiom,
    `test_baseline_lens.py:299-323`) to fall through to the default baseline lens."""
    profile = yaml.safe_load(
        (_QUICKSTART / "applicability.yaml").read_text(encoding="utf-8")
    )
    del profile["catalogs"]
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    out = tmp_path / "out"
    code = cli.main(
        [
            "assess",
            "--bundle",
            str(_QUICKSTART / "evidence"),
            "--profile",
            str(tmp_path / "profile.yaml"),
            "--domain",
            str(_QUICKSTART / "domain.linkml.yaml"),
            "--out",
            str(out),
            "--for",
            "buyer",
        ]
    )
    capsys.readouterr()
    assert code == 0
    buyer = json.loads((out / "buyer.json").read_text(encoding="utf-8"))
    pairs = {(e["question"], e["control"]) for e in buyer["answers"]}
    assert pairs >= {
        ("IAM-13.1", "REC-01"),
        ("LOG-08.1", "REC-04"),
        ("CCC-07.1", "DOC-01"),
        ("SEF-06.1", "INC-02"),
    }
    assert buyer["by_question"]["ai-controls-matrix"] == {}
    assertions_json = json.loads((out / "assertions.json").read_text(encoding="utf-8"))
    assertions = [Assertion.from_json(a) for a in assertions_json]
    assert buyer["counts"] == aggregate(assertions)
