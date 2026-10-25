"""The applicability profile loader (SPEC §6.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentce.errors import InputError
from agentce.profile import Profile
from agentce.readiness import deviation_lint

_EXAMPLE = """
profile_version: 1
observation_window: {start: 2026-06-01T00:00:00Z, end: 2026-09-01T00:00:00Z}
subjects:
  - id: spiffe://corp/agents/claims-triage
    name: Claims triage agent
    role: deployer
    declared_decision_types: [ins:ClaimStatusChange]
    declared_oversight: {ins:ClaimDenialRecommendation: review_before}
    evidence_sources:
      - {adapter: mcp-gateway, source: urn:src:gw, class: enforcement_point, manifest: reference/gw-counts.json}
    coverage_denominators:
      - {kind: egress_proxy_log, source: urn:src:egress, covers: [ToolCall, ModelCall]}
catalogs: [eu-ai-act@2026.09]
"""


def test_load_profile(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    path.write_text(_EXAMPLE, encoding="utf-8")
    profile = Profile.load(path)
    assert profile.profile_version == 1
    assert profile.catalogs == ["eu-ai-act@2026.09"]
    assert len(profile.subjects) == 1
    subject = profile.subjects[0]
    assert subject.id == "spiffe://corp/agents/claims-triage"
    assert subject.role == "deployer"
    assert subject.declared_oversight == {
        "ins:ClaimDenialRecommendation": "review_before"
    }
    assert subject.evidence_sources[0].manifest == "reference/gw-counts.json"
    assert subject.coverage_denominators[0].covers == ["ToolCall", "ModelCall"]
    assert subject.source_ids == {"urn:src:gw"}


def test_empty_profile() -> None:
    assert Profile.from_dict({}).subjects == []


_WINDOW = 'profile_version: 1\nobservation_window:{}\nsubjects:\n  - id: "a"\ncatalogs: ["eu-ai-act@2026.09"]\n'


@pytest.mark.parametrize(
    "window",
    [
        '\n  start: "2026-01-01T00:00:00Z"\n  end: "2026-02-01T00:00:00Z"',
        '\n  start: "2026-01-01"\n  end: "2026-02-01"',
        "\n  start: 2026-01-01\n  end: 2026-02-01T10:00:00Z",
        "\n  start: 2026-01-01\n  end: 2026-02-01 10:00:00.5",
        " {}",
    ],
)
def test_window_accepted(tmp_path: Path, window: str) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(_WINDOW.format(window), encoding="utf-8")
    assert Profile.load(path).subjects[0].id == "a"


@pytest.mark.parametrize(
    ("window", "named"),
    [
        ('\n  start: "2026-01-01"\n  end: "foo"', "end"),
        ('\n  start: "2026-01-01"\n  end: "2026-02-30"', "end"),
        ('\n  start: "2026-01-01"\n  end: 2026-02-30', "end"),
        ('\n  start: "2026-01-01"\n  end: ""', "end"),
        ('\n  start: "2026-01-01"\n  end: null', "end"),
        ('\n  start: "2026-01-01"\n  end: 5', "end"),
        ('\n  start: "2026-01-01"\n  end: "2026-02-01T24:00:00Z"', "end"),
        ('\n  start: "2026-01-01"\n  end: "2026-02-01T10:00Z"', "end"),
        ('\n  start: "2026-01-01"\n  end: "2026-W05-1"', "end"),
        ('\n  start: "foo"\n  end: "2026-02-01"', "start"),
        (' "2026"', "mapping"),
        (" ~", "mapping"),
        (" []", "mapping"),
    ],
)
def test_window_refused(tmp_path: Path, window: str, named: str) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(_WINDOW.format(window), encoding="utf-8")
    with pytest.raises(InputError) as caught:
        Profile.load(path)
    assert caught.value.key == "input.profile_invalid"
    assert named in caught.value.cause


def test_lint_refuses_unreadable_window_end() -> None:
    entry = {"control": "X-1", "expiry": "2026-06-01"}
    problems = deviation_lint(
        [entry], control_ids={"X-1"}, outcomes_by_control={}, as_of="zzzz"
    )
    assert problems[0] == (
        "the observation window end is not a valid RFC 3339 date ('zzzz'), so no expiry can be checked"
    )
    assert not any(
        p.startswith("the observation")
        for p in deviation_lint(
            [], control_ids=set(), outcomes_by_control={}, as_of="zzzz"
        )
    )
