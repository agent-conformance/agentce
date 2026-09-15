"""lint_profile (SPEC 13.3.3 step 7, 7.7.5, S-2/S-8): check an applicability profile before assessment.

The lint holds a profile to the rules the engine cannot check for the operator: every declared source
carries a trust class and a justification; an enforcement point names a coverage manifest (or ``none``
with a reason); a source whose kind is a known self-report pattern -- a framework interrupt, an SDK
tracing processor, an in-process approval prompt, an agent-written log -- is **never** declared
anything but ``self_report`` (rule S-2, SPEC 6.9); the observation window is at least 90 days unless a
pilot is declared; classification, decision typing, and oversight are ``confirmed_by`` a named person
(rule S-8); and, when the Conduct overlay is targeted, every declared task type has an effect scope
(SPEC 7.7.5).

``--self-test`` lints a built-in clean profile and a built-in defective one and confirms the lint
accepts the first and rejects the second (printing ``LINT PROFILE SELF-TEST OK``). Deterministic,
offline, model-free (rule S-4). Exit 0 when clean, 1 on findings, 2 on input error, 3 on version mismatch.
"""

from __future__ import annotations

import datetime
import sys
from typing import Any

import yaml

from _common import FINDINGS, INPUT_ERROR, OK, Finding, arg_value, emit, run_guarded

_CLASSES = frozenset({"self_report", "enforcement_point", "independent_system"})
#: Source kinds that are self-reports by construction; declaring them otherwise is a finding (SPEC 6.9).
_SELF_REPORT_KINDS = frozenset(
    {"framework_interrupt", "sdk_tracing", "in_process_approval", "agent_log"}
)
_MIN_WINDOW_DAYS = 90


def _days(window: dict[str, Any]) -> int | None:
    try:
        start = datetime.datetime.fromisoformat(
            str(window["start"]).replace("Z", "+00:00")
        )
        end = datetime.datetime.fromisoformat(str(window["end"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    return (end - start).days


def lint(profile: object) -> list[Finding]:
    """Return the findings for ``profile`` (an empty list means it is clean)."""
    findings: list[Finding] = []
    if not isinstance(profile, dict):
        return [Finding("lint.not_a_mapping", "the profile is not a YAML mapping")]

    window = profile.get("observation_window")
    days = _days(window) if isinstance(window, dict) else None
    if not profile.get("pilot_window"):
        if days is None:
            findings.append(
                Finding(
                    "lint.observation_window_missing", "no valid observation_window"
                )
            )
        elif days < _MIN_WINDOW_DAYS:
            findings.append(
                Finding(
                    "lint.observation_window_too_short",
                    f"observation window is {days} days (< {_MIN_WINDOW_DAYS}); set pilot_window: true if intended",
                    {"days": days},
                )
            )

    subjects = profile.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        findings.append(Finding("lint.no_subjects", "the profile declares no subjects"))
        subjects = []

    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        sid = str(subject.get("id", "<unknown>"))
        findings.extend(_lint_sources(sid, subject.get("evidence_sources")))
        findings.extend(_lint_confirmations(sid, subject))

    findings.extend(_lint_conduct(profile))
    return findings


def _lint_sources(sid: str, sources: object) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(sources, list):
        return [
            Finding(
                "lint.no_sources",
                f"subject {sid} declares no evidence sources",
                {"subject": sid},
            )
        ]
    for entry in sources:
        if not isinstance(entry, dict):
            continue
        ref = str(entry.get("source", entry.get("adapter", "<source>")))
        cls = str(entry.get("class", ""))
        detail = {"subject": sid, "source": ref}
        if cls not in _CLASSES:
            findings.append(
                Finding(
                    "lint.source_bad_class",
                    f"source {ref} has no valid trust class",
                    detail,
                )
            )
        if not entry.get("class_justification"):
            findings.append(
                Finding(
                    "lint.source_missing_justification",
                    f"source {ref} has no class_justification",
                    detail,
                )
            )
        kind = str(entry.get("kind", ""))
        if kind in _SELF_REPORT_KINDS and cls != "self_report":
            findings.append(
                Finding(
                    "lint.self_report_as_enforcement",
                    f"source {ref} is a {kind} (a self-report) but is declared {cls!r}",
                    {**detail, "kind": kind, "declared_class": cls},
                )
            )
        if (
            cls == "enforcement_point"
            and not entry.get("manifest")
            and not entry.get("manifest_reason")
        ):
            findings.append(
                Finding(
                    "lint.enforcement_point_missing_manifest",
                    f"enforcement point {ref} needs a coverage manifest or manifest_reason",
                    detail,
                )
            )
    return findings


def _lint_confirmations(sid: str, subject: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    confirmations = subject.get("confirmations")
    confirmations = confirmations if isinstance(confirmations, dict) else {}
    required = []
    if subject.get("annex_iii_category") or subject.get("classification_status"):
        required.append("classification")
    if subject.get("declared_decision_types"):
        required.append("decision_typing")
    if subject.get("declared_oversight"):
        required.append("oversight")
    for declaration in required:
        entry = confirmations.get(declaration)
        confirmed = entry.get("confirmed_by") if isinstance(entry, dict) else entry
        if not confirmed:
            findings.append(
                Finding(
                    "lint.missing_confirmation",
                    f"subject {sid} needs a confirmed_by for {declaration} (S-8)",
                    {"subject": sid, "declaration": declaration},
                )
            )
    return findings


def _lint_conduct(profile: dict[str, Any]) -> list[Finding]:
    overlays = profile.get("overlays")
    overlays = overlays if isinstance(overlays, list) else []
    if "conduct" not in overlays:
        return []
    task_types = profile.get("task_types")
    task_types = [str(t) for t in task_types] if isinstance(task_types, list) else []
    scopes = profile.get("task_scopes")
    scopes = scopes if isinstance(scopes, dict) else {}
    return [
        Finding(
            "lint.conduct_overlay_missing_task_scope",
            f"the Conduct overlay is targeted but task type {task!r} has no effect scope (SPEC 7.7.5)",
            {"task_type": task},
        )
        for task in task_types
        if task not in scopes
    ]


_GOOD_PROFILE = """
profile_version: 1
observation_window: {start: "2026-05-01T00:00:00Z", end: "2026-08-29T00:00:00Z"}
catalogs: ["eu-ai-act@2026.09"]
overlays: [conduct]
task_types: [disburse_credit]
task_scopes:
  disburse_credit: {allowed_effect_classes: [read, write], approval_required: [spend]}
subjects:
  - id: spiffe://corp/agents/credit
    declared_decision_types: [dom:CreditDecision]
    declared_oversight: {dom:CreditDecision: review_before}
    classification_status: draft_for_legal_review
    confirmations:
      classification: {confirmed_by: "risk-officer@corp"}
      decision_typing: {confirmed_by: "risk-officer@corp"}
      oversight: {confirmed_by: "risk-officer@corp"}
    evidence_sources:
      - {adapter: otel-genai, source: urn:otel:credit, class: self_report, class_justification: "agent runtime"}
      - {adapter: mcp-gateway, source: urn:mcp:credit, class: enforcement_point, class_justification: "sole egress", manifest: reference/gw.json}
"""

_BAD_PROFILE = """
profile_version: 1
observation_window: {start: "2026-08-01T00:00:00Z", end: "2026-08-10T00:00:00Z"}
catalogs: ["eu-ai-act@2026.09"]
overlays: [conduct]
task_types: [disburse_credit]
task_scopes: {}
subjects:
  - id: spiffe://corp/agents/credit
    declared_decision_types: [dom:CreditDecision]
    evidence_sources:
      - {kind: in_process_approval, adapter: oversight, source: urn:agent:approvals, class: enforcement_point, class_justification: "in-process prompt"}
"""


def _self_test(want_json: bool) -> int:
    good = lint(yaml.safe_load(_GOOD_PROFILE))
    bad_codes = {f.code for f in lint(yaml.safe_load(_BAD_PROFILE))}
    expected_in_bad = {
        "lint.self_report_as_enforcement",
        "lint.conduct_overlay_missing_task_scope",
        "lint.observation_window_too_short",
        "lint.missing_confirmation",
    }
    ok = not good and expected_in_bad <= bad_codes
    payload = {
        "self_test": "ok" if ok else "failed",
        "good_findings": [f.code for f in good],
        "bad_findings": sorted(bad_codes),
    }
    emit(
        payload,
        want_json=want_json,
        human="LINT PROFILE SELF-TEST OK" if ok else "LINT PROFILE SELF-TEST FAILED",
    )
    return OK if ok else FINDINGS


def _body(argv: list[str]) -> int:
    want_json = "--json" in argv
    if "--self-test" in argv:
        return _self_test(want_json)
    path = arg_value(argv, "--profile")
    if path is None:
        emit(
            {"status": "input_error", "message": "pass --profile <file>"},
            want_json=want_json,
            human="INPUT ERROR: pass --profile <file>",
        )
        return INPUT_ERROR
    try:
        profile = yaml.safe_load(open(path, encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        emit(
            {"status": "input_error", "message": str(exc)},
            want_json=want_json,
            human=f"INPUT ERROR: {exc}",
        )
        return INPUT_ERROR
    findings = lint(profile)
    payload = {
        "profile": path,
        "clean": not findings,
        "findings": [f.to_json() for f in findings],
    }
    human = (
        "LINT PROFILE OK"
        if not findings
        else f"LINT PROFILE: {len(findings)} finding(s)"
    )
    emit(payload, want_json=want_json, human=human)
    return OK if not findings else FINDINGS


def main(argv: list[str] | None = None) -> int:
    return run_guarded(list(sys.argv[1:] if argv is None else argv), _body)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
