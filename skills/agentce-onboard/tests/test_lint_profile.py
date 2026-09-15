"""lint_profile: the trust-class, confirmation, and task-scope rules (SPEC 13.3.3, 7.7.5, S-2/S-8)."""

from __future__ import annotations

import yaml

import lint_profile

_CLEAN = """
profile_version: 1
observation_window: {start: "2026-05-01T00:00:00Z", end: "2026-08-29T00:00:00Z"}
subjects:
  - id: spiffe://corp/agents/credit
    declared_decision_types: [dom:CreditDecision]
    confirmations:
      decision_typing: {confirmed_by: "risk@corp"}
    evidence_sources:
      - {adapter: otel-genai, source: urn:otel:x, class: self_report, class_justification: "agent runtime"}
"""


def test_self_test_passes() -> None:
    assert lint_profile.main(["--self-test"]) == lint_profile.OK


def test_a_clean_profile_has_no_findings() -> None:
    assert lint_profile.lint(yaml.safe_load(_CLEAN)) == []


def test_self_report_kind_declared_as_enforcement_is_rejected() -> None:
    profile = yaml.safe_load(_CLEAN)
    profile["subjects"][0]["evidence_sources"].append(
        {
            "kind": "in_process_approval",
            "adapter": "oversight",
            "source": "urn:agent:approvals",
            "class": "enforcement_point",
            "class_justification": "in-process prompt",
        }
    )
    codes = {f.code for f in lint_profile.lint(profile)}
    assert "lint.self_report_as_enforcement" in codes


def test_short_window_without_pilot_is_flagged() -> None:
    profile = yaml.safe_load(_CLEAN)
    profile["observation_window"] = {
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-08-10T00:00:00Z",
    }
    codes = {f.code for f in lint_profile.lint(profile)}
    assert "lint.observation_window_too_short" in codes
    profile["pilot_window"] = True
    assert "lint.observation_window_too_short" not in {
        f.code for f in lint_profile.lint(profile)
    }


def test_decision_typing_requires_confirmation() -> None:
    profile = yaml.safe_load(_CLEAN)
    del profile["subjects"][0]["confirmations"]
    codes = {f.code for f in lint_profile.lint(profile)}
    assert "lint.missing_confirmation" in codes


def test_enforcement_point_needs_a_manifest() -> None:
    profile = yaml.safe_load(_CLEAN)
    profile["subjects"][0]["evidence_sources"].append(
        {
            "adapter": "mcp-gateway",
            "source": "urn:mcp:x",
            "class": "enforcement_point",
            "class_justification": "gw",
        }
    )
    codes = {f.code for f in lint_profile.lint(profile)}
    assert "lint.enforcement_point_missing_manifest" in codes


def test_conduct_overlay_requires_task_scopes() -> None:
    profile = yaml.safe_load(_CLEAN)
    profile["overlays"] = ["conduct"]
    profile["task_types"] = ["disburse_credit"]
    profile["task_scopes"] = {}
    assert "lint.conduct_overlay_missing_task_scope" in {
        f.code for f in lint_profile.lint(profile)
    }
    profile["task_scopes"] = {"disburse_credit": {"allowed_effect_classes": ["read"]}}
    assert "lint.conduct_overlay_missing_task_scope" not in {
        f.code for f in lint_profile.lint(profile)
    }


def test_a_profile_file_is_linted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    good = tmp_path / "p.yaml"
    good.write_text(_CLEAN, encoding="utf-8")
    assert lint_profile.main(["--profile", str(good)]) == lint_profile.OK
    assert (
        lint_profile.main(["--profile", str(tmp_path / "missing.yaml")])
        == lint_profile.INPUT_ERROR
    )


def test_shipped_applicability_template_lints_clean() -> None:
    from pathlib import Path

    template = (
        Path(__file__).resolve().parent.parent
        / "assets"
        / "applicability.template.yaml"
    )
    assert lint_profile.main(["--profile", str(template)]) == lint_profile.OK
