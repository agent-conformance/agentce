"""compute_project_view: the per-agent rollup and undeclared-agents list (18.14, Hill 7)."""

from __future__ import annotations

from typing import Any

from agentce.assertions import Assertion
from agentce.profile import Profile, Subject
from agentce.project import (
    blind_spots_by_subject,
    compute_project_view,
    no_population_by_subject,
)

_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")


def _assertion(subject: str, control: str, outcome: str = "conformant") -> Assertion:
    return Assertion(
        control=control,
        control_version="2026.09",
        subject=subject,
        outcome=outcome,
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=(1, 0),
        severity="high",
        family="REC",
    )


def _check_ref(subject: str, control: str) -> dict[str, str]:
    return {
        "subject": subject,
        "catalog": "cat",
        "control": control,
        "control_version": "2026.09",
    }


def _blind_spot(
    unlocked: list[dict[str, str]],
    needed: list[dict[str, str]],
    *,
    event: str = "Decision",
) -> dict[str, Any]:
    return {
        "event": event,
        "class": "any",
        "ladder_rung": 2,
        "owner_key": "agent_team",
        "step_kind": "code_change",
        "supplying_adapters": [],
        "checks_unlocked": len(unlocked),
        "unlocked_checks": unlocked,
        "needed_by": len(needed),
        "needed_by_checks": needed,
    }


def test_blind_spots_by_subject_rescopes_counts_and_refs() -> None:
    entry = _blind_spot(
        [
            _check_ref("A", "REC-01"),
            _check_ref("A", "REC-02"),
            _check_ref("B", "REC-03"),
        ],
        [],
    )
    by_subject = blind_spots_by_subject({"blind_spots": [entry]})
    assert [cr["control"] for cr in by_subject["A"][0]["unlocked_checks"]] == [
        "REC-01",
        "REC-02",
    ]
    assert by_subject["A"][0]["checks_unlocked"] == 2
    assert [cr["control"] for cr in by_subject["B"][0]["unlocked_checks"]] == ["REC-03"]
    assert by_subject["B"][0]["checks_unlocked"] == 1
    # Neither per-subject count equals the global entry's own count (the N3 regression: reusing
    # the global count verbatim would report every other subject's unlocked checks under each one).
    assert entry["checks_unlocked"] == 3
    assert by_subject["A"][0]["checks_unlocked"] != entry["checks_unlocked"]
    assert by_subject["B"][0]["checks_unlocked"] != entry["checks_unlocked"]


def test_blind_spots_by_subject_drops_entries_that_do_not_touch_a_subject() -> None:
    entry = _blind_spot([_check_ref("A", "REC-01")], [])
    by_subject = blind_spots_by_subject({"blind_spots": [entry]})
    assert "B" not in by_subject


def test_no_population_by_subject_groups_by_own_field() -> None:
    grouped = no_population_by_subject(
        [
            _check_ref("A", "REC-04"),
            _check_ref("B", "REC-05"),
            _check_ref("A", "REC-06"),
        ]
    )
    assert [cr["control"] for cr in grouped["A"]] == ["REC-04", "REC-06"]
    assert [cr["control"] for cr in grouped["B"]] == ["REC-05"]


def test_three_subject_rollup_declared_undeclared_and_top_gaps() -> None:
    # A: declared, has events, no undeclared agents. B: declared, no events at all (still a row).
    # C: NOT declared -- discovered only via an event's data.agent.id.
    profile = Profile(subjects=[Subject(id="A"), Subject(id="B")])
    assertions = [
        _assertion("A", "REC-01", "conformant"),
        _assertion("C", "REC-01", "insufficient_evidence"),
    ]
    declared_subject_ids = frozenset({"A", "B"})
    activity_by_subject = {
        "A": {"agents": ["A"]},
        "B": {"agents": []},
        "C": {"agents": ["C"]},
    }
    shared_gap = _blind_spot(
        [_check_ref("A", "REC-01")], [_check_ref("C", "REC-01")], event="ModelCall"
    )
    other_gap = _blind_spot([_check_ref("B", "REC-02")], [], event="ToolCall")
    blind_spots = {"blind_spots": [shared_gap, other_gap], "no_population": []}

    view = compute_project_view(
        assertions, profile, declared_subject_ids, activity_by_subject, blind_spots
    )

    rows = {row["id"]: row for row in view["agents"]}
    assert set(rows) == {"A", "B", "C"}
    assert rows["A"]["declared"] is True
    assert rows["B"]["declared"] is True
    assert rows["C"]["declared"] is False
    assert rows["C"]["blind_spots_count"] == 1
    assert view["undeclared_agents"] == ["C"]

    assert len(view["top_gaps"]) == 2
    assert view["top_gaps"][0]["agents"] == ["A", "C"]
    assert view["top_gaps"][1]["agents"] == ["B"]
    # top_gaps reuses compute_blind_spots' own global entries, not per-subject-filtered copies.
    assert view["top_gaps"][0]["checks_unlocked"] == shared_gap["checks_unlocked"]


def test_single_subject_profile_still_returns_a_valid_one_row_view() -> None:
    profile = Profile(subjects=[Subject(id="A")])
    assertions = [_assertion("A", "REC-01")]
    view = compute_project_view(
        assertions,
        profile,
        frozenset({"A"}),
        {"A": {"agents": ["A"]}},
        {"blind_spots": [], "no_population": []},
    )
    assert len(view["agents"]) == 1
    assert view["agents"][0] == {
        "id": "A",
        "declared": True,
        "summary": view["agents"][0]["summary"],
        "blind_spots_count": 0,
        "agents_observed": ["A"],
    }
    assert view["undeclared_agents"] == []
    assert view["top_gaps"] == []
