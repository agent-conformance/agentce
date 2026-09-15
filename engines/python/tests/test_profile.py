"""The applicability profile loader (SPEC §6.5)."""

from __future__ import annotations

from pathlib import Path

from agentce.profile import Profile

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
    assert subject.denominator_ids == {"urn:src:egress"}


def test_empty_profile() -> None:
    assert Profile.from_dict({}).subjects == []
