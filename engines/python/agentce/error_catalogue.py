"""The message-key catalogue: a cause and a fix for every error, warning, quarantine reason, and
``insufficient_evidence`` explanation (SPEC §13.4 AX-6).

Every message the engine surfaces to a user carries a stable key so automation keys on the code, not
the prose. This module is the single *registry* of those keys -- which kind each one is -- and reads
their cause/fix text from the vendored, language-neutral catalogue (``agentce/data/i18n/``, sourced
from ``spec/i18n/``, the same catalogue :mod:`agentce.messages` reads report strings from) so the
English text lives in exactly one place. ``docs/errors.md`` is generated from it, and
``errors_check`` (the phase-4 P4.8 gate) fails if any key lacks a cause or a fix, or if a quarantine
reason or the insufficient-evidence explanation is missing.
"""

from __future__ import annotations

from . import i18n_format
from .quarantine import QuarantineReason


class Entry:
    __slots__ = ("kind", "cause", "fix")

    def __init__(self, kind: str, cause: str, fix: str) -> None:
        self.kind = kind
        self.cause = cause
        self.fix = fix


#: key -> kind. Kinds: error, warning, quarantine, explanation. The cause/fix text for each key comes
#: from the catalogue's ``errors.<key>.cause``/``errors.<key>.fix`` entries, never hardcoded here.
_KINDS: dict[str, str] = {
    # Quarantine reasons (SPEC Appendix F).
    "schema_invalid": "quarantine",
    "duplicate_id": "quarantine",
    "time_order": "quarantine",
    "unknown_type": "quarantine",
    "unknown_source": "quarantine",
    "class_mismatch": "quarantine",
    "oversize": "quarantine",
    "context_mismatch": "quarantine",
    # Outcome explanation.
    "insufficient_evidence": "explanation",
    # Errors.
    "input.bundle_manifest_missing": "error",
    "input.bundle_manifest_mismatch": "error",
    "input.bundle_manifest_file_too_large": "error",
    "input.profile_invalid": "error",
    "input.domain_binding_invalid": "error",
    "input.deviation_invalid": "error",
    "input.coverage_denominator_manifest_invalid": "error",
    "input.event_structure_too_deep": "error",
    "input.for_emit_ambiguous": "error",
    "input.for_preset": "error",
    "input.catalog_missing": "error",
    "input.catalog_unresolved": "error",
    "input.catalog_mismatch": "error",
    "input.catalog_unverified": "error",
    "input.trust_root_invalid": "error",
    "input.catalog_not_found": "error",
    "input.nothing_evaluated": "error",
    "input.init_exists": "error",
    "input.profile_missing": "error",
    "input.records_no_genai_spans": "error",
    "input.records_out_collides": "error",
    "input.records_none_recognised": "error",
    "input.records_not_a_directory": "error",
    "input.records_source_ambiguous": "error",
    "input.records_subject_ambiguous": "error",
    "input.package_requires_bundle": "error",
    "input.package_path_overlap": "error",
    "input.diff_field_not_string": "error",
    "input.diff_extra_argument": "error",
    "input.diff_unrecognized_flag": "error",
    "input.readiness_unrecognized_flag": "error",
    "input.sign_role": "error",
    "input.sign_profile": "error",
    "environment.cryptography_unavailable": "error",
    "environment.python_unsupported": "error",
    "internal.unexpected": "error",
    "sign.trust_root_requires_kms": "error",
    "sign.key_unreadable": "error",
    "sign.not_ready": "error",
    "sign.no_claim": "error",
    "sign.kms_key_missing": "error",
    "sign.key_algorithm": "error",
    "sign.keyless_offline": "error",
    "catalog.init_family_invalid": "error",
    "catalog.init_family_collision": "error",
    "catalog.init_exists": "error",
    "catalog.sign_not_a_catalog": "error",
    "catalog.sign_lint_failed": "error",
    "catalog.sign_key_missing": "error",
    "catalog.sign_key_algorithm": "error",
    "catalog.sign_key_unreadable": "error",
    "catalog.sign_key_inside_catalog": "error",
    "catalog.sign_new_key_exists": "error",
    "catalog.sign_exists": "error",
    "catalog.sign_trust_root_inside_catalog": "error",
    "catalog.support_matrix_multi": "error",
    "catalog.support_matrix_inside_catalog": "error",
    "verify.report_no_claim": "error",
    "verify.report_claim_malformed": "error",
    "verify.report_unsigned": "error",
    "verify.report_no_trust_root": "error",
    "verify.report_keyid_mismatch": "error",
    "verify.report_signature_invalid": "error",
    "verify.report_subject_missing": "error",
    "verify.report_manifest_tampered": "error",
    "verify.report_claim_tampered": "error",
    "verify.report_engine_mismatch": "error",
    "verify.report_output_tampered": "error",
    "verify.report_evidence_tampered": "error",
    "verify.report_reproduction_mismatch": "error",
    # Warnings.
    "environment.cryptography_source_build": "warning",
    "warning.pilot_window": "warning",
    "warning.stale_component": "warning",
}


def _load_message_keys() -> dict[str, Entry]:
    catalog = i18n_format.load_catalog("en")
    entries: dict[str, Entry] = {}
    for key, kind in _KINDS.items():
        entries[key] = Entry(
            kind,
            catalog.get(f"errors.{key}.cause", ""),
            catalog.get(f"errors.{key}.fix", ""),
        )
    return entries


#: key -> Entry(kind, cause, fix), built from the vendored catalogue at import time.
MESSAGE_KEYS: dict[str, Entry] = _load_message_keys()


def catalogue_gaps() -> list[str]:
    """Keys whose cause or fix is empty (the P4.8 completeness check), plus any required key missing."""
    problems: list[str] = []
    for key, entry in sorted(MESSAGE_KEYS.items()):
        if not entry.cause.strip():
            problems.append(f"{key}: no cause")
        if not entry.fix.strip():
            problems.append(f"{key}: no fix")
    for reason in QuarantineReason:
        if reason.value not in MESSAGE_KEYS:
            problems.append(f"quarantine reason {reason.value} has no catalogue entry")
    if "insufficient_evidence" not in MESSAGE_KEYS:
        problems.append("insufficient_evidence has no catalogue entry")
    return problems


def render_errors_md() -> str:
    """Render ``docs/errors.md`` from the catalogue: one table per kind, keys sorted."""
    kinds = {
        "error": "Errors",
        "warning": "Warnings",
        "quarantine": "Quarantine reasons",
        "explanation": "Outcome explanations",
    }
    lines = [
        "# Message keys",
        "",
        "Every message the engine surfaces carries a stable key. This catalogue is generated by "
        "`agentce doctor --write-errors`; do not edit it by hand (SPEC §13.4).",
    ]
    for kind, heading in kinds.items():
        entries = sorted((k, e) for k, e in MESSAGE_KEYS.items() if e.kind == kind)
        if not entries:
            continue
        lines += ["", f"## {heading}", "", "| Key | Cause | Fix |", "|---|---|---|"]
        lines += [
            f"| `{key}` | {entry.cause} | {entry.fix} |" for key, entry in entries
        ]
    return "\n".join(lines) + "\n"
