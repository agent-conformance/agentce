"""The incremental-assessment state directory (SPEC §5.4 B7, §9.6, HR-10): supersession and versioning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentce.assertions import Assertion, EvidencePointer
from agentce.errors import InputError
from agentce.state import STATE_VERSION, StateDir, window_end


def _assertion(
    subject: str, control: str, outcome: str, *, evidence: tuple[str, ...] = ()
) -> Assertion:
    return Assertion(
        control=control,
        control_version="1",
        subject=subject,
        outcome=outcome,
        rung=2,
        mode="automated",
        window=("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),
        population=(1, 0),
        severity="high",
        family=control.split("-", 1)[0],
        evidence=[
            EvidencePointer(ref=r, digest="sha256:" + "a" * 64, source_class="x")
            for r in evidence
        ],
    )


def _event(source: str, subject: str, time: str, stream: str | None = None) -> dict:
    data: dict = {"@type": "ToolCall"}
    if stream is not None:
        data["integrity"] = {"stream": stream}
    return {"source": source, "subject": subject, "time": time, "data": data}


def test_window_end_prefers_declared_end() -> None:
    class _Profile:
        observation_window = {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-03-31T00:00:00Z",
        }

    assert window_end(_Profile(), []) == "2026-03-31T00:00:00Z"


def test_window_end_falls_back_to_latest_event() -> None:
    class _Profile:
        observation_window: dict = {}

    events = [
        _event("s", "x", "2026-02-01T00:00:00.000Z"),
        _event("s", "x", "2026-01-01T00:00:00.000Z"),
    ]
    assert window_end(_Profile(), events) == "2026-02-01T00:00:00.000Z"


def test_first_assessment_has_no_supersedes(tmp_path: Path) -> None:
    state = StateDir.load(tmp_path / "s")
    supersedes, late = state.plan("sha256:aaa", [], "2026-03-31T00:00:00Z")
    assert supersedes == []
    assert late == {}


def test_changed_bundle_supersedes_and_counts_late_events(tmp_path: Path) -> None:
    state = StateDir.load(tmp_path / "s")
    state.record("sha256:aaa", _manifest(tmp_path, "one"), "2026-02-01T00:00:00.000Z")

    reloaded = StateDir.load(tmp_path / "s")
    events = [
        _event(
            "s1", "x", "2026-01-15T00:00:00.000Z", stream="strm-a"
        ),  # inside the prior window
        _event(
            "s1", "x", "2026-03-01T00:00:00.000Z", stream="strm-a"
        ),  # after the prior window
    ]
    supersedes, late = reloaded.plan("sha256:bbb", events, "2026-03-31T00:00:00.000Z")
    assert supersedes == [reloaded.last_report_digest]
    assert late == {"strm-a": 1}  # only the in-window event is late


def test_reingesting_the_same_bundle_is_idempotent(tmp_path: Path) -> None:
    state = StateDir.load(tmp_path / "s")
    state.record("sha256:aaa", _manifest(tmp_path, "one"), "2026-02-01T00:00:00.000Z")
    reloaded = StateDir.load(tmp_path / "s")
    supersedes, late = reloaded.plan("sha256:aaa", [], "2026-02-01T00:00:00.000Z")
    assert supersedes == []  # same digest already indexed: a no-op
    assert late == {}


def test_incompatible_state_version_refuses(tmp_path: Path) -> None:
    state_dir = tmp_path / "s"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"state_version": STATE_VERSION + 1}), encoding="utf-8"
    )
    with pytest.raises(InputError) as exc:
        StateDir.load(state_dir)
    assert exc.value.key == "input.state_version_incompatible"
    assert "no migration command" in exc.value.fix
    assert "move or delete the state directory" in exc.value.fix


def test_drift_reports_nothing_for_a_first_tracked_pair(tmp_path: Path) -> None:
    state = StateDir.load(tmp_path / "s")
    entries = state.drift([_assertion("x", "INC-02", "conformant", evidence=("e1",))])
    assert entries == []
    assert state.last_outcomes == {"x|INC-02": {"outcome": "conformant"}}


def test_drift_is_silent_on_an_unchanged_outcome(tmp_path: Path) -> None:
    state = StateDir.load(tmp_path / "s")
    state.drift([_assertion("x", "INC-02", "conformant", evidence=("e1",))])
    entries = state.drift([_assertion("x", "INC-02", "conformant", evidence=("e1",))])
    assert entries == []


def test_drift_reports_a_changed_outcome_with_its_evidence(tmp_path: Path) -> None:
    state = StateDir.load(tmp_path / "s")
    state.drift([_assertion("x", "INC-02", "conformant", evidence=("e1",))])
    entries = state.drift(
        [_assertion("x", "INC-02", "non-conformant", evidence=("e2",))]
    )
    assert entries == [
        {
            "subject": "x",
            "control": "INC-02",
            "previous_outcome": "conformant",
            "outcome": "non-conformant",
            "evidence": ["e2"],
        }
    ]


def test_drift_reports_only_the_pair_that_changed(tmp_path: Path) -> None:
    state = StateDir.load(tmp_path / "s")
    state.drift(
        [
            _assertion("a", "INC-02", "conformant", evidence=("e1",)),
            _assertion("b", "INC-02", "conformant", evidence=("e2",)),
        ]
    )
    entries = state.drift(
        [
            _assertion("a", "INC-02", "non-conformant", evidence=("e3",)),
            _assertion("b", "INC-02", "conformant", evidence=("e2",)),
        ]
    )
    assert [e["subject"] for e in entries] == ["a"]


def test_last_outcomes_round_trips_through_save_and_load(tmp_path: Path) -> None:
    state = StateDir.load(tmp_path / "s")
    state.drift([_assertion("x", "INC-02", "conformant", evidence=("e1",))])
    state.record("sha256:aaa", _manifest(tmp_path, "one"), "2026-02-01T00:00:00.000Z")

    reloaded = StateDir.load(tmp_path / "s")
    assert reloaded.last_outcomes == {"x|INC-02": {"outcome": "conformant"}}


def test_a_state_directory_written_before_last_outcomes_existed_reports_no_drift(
    tmp_path: Path,
) -> None:
    """Additive field, SPEC's own on-disk-index extension point: a pre-existing `state.json` with no
    `last_outcomes` key at all (a real prior run happened, just not tracked per-pair) loads as `{}` --
    "no prior outcome recorded" -- never a version mismatch and never a manufactured drift finding."""
    state_dir = tmp_path / "s"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "state_version": STATE_VERSION,
                "bundle_digests": ["sha256:aaa"],
                "last_report_digest": "sha256:bbb",
                "last_window_end": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    state = StateDir.load(state_dir)
    assert state.last_outcomes == {}
    entries = state.drift([_assertion("x", "INC-02", "conformant", evidence=("e1",))])
    assert entries == []


def _manifest(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}-manifest.json"
    path.write_text(
        json.dumps({"agentce_manifest_version": 1, "name": name}), encoding="utf-8"
    )
    return path
