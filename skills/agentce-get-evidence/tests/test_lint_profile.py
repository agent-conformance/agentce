"""lint_profile: the trust-class, confirmation, and task-scope rules (SPEC 13.3.3, 7.7.5, S-2/S-8)."""

from __future__ import annotations

from pathlib import Path

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


_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "task-scope.template.yaml"


def _codes(profile: object, domain: object = None) -> set[str]:
    return {f.code for f in lint_profile.lint(profile, domain)}


def _conduct_profile() -> dict:  # type: ignore[type-arg]
    profile = yaml.safe_load(_CLEAN)
    profile["catalogs"] = ["eu-ai-act@2026.09", "conduct@2026.09"]
    return profile  # type: ignore[no-any-return]


def test_conduct_overlay_requires_task_scopes_in_the_binding() -> None:
    profile = _conduct_profile()
    assert "lint.conduct_overlay_missing_task_scope" in _codes(profile)
    for domain in (
        {},
        {"task_scopes": {}},
        {"task_scopes": [{"allowed_effect_classes": ["read"]}]},
        [],
    ):
        assert "lint.conduct_overlay_missing_task_scope" in _codes(profile, domain)
    domain = {"task_scopes": [{"task_type": "t", "allowed_effect_classes": ["read"]}]}
    assert _codes(profile, domain) == set()
    # only a conduct@ catalog entry selects the overlay, as in the engine; overlays is reported, never read
    legacy = yaml.safe_load(_CLEAN)
    legacy["overlays"] = ["conduct"]
    assert _codes(legacy) == {"lint.overlays_not_read"}
    assert _codes(yaml.safe_load(_CLEAN)) == set()


def test_task_scopes_in_the_profile_are_reported() -> None:
    profile = _conduct_profile()
    profile["task_types"] = ["disburse_credit"]
    profile["task_scopes"] = {"disburse_credit": {"allowed_effect_classes": ["read"]}}
    findings = [
        f for f in lint_profile.lint(profile) if f.code == "lint.task_scope_in_profile"
    ]
    assert sorted(f.details["key"] for f in findings) == ["task_scopes", "task_types"]
    # reported whether or not the overlay is selected, since the engine reads neither key
    plain = yaml.safe_load(_CLEAN)
    plain["task_scopes"] = {}
    assert "lint.task_scope_in_profile" in _codes(plain)


def test_an_approval_class_must_also_be_allowed() -> None:
    domain = {
        "task_scopes": [
            {
                "task_type": "t",
                "allowed_effect_classes": ["read"],
                "requires_approval": ["spend", "read"],
            }
        ]
    }
    findings = [
        f
        for f in lint_profile.lint(_conduct_profile(), domain)
        if f.code == "lint.task_scope_approval_not_allowed"
    ]
    assert [f.details["effect_class"] for f in findings] == ["spend"]


def test_an_unreadable_task_scope_entry_is_reported() -> None:
    domain = yaml.safe_load(
        """
task_scopes:
  - {task-type: "2024", allowed_effect_classes: [read]}
  - {task_type: 2024, allowed_effect_classes: [read]}
  - nope
  - {task_type: batch, allowed_effect_classes: [read, irreversible]}
"""
    )
    findings = [
        f
        for f in lint_profile.lint(_conduct_profile(), domain)
        if f.code == "lint.task_scope_entry_malformed"
    ]
    assert [f.details["entry"] for f in findings] == [1, 2, 3]


def test_the_template_filled_in_is_clean() -> None:
    text = _TEMPLATE.read_text(encoding="utf-8")
    for blank, value in (
        ("<task-type>", "credit-review"),
        ("<tool-name>", "lookup"),
    ):
        text = text.replace(blank, value)
    assert _codes(_conduct_profile(), yaml.safe_load(text)) == set()


def test_domain_flag(tmp_path) -> None:  # type: ignore[no-untyped-def]
    profile = tmp_path / "p.yaml"
    profile.write_text(yaml.safe_dump(_conduct_profile()), encoding="utf-8")
    domain = tmp_path / "domain.yaml"
    domain.write_text(
        "task_scopes:\n  - {task_type: t, allowed_effect_classes: [read]}\n",
        encoding="utf-8",
    )
    assert lint_profile.main(["--profile", str(profile)]) == lint_profile.FINDINGS
    assert (
        lint_profile.main(["--profile", str(profile), "--domain", str(domain)])
        == lint_profile.OK
    )
    assert (
        lint_profile.main(["--profile", str(profile), "--domain"])
        == lint_profile.INPUT_ERROR
    )
    assert (
        lint_profile.main(
            ["--profile", str(profile), "--domain", str(tmp_path / "missing.yaml")]
        )
        == lint_profile.INPUT_ERROR
    )


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
