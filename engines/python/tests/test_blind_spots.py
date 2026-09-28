"""``compute_blind_spots``: grouping, evidence-ladder rung, and the multi-catalog key (RFC 0008)."""

from __future__ import annotations

import locale
import os
from pathlib import Path
from typing import Any

import pytest

from agentce.assertions import Assertion
from agentce.blind_spots import (
    _supplying_adapters,
    catalog_support_view,
    compute_blind_spots,
)
from agentce.catalog import Catalog, ControlSpec
from agentce.profile import Profile, Subject

_SUBJECT = "spiffe://corp/agents/a"
_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")


def _control(
    control_id: str,
    minimum_evidence: list[dict[str, str]],
    *,
    version: str = "2026.09",
    mode: str = "automated",
    rung: int = 2,
) -> ControlSpec:
    return ControlSpec(
        id=control_id,
        version=version,
        title=control_id,
        applies_to_roles=["both"],
        mode=mode,
        rung=rung,
        severity="high",
        min_source_class="any",
        minimum_evidence=minimum_evidence,
        shape_path=None,
        tolerance={"kind": "count", "max": 0},
        test_cases=[],
    )


def _catalog(controls: list[ControlSpec], *, catalog_id: str = "cat") -> Catalog:
    return Catalog(
        id=catalog_id,
        version="2026.09",
        directory=Path("."),
        controls=controls,
        shapes={},
    )


def _profile(subject_id: str = _SUBJECT) -> Profile:
    return Profile(subjects=[Subject(id=subject_id)])


def _assertion(
    control: ControlSpec,
    *,
    outcome: str,
    population: tuple[int, int],
    subject: str = _SUBJECT,
) -> Assertion:
    return Assertion(
        control=control.id,
        control_version=control.version,
        subject=subject,
        outcome=outcome,
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=population,
        severity="high",
        family=control.id.split("-", 1)[0],
    )


def _event(
    subject: str, event_type: str, source_class: str = "self_report"
) -> dict[str, Any]:
    return {
        "subject": subject,
        "agentcesourceclass": source_class,
        "data": {"@type": event_type},
    }


def _check_ref(
    control: ControlSpec, *, catalog_id: str = "cat", subject: str = _SUBJECT
) -> dict[str, str]:
    return {
        "subject": subject,
        "catalog": catalog_id,
        "control": control.id,
        "control_version": control.version,
    }


def test_no_insufficient_evidence_writes_the_honest_empty_answer() -> None:
    control = _control("C-01", [{"event": "Decision", "class": "any"}])
    catalog = _catalog([control])
    assertion = _assertion(control, outcome="conformant", population=(1, 0))
    result = compute_blind_spots([assertion], _profile(), [catalog], [])
    assert result == {"blind_spots": [], "no_population": []}


def test_two_missing_requirements_go_to_needed_by_never_unlocked() -> None:
    control = _control(
        "C-02",
        [{"event": "ModelCall", "class": "any"}, {"event": "ToolCall", "class": "any"}],
    )
    catalog = _catalog([control])
    assertion = _assertion(control, outcome="insufficient_evidence", population=(3, 0))
    result = compute_blind_spots([assertion], _profile(), [catalog], [])
    by_key = {(b["event"], b["class"]): b for b in result["blind_spots"]}
    assert set(by_key) == {("ModelCall", "self_report"), ("ToolCall", "self_report")}
    for bs in by_key.values():
        assert bs["checks_unlocked"] == 0
        assert bs["unlocked_checks"] == []
        assert bs["needed_by"] == 1
        assert bs["needed_by_checks"] == [_check_ref(control)]
    assert result["no_population"] == []


def test_empty_population_branch_single_missing_is_needed_by_not_unlocked() -> None:
    """Round 3's fix: a non-empty recomputed missing set on the empty-population branch is
    informative but never provably sufficient on its own."""
    control = _control("C-03", [{"event": "Decision", "class": "any"}])
    catalog = _catalog([control])
    assertion = _assertion(control, outcome="insufficient_evidence", population=(0, 0))
    result = compute_blind_spots([assertion], _profile(), [catalog], [])
    assert len(result["blind_spots"]) == 1
    bs = result["blind_spots"][0]
    assert bs["checks_unlocked"] == 0
    assert bs["needed_by"] == 1
    assert bs["needed_by_checks"] == [_check_ref(control)]
    assert result["no_population"] == []


def test_any_and_self_report_merge_into_one_blind_spot() -> None:
    control_any = _control("C-04", [{"event": "Decision", "class": "any"}])
    control_self_report = _control(
        "C-05", [{"event": "Decision", "class": "self_report"}]
    )
    catalog = _catalog([control_any, control_self_report])
    assertions = [
        _assertion(control_any, outcome="insufficient_evidence", population=(1, 0)),
        _assertion(
            control_self_report, outcome="insufficient_evidence", population=(1, 0)
        ),
    ]
    result = compute_blind_spots(assertions, _profile(), [catalog], [])
    assert len(result["blind_spots"]) == 1
    bs = result["blind_spots"][0]
    assert (bs["event"], bs["class"]) == ("Decision", "self_report")
    assert bs["checks_unlocked"] == 2
    assert {c["control"] for c in bs["unlocked_checks"]} == {"C-04", "C-05"}


def test_enforcement_point_default_events_rank_at_rung_3() -> None:
    """PolicyDecision is produced today only by enforcement_point-default adapters, so a catalog that
    only asks for 'any' class must still rank it rung 3 (RFC 0008 Sec.3), not rung 1/2."""
    control = _control("C-06", [{"event": "PolicyDecision", "class": "any"}])
    catalog = _catalog([control])
    assertion = _assertion(control, outcome="insufficient_evidence", population=(1, 0))
    result = compute_blind_spots([assertion], _profile(), [catalog], [])
    bs = result["blind_spots"][0]
    assert bs["ladder_rung"] == 3
    assert bs["owner_key"] == "platform_or_security"
    assert bs["step_kind"] == "request"
    assert set(bs["supplying_adapters"]) == {"mcp-gateway", "policy-engines"}


def test_adapter_free_event_ranks_at_rung_2_not_a_vacuous_rung_3() -> None:
    """No adapter declares Decision at all; round 2's fix for the empty-producer-set guard."""
    control = _control("C-07", [{"event": "Decision", "class": "any"}])
    catalog = _catalog([control])
    assertion = _assertion(control, outcome="insufficient_evidence", population=(1, 0))
    result = compute_blind_spots([assertion], _profile(), [catalog], [])
    bs = result["blind_spots"][0]
    assert bs["ladder_rung"] == 2
    assert bs["owner_key"] == "agent_team"
    assert bs["step_kind"] == "code_change"
    assert bs["supplying_adapters"] == []


def test_otel_shaped_self_report_event_ranks_at_rung_1() -> None:
    control = _control("C-08", [{"event": "ModelCall", "class": "any"}])
    catalog = _catalog([control])
    assertion = _assertion(control, outcome="insufficient_evidence", population=(1, 0))
    result = compute_blind_spots([assertion], _profile(), [catalog], [])
    bs = result["blind_spots"][0]
    assert bs["ladder_rung"] == 1
    assert bs["supplying_adapters"] == ["otel-genai"]


def test_exact_enforcement_point_key_excludes_self_report_only_adapters() -> None:
    """A rung-3 (ToolCall, enforcement_point) blind spot never lists otel-genai (self_report-only for
    ToolCall), and its checks_unlocked never absorbs a separate rung-1 (ToolCall, any) blind spot's
    check -- the documented cross-key non-goal (RFC 0008 Sec.2)."""
    rung1_control = _control("C-09", [{"event": "ToolCall", "class": "any"}])
    rung3_control = _control(
        "C-10", [{"event": "ToolCall", "class": "enforcement_point"}]
    )
    catalog = _catalog([rung1_control, rung3_control])
    assertions = [
        _assertion(rung1_control, outcome="insufficient_evidence", population=(1, 0)),
        _assertion(rung3_control, outcome="insufficient_evidence", population=(1, 0)),
    ]
    result = compute_blind_spots(assertions, _profile(), [catalog], [])
    by_key = {(b["event"], b["class"]): b for b in result["blind_spots"]}
    rung1 = by_key[("ToolCall", "self_report")]
    rung3 = by_key[("ToolCall", "enforcement_point")]
    assert rung1["ladder_rung"] == 1
    # Every producer satisfies a self_report/any requirement (an enforcement_point observation is
    # trivially strong enough too) -- RFC 0008 Sec.3's last point.
    assert rung1["supplying_adapters"] == ["mcp-gateway", "otel-genai"]
    assert rung3["ladder_rung"] == 3
    assert rung3["supplying_adapters"] == ["mcp-gateway"]
    assert {c["control"] for c in rung1["unlocked_checks"]} == {"C-09"}
    assert {c["control"] for c in rung3["unlocked_checks"]} == {"C-10"}


def test_independent_system_ranks_at_rung_4() -> None:
    control = _control(
        "C-11", [{"event": "ApprovalDecided", "class": "independent_system"}]
    )
    catalog = _catalog([control])
    assertion = _assertion(control, outcome="insufficient_evidence", population=(1, 0))
    result = compute_blind_spots([assertion], _profile(), [catalog], [])
    bs = result["blind_spots"][0]
    assert bs["ladder_rung"] == 4
    assert bs["owner_key"] == "ticketing_or_iam"
    assert bs["step_kind"] == "request"
    assert bs["supplying_adapters"] == []


def test_fully_satisfied_but_empty_structural_population_goes_to_no_population() -> (
    None
):
    """The recomputed missing set is empty even though the outcome is insufficient_evidence -- the
    rare 'no_population' case (RFC 0008 Sec.4), never keyed off which assess.py branch fired."""
    control = _control("C-12", [{"event": "Decision", "class": "any"}])
    catalog = _catalog([control])
    events = [_event(_SUBJECT, "Decision")]
    assertion = _assertion(control, outcome="insufficient_evidence", population=(0, 0))
    result = compute_blind_spots([assertion], _profile(), [catalog], events)
    assert result["blind_spots"] == []
    assert result["no_population"] == [_check_ref(control)]


def test_multi_catalog_same_control_id_and_version_never_collide() -> None:
    """11 shared ids across baseline/eu-ai-act/nist-ai-rmf at 2026.09 measured (RFC 0008 Sec.6); a
    bare (control_id, control_version) key would collapse the two catalogs' checks into one."""
    control_a = _control("SHARED-01", [{"event": "Decision", "class": "any"}])
    control_b = _control("SHARED-01", [{"event": "Decision", "class": "any"}])
    catalog_a = _catalog([control_a], catalog_id="cat-a")
    catalog_b = _catalog([control_b], catalog_id="cat-b")
    profile = _profile()
    assertions = [
        _assertion(control_a, outcome="insufficient_evidence", population=(1, 0)),
        _assertion(control_b, outcome="insufficient_evidence", population=(1, 0)),
    ]
    result = compute_blind_spots(assertions, profile, [catalog_a, catalog_b], [])
    assert len(result["blind_spots"]) == 1
    bs = result["blind_spots"][0]
    assert bs["checks_unlocked"] == 2
    catalogs_seen = {c["catalog"] for c in bs["unlocked_checks"]}
    assert catalogs_seen == {"cat-a", "cat-b"}


def test_mismatched_assertion_order_raises() -> None:
    """The positional pairing fails loudly on the first mismatch, never silently misattributing
    (RFC 0008 Sec.6: a per-position identity check, not just a length check)."""
    control_a = _control("C-13", [{"event": "Decision", "class": "any"}])
    control_b = _control("C-14", [{"event": "Decision", "class": "any"}])
    catalog = _catalog([control_a, control_b])
    # Reversed relative to the catalog's own control order.
    assertions = [
        _assertion(control_b, outcome="conformant", population=(1, 0)),
        _assertion(control_a, outcome="conformant", population=(1, 0)),
    ]
    with pytest.raises(AssertionError):
        compute_blind_spots(assertions, _profile(), [catalog], [])


def test_wrong_length_raises() -> None:
    control_a = _control("C-15", [{"event": "Decision", "class": "any"}])
    control_b = _control("C-16", [{"event": "Decision", "class": "any"}])
    catalog = _catalog([control_a, control_b])
    assertions = [_assertion(control_a, outcome="conformant", population=(1, 0))]
    with pytest.raises(AssertionError):
        compute_blind_spots(assertions, _profile(), [catalog], [])


def test_blind_spots_sort_by_rank_then_rung_then_event_then_class() -> None:
    high = _control("C-17", [{"event": "ZEvent", "class": "any"}])
    low_a = _control("C-18", [{"event": "AEvent", "class": "any"}])
    low_b = _control("C-19", [{"event": "BEvent", "class": "any"}])
    catalog = _catalog([high, low_a, low_b])
    assertions = [
        _assertion(high, outcome="insufficient_evidence", population=(1, 0)),
        _assertion(low_a, outcome="insufficient_evidence", population=(1, 0)),
        _assertion(low_b, outcome="insufficient_evidence", population=(1, 0)),
    ]
    result = compute_blind_spots(assertions, _profile(), [catalog], [])
    # All three have checks_unlocked=1, needed_by=0, ladder_rung=2 (adapter-free events): tie-broken
    # by event name in byte order.
    events = [b["event"] for b in result["blind_spots"]]
    assert events == ["AEvent", "BEvent", "ZEvent"]


def test_a_blind_spot_unlocking_more_checks_ranks_first_even_when_discovered_later() -> (
    None
):
    """The primary sort key is real, not an accident of discovery order: unlike the tie-broken test
    above, these two groups differ on checks_unlocked, and the higher-unlocking group (BEvent) is
    discovered *after* the lower one (AEvent) -- so a missing or discovery-order ranking would put
    AEvent first, and only an actual sort by checks_unlocked puts BEvent first as it must."""
    low = _control("C-22", [{"event": "AEvent", "class": "any"}])
    high_1 = _control("C-23", [{"event": "BEvent", "class": "any"}])
    high_2 = _control("C-24", [{"event": "BEvent", "class": "any"}])
    catalog = _catalog([low, high_1, high_2])
    assertions = [
        _assertion(low, outcome="insufficient_evidence", population=(1, 0)),
        _assertion(high_1, outcome="insufficient_evidence", population=(1, 0)),
        _assertion(high_2, outcome="insufficient_evidence", population=(1, 0)),
    ]
    result = compute_blind_spots(assertions, _profile(), [catalog], [])
    events = [b["event"] for b in result["blind_spots"]]
    unlocked = [b["checks_unlocked"] for b in result["blind_spots"]]
    assert events == ["BEvent", "AEvent"]
    assert unlocked == [2, 1]


def test_a_tied_checks_unlocked_blind_spot_with_more_needed_by_ranks_first() -> None:
    """The secondary sort key (needed_by) is real too, not just the primary one: these two groups
    tie on checks_unlocked=1, and the one discovered first (AEvent) has the lower needed_by -- so a
    missing or discovery-order tie-break would leave AEvent first, and only an actual sort by
    needed_by puts BEvent (needed_by=1) first as RFC 0008's fixed ranking order requires."""
    a_unlock = _control("C-25", [{"event": "AEvent", "class": "any"}])
    b_unlock = _control("C-27", [{"event": "BEvent", "class": "any"}])
    b_needed = _control(
        "C-28",
        [{"event": "BEvent", "class": "any"}, {"event": "YEvent", "class": "any"}],
    )
    controls = [a_unlock, b_unlock, b_needed]
    catalog = _catalog(controls)
    assertions = [
        _assertion(c, outcome="insufficient_evidence", population=(1, 0))
        for c in controls
    ]
    result = compute_blind_spots(assertions, _profile(), [catalog], [])
    top_two = [
        (b["event"], b["checks_unlocked"], b["needed_by"])
        for b in result["blind_spots"][:2]
    ]
    assert top_two == [("BEvent", 1, 1), ("AEvent", 1, 0)]


def test_is_order_independent_over_assess_subjects_own_order() -> None:
    """A deterministic pure function of its arguments (given assertions in its one true order, RFC
    0008 Sec.6) -- calling it twice with the same input gives the same output."""
    control = _control("C-20", [{"event": "Decision", "class": "any"}])
    catalog = _catalog([control])
    assertion = _assertion(control, outcome="insufficient_evidence", population=(1, 0))
    profile = _profile()
    first = compute_blind_spots([assertion], profile, [catalog], [])
    second = compute_blind_spots([assertion], profile, [catalog], [])
    assert first == second


def test_is_locale_and_clock_independent() -> None:
    control = _control("C-21", [{"event": "Decision", "class": "any"}])
    catalog = _catalog([control])
    assertion = _assertion(control, outcome="insufficient_evidence", population=(1, 0))
    baseline = compute_blind_spots([assertion], _profile(), [catalog], [])

    old_tz = os.environ.get("TZ")
    old_locale = locale.setlocale(locale.LC_ALL)
    try:
        os.environ["TZ"] = "Pacific/Kiritimati"
        for candidate in ("de_DE.UTF-8", "de_DE", "C"):
            try:
                locale.setlocale(locale.LC_ALL, candidate)
                break
            except locale.Error:
                continue
        assert compute_blind_spots([assertion], _profile(), [catalog], []) == baseline
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        locale.setlocale(locale.LC_ALL, old_locale)


# --- 18.9 C4: `catalog_support_view` (contracts/P18-18.9.md). ---


def test_catalog_support_view_pins_the_scaffold_requirement() -> None:
    """The scaffold's one requirement (Decision/self_report) has no adapter producer at all -- a
    true, meaningful "only your own code can emit this" answer, not a stub-passable empty list. A
    stub returning ``requirements: []``, or one that zeroes ``supplying_adapters``, would pass a
    presence check but not this exact-literal one."""
    control = _control("GEN-01", [{"event": "Decision", "class": "self_report"}])
    view = catalog_support_view(_catalog([control]))
    assert view == [
        {
            "control": "GEN-01",
            "control_version": "2026.09",
            "title": "GEN-01",
            "mode": "automated",
            "rung": 2,
            "requirements": [
                {
                    "event": "Decision",
                    "class": "self_report",
                    "ladder_rung": 2,
                    "owner_key": "agent_team",
                    "step_kind": "code_change",
                    "supplying_adapters": [],
                }
            ],
        }
    ]


def test_catalog_support_view_pins_supplying_adapters_for_a_real_event() -> None:
    """A control needing ``ToolCall``/``any`` (matching
    ``verification/gates/fixtures/audience_presets/catalog/controls/AUD-01.yaml``'s own shape): the
    pinned adapter list, cross-checked against ``_supplying_adapters`` called directly so the test
    stays correct if ``event_producers.json`` is ever regenerated."""
    control = _control("AUD-01", [{"event": "ToolCall", "class": "any"}])
    view = catalog_support_view(_catalog([control]))
    requirement = view[0]["requirements"][0]
    assert requirement["supplying_adapters"] == ["mcp-gateway", "otel-genai"]
    assert requirement["supplying_adapters"] == _supplying_adapters(
        "ToolCall", "self_report"
    )


def test_catalog_support_view_reports_manual_mode_regardless_of_minimum_evidence() -> (
    None
):
    """A ``mode: manual`` control (``assess.py``'s own gate always resolves it to
    ``not_assessed``, never a real verdict, regardless of what its ``minimum_evidence`` lists) must
    not look identical to an automated/rung-2 control with the same requirement -- the view carries
    the control's own ``mode``/``rung`` so a reader isn't misled into thinking evidence collection
    alone would produce a verdict for it."""
    control = _control(
        "MAN-01", [{"event": "Decision", "class": "self_report"}], mode="manual", rung=1
    )
    view = catalog_support_view(_catalog([control]))
    assert view[0]["mode"] == "manual"
    assert view[0]["rung"] == 1


def test_catalog_support_view_orders_controls_and_requirements() -> None:
    b_control = _control(
        "B-01",
        [
            {"event": "ToolCall", "class": "any"},
            {"event": "Decision", "class": "self_report"},
        ],
    )
    a_control = _control("A-01", [{"event": "Decision", "class": "self_report"}])
    view = catalog_support_view(_catalog([b_control, a_control]))
    assert [entry["control"] for entry in view] == ["A-01", "B-01"]
    b_entry = next(entry for entry in view if entry["control"] == "B-01")
    assert [r["event"] for r in b_entry["requirements"]] == ["Decision", "ToolCall"]


def test_catalog_support_view_never_drops_an_empty_requirements_control() -> None:
    control = _control("E-01", [])
    view = catalog_support_view(_catalog([control]))
    assert view == [
        {
            "control": "E-01",
            "control_version": "2026.09",
            "title": "E-01",
            "mode": "automated",
            "rung": 2,
            "requirements": [],
        }
    ]
