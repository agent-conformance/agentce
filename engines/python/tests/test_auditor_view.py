"""compute_auditor_view: the clause-by-clause selection of already-computed assertions (18.17). The
CLI-level `--for auditor` wiring, render functions, and build gate are C3/C4's work
(verification/gates/auditor_view.sh); this file proves this session's real compute-layer code against
real Assertion objects, including schema validation and a hostile-name escaping check that confirms
escaping is deferred to render time, not baked in here."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import jsonschema

from agentce.assertions import Assertion, EvidencePointer
from agentce.auditor_view import compute_auditor_view

_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA = json.loads(
    (_REPO_ROOT / "spec/report/auditor.schema.json").read_text(encoding="utf-8")
)
_DEVIATION_ENTRY: dict[str, Any] = {
    "control": "OVS-03",
    "rationale": "compensating monitor in place while the fix ships",
    "compensating_control": "manual review of every irreversible action",
    "owner": "platform-team@corp",
    "approver": "ciso@corp",
    "granted": "2026-01-01",
    "expiry": "2026-06-30",
}


def _assertion(
    control: str,
    subject: str = "spiffe://corp/agents/a",
    *,
    outcome: str = "conformant",
    mode: str = "automated",
    deviation: str | None = None,
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
        deviation=deviation,
        crosswalk=crosswalk or [],
    )


def test_compute_auditor_view_returns_one_entry_per_control_subject_pair() -> None:
    assertions = [
        _assertion("OVS-03", "spiffe://corp/agents/a"),
        _assertion("OVS-03", "spiffe://corp/agents/b"),
        _assertion("REC-01", "spiffe://corp/agents/a"),
    ]
    view = compute_auditor_view(assertions)
    pairs = [(c["control"], c["subject"]) for c in view["clauses"]]
    assert pairs == [
        ("OVS-03", "spiffe://corp/agents/a"),
        ("OVS-03", "spiffe://corp/agents/b"),
        ("REC-01", "spiffe://corp/agents/a"),
    ]
    assert len(view["clauses"]) == len(assertions)


def test_by_clause_groups_control_ids_under_every_crosswalk_entry_and_omits_uncrosswalked_controls() -> (
    None
):
    assertions = [
        _assertion(
            "OVS-03",
            crosswalk=[
                {"framework": "eu-ai-act", "clause": "Art. 14", "verified": True}
            ],
        ),
        _assertion(
            "REC-01",
            "spiffe://corp/agents/b",
            crosswalk=[
                {"framework": "eu-ai-act", "clause": "Art. 14", "verified": False}
            ],
        ),
        _assertion("INT-01", "spiffe://corp/agents/c"),  # no crosswalk entry at all
    ]
    view = compute_auditor_view(assertions)
    assert view["by_clause"] == {"eu-ai-act": {"Art. 14": ["OVS-03", "REC-01"]}}
    # INT-01 still appears in clauses, full record, simply absent from by_clause.
    assert "INT-01" in {c["control"] for c in view["clauses"]}
    assert "INT-01" not in view["by_clause"]["eu-ai-act"]["Art. 14"]


def test_by_clause_dedupes_a_control_crosswalked_to_the_same_clause_by_two_subjects() -> (
    None
):
    xw = [{"framework": "eu-ai-act", "clause": "Art. 14", "verified": True}]
    assertions = [
        _assertion("OVS-03", "spiffe://corp/agents/a", crosswalk=xw),
        _assertion("OVS-03", "spiffe://corp/agents/b", crosswalk=xw),
    ]
    view = compute_auditor_view(assertions)
    assert view["by_clause"]["eu-ai-act"]["Art. 14"] == ["OVS-03"]


def test_deviation_detail_matches_the_register_record_verbatim() -> None:
    assertions = [_assertion("OVS-03", outcome="partial", deviation="OVS-03")]
    view = compute_auditor_view(assertions, deviations=[_DEVIATION_ENTRY])
    detail = view["clauses"][0]["deviation"]
    assert detail == {
        "rationale": _DEVIATION_ENTRY["rationale"],
        "compensating_control": _DEVIATION_ENTRY["compensating_control"],
        "owner": _DEVIATION_ENTRY["owner"],
        "approver": _DEVIATION_ENTRY["approver"],
        "granted": _DEVIATION_ENTRY["granted"],
        "expiry": _DEVIATION_ENTRY["expiry"],
    }


def test_deviation_detail_is_register_unavailable_stub_when_no_register_passed() -> (
    None
):
    assertions = [_assertion("OVS-03", outcome="partial", deviation="OVS-03")]
    view = compute_auditor_view(
        assertions
    )  # no `deviations` -- re-rendered from assertions.json alone
    assert view["clauses"][0]["deviation"] == {"control": "OVS-03"}


def test_manual_mode_not_assessed_carries_the_disclosure_note_others_do_not() -> None:
    assertions = [
        _assertion("DOC-01", mode="manual", outcome="not_assessed"),
        _assertion(
            "REC-01", "spiffe://corp/agents/b", mode="automated", outcome="conformant"
        ),
    ]
    view = compute_auditor_view(assertions)
    manual_clause = next(c for c in view["clauses"] if c["control"] == "DOC-01")
    automated_clause = next(c for c in view["clauses"] if c["control"] == "REC-01")
    assert "manual_checklist_note" in manual_clause
    assert manual_clause["manual_checklist_note"]  # non-empty
    assert "manual_checklist_note" not in automated_clause


def test_manual_mode_with_a_real_outcome_carries_no_note() -> None:
    """A manual-mode control that *was* assessed (a future evaluator) gets no disclosure note --
    only the still-unevaluated case (`not_assessed`) does."""
    assertions = [_assertion("DOC-01", mode="manual", outcome="conformant")]
    view = compute_auditor_view(assertions)
    assert "manual_checklist_note" not in view["clauses"][0]


def test_counts_matches_aggregate_of_the_same_assertions() -> None:
    from agentce.assertions import aggregate

    assertions = [
        _assertion("OVS-03", outcome="conformant"),
        _assertion("REC-01", "spiffe://corp/agents/b", outcome="non-conformant"),
    ]
    view = compute_auditor_view(assertions)
    assert view["counts"] == aggregate(assertions)


def test_auditor_json_validates_against_its_schema() -> None:
    assertions = [
        _assertion(
            "OVS-03",
            outcome="partial",
            deviation="OVS-03",
            crosswalk=[
                {"framework": "eu-ai-act", "clause": "Art. 14", "verified": True}
            ],
            evidence=[
                EvidencePointer(
                    ref="trace.jsonl#L1",
                    digest="sha256:" + "a" * 64,
                    source_class="self_report",
                )
            ],
        ),
        _assertion(
            "DOC-01", "spiffe://corp/agents/b", mode="manual", outcome="not_assessed"
        ),
    ]
    view = compute_auditor_view(assertions, deviations=[_DEVIATION_ENTRY])
    jsonschema.validate(view, _SCHEMA)  # must not raise


def test_compute_auditor_view_is_order_independent_over_many_assertions() -> None:
    assertions = [
        _assertion(
            control,
            crosswalk=[
                {
                    "framework": "eu-ai-act",
                    "clause": f"Art. {n}",
                    "verified": n % 2 == 0,
                }
            ],
        )
        for n, control in enumerate(
            ["OVS-03", "REC-01", "INT-01", "DOC-01", "RSK-01"], start=1
        )
    ]
    forward = compute_auditor_view(assertions)
    reversed_view = compute_auditor_view(list(reversed(assertions)))
    shuffled = list(assertions)
    random.Random(0).shuffle(shuffled)
    shuffled_view = compute_auditor_view(shuffled)
    assert reversed_view == forward
    assert shuffled_view == forward
    assert len(forward["clauses"]) == 5


def test_hostile_deviation_and_evidence_text_passes_through_unescaped_at_compute_time() -> (
    None
):
    """Escaping is the render layer's job (18.21's sanitiser, reused at render time, never here) --
    `compute_auditor_view` is a pure data selection and must not mangle or pre-escape a hostile
    string, only pass the record through verbatim for the renderer to sanitise."""
    hostile = "<script>alert(1)</script>"
    entry = {**_DEVIATION_ENTRY, "rationale": hostile}
    assertions = [
        _assertion(
            "OVS-03",
            outcome="partial",
            deviation="OVS-03",
            evidence=[
                EvidencePointer(
                    ref=hostile, digest="sha256:" + "a" * 64, source_class="self_report"
                )
            ],
        )
    ]
    view = compute_auditor_view(assertions, deviations=[entry])
    assert view["clauses"][0]["deviation"]["rationale"] == hostile
    assert view["clauses"][0]["evidence"][0]["ref"] == hostile
