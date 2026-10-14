"""lint_profile (SPEC 13.3.3 step 7, 7.7.5, S-2/S-8): check an applicability profile before assessment.

The lint holds a profile to the rules the engine cannot check for the operator: every declared source
carries a trust class and a justification; an enforcement point names a coverage manifest (or ``none``
with a reason); a source whose kind is a known self-report pattern -- a framework interrupt, an SDK
tracing processor, an in-process approval prompt, an agent-written log -- is **never** declared
anything but ``self_report`` (rule S-2, SPEC 6.9); the observation window is at least 90 days unless a
pilot is declared; classification, decision typing, and oversight are ``confirmed_by`` a named person
(rule S-8); and, when the profile selects the Conduct overlay (a ``conduct@`` catalog entry), the domain
binding passed with ``--domain`` declares task effect scopes in the form the engine reads (SPEC 7.7.5, 7.7.6).

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


def lint(profile: object, domain: object = None) -> list[Finding]:
    """Return the findings for ``profile`` and its domain binding (``None`` when none was passed); an
    empty list means both are clean."""
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

    findings.extend(_lint_conduct(profile, domain))
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


def _selects_conduct(profile: dict[str, Any]) -> bool:
    catalogs = profile.get("catalogs")
    return any(
        isinstance(c, str) and c.split("@", 1)[0] == "conduct"
        for c in (catalogs if isinstance(catalogs, list) else [])
    )


def _lint_conduct(profile: dict[str, Any], domain: object) -> list[Finding]:
    findings = [
        Finding(
            "lint.task_scope_in_profile",
            f"the profile declares {key}, which the engine never reads: task effect scopes go in the "
            "domain binding's task_scopes list (SPEC 7.7.6)",
            {"key": key},
        )
        for key in ("task_types", "task_scopes")
        if key in profile
    ]
    if "overlays" in profile:
        findings.append(
            Finding(
                "lint.overlays_not_read",
                "the profile declares overlays, which the engine never reads: select an overlay with its "
                'catalog entry, for the Conduct overlay "conduct@2026.09" in catalogs',
            )
        )
    if not _selects_conduct(profile):
        return findings
    scopes = domain.get("task_scopes") if isinstance(domain, dict) else None
    entries = []
    for number, e in enumerate(scopes if isinstance(scopes, list) else [], 1):
        if isinstance(e, dict) and isinstance(e.get("task_type"), str):
            entries.append(e)
        else:
            findings.append(
                Finding(
                    "lint.task_scope_entry_malformed",
                    f"task_scopes entry {number} is not a mapping with a quoted task_type string, so "
                    "the engine cannot read it and holds every task it may name to no scope (SPEC 7.7.6)",
                    {"entry": number},
                )
            )
    if not entries:
        where = (
            "the domain binding"
            if domain is not None
            else "no domain binding (pass --domain)"
        )
        findings.append(
            Finding(
                "lint.conduct_overlay_missing_task_scope",
                f"the Conduct overlay is selected but {where} declares no task_scopes entry with a "
                "task_type, so CND-01 reads every tool call in a task out of scope (SPEC 7.7.5, 7.7.6)",
            )
        )
    for entry in entries:
        allowed = entry.get("allowed_effect_classes")
        allowed = allowed if isinstance(allowed, list) else []
        gated = entry.get("requires_approval")
        findings.extend(
            Finding(
                "lint.task_scope_approval_not_allowed",
                f"task type {entry['task_type']!r} marks {cls!r} requires_approval but does not list it in "
                "allowed_effect_classes, so CND-01 reads every such call out of scope; list it in both",
                {"task_type": entry["task_type"], "effect_class": cls},
            )
            for cls in (gated if isinstance(gated, list) else [])
            if cls not in allowed
        )
    return findings


_GOOD_PROFILE = """
profile_version: 1
observation_window: {start: "2026-05-01T00:00:00Z", end: "2026-08-29T00:00:00Z"}
catalogs: ["eu-ai-act@2026.09", "conduct@2026.09"]
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
catalogs: ["eu-ai-act@2026.09", "conduct@2026.09"]
overlays: [conduct]
task_scopes: {}
subjects:
  - id: spiffe://corp/agents/credit
    declared_decision_types: [dom:CreditDecision]
    evidence_sources:
      - {kind: in_process_approval, adapter: oversight, source: urn:agent:approvals, class: enforcement_point, class_justification: "in-process prompt"}
"""


_GOOD_DOMAIN = """
task_scopes:
  - {task_type: disburse_credit, allowed_effect_classes: [read, write, spend], requires_approval: [spend]}
"""

_BAD_DOMAIN = """
task_scopes:
  - {task_type: disburse_credit, allowed_effect_classes: [read], requires_approval: [spend]}
  - {task-type: refund, allowed_effect_classes: [read]}
"""


def _self_test(want_json: bool) -> int:
    good = lint(yaml.safe_load(_GOOD_PROFILE), yaml.safe_load(_GOOD_DOMAIN))
    bad_codes = {f.code for f in lint(yaml.safe_load(_BAD_PROFILE))} | {
        f.code for f in lint(yaml.safe_load(_GOOD_PROFILE), yaml.safe_load(_BAD_DOMAIN))
    }
    expected_in_bad = {
        "lint.self_report_as_enforcement",
        "lint.conduct_overlay_missing_task_scope",
        "lint.task_scope_in_profile",
        "lint.task_scope_approval_not_allowed",
        "lint.task_scope_entry_malformed",
        "lint.overlays_not_read",
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


_FLAGS = frozenset({"--json", "--self-test"})
_VALUED = frozenset({"--profile", "--domain"})


def _refused_args(argv: list[str]) -> str | None:
    """Why the arguments are refused (an unknown argument, a repeated option, or --self-test with a
    file), else None."""
    seen: list[str] = []
    rest = iter(argv)
    for arg in rest:
        if arg in _VALUED:
            if arg in seen:
                return f"{arg} given twice"
            seen.append(arg)
            next(rest, None)
        elif arg not in _FLAGS:
            return f"unknown argument {arg!r}"
    if "--self-test" in argv and seen:
        return f"--self-test lints built-in profiles and takes no {' or '.join(seen)}"
    return None


def _input_error(message: str, want_json: bool) -> int:
    emit(
        {"status": "input_error", "message": message},
        want_json=want_json,
        human=f"INPUT ERROR: {message}",
    )
    return INPUT_ERROR


def _body(argv: list[str]) -> int:
    want_json = "--json" in argv
    refused = _refused_args(argv)
    if refused is not None:
        return _input_error(refused, want_json)
    if "--self-test" in argv:
        return _self_test(want_json)
    path = arg_value(argv, "--profile")
    if path is None:
        return _input_error("pass --profile <file>", want_json)
    domain_path = arg_value(argv, "--domain")
    if "--domain" in argv and domain_path is None:
        return _input_error("pass --domain <file>", want_json)
    try:
        profile = yaml.safe_load(open(path, encoding="utf-8"))
        domain = (
            None
            if domain_path is None
            else yaml.safe_load(open(domain_path, encoding="utf-8"))
        )
    except (OSError, yaml.YAMLError) as exc:
        return _input_error(str(exc), want_json)
    findings = lint(profile, domain)
    payload = {
        "profile": path,
        "domain": domain_path,
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
