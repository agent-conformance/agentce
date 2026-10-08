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
    "input.assess_flag_needs_value": "error",
    "input.coverage_denominator_manifest_invalid": "error",
    "input.event_structure_too_deep": "error",
    "input.for_emit_ambiguous": "error",
    "input.for_preset": "error",
    "input.emit_unsupported": "error",
    "input.assess_unrecognized_flag": "error",
    "input.catalog_missing": "error",
    "input.catalog_unresolved": "error",
    "input.catalog_mismatch": "error",
    "input.catalog_unverified": "error",
    "input.trust_root_invalid": "error",
    "input.digest_unreadable": "error",
    "input.report_format": "error",
    "input.emit_format": "error",
    "input.fail_on_invalid_expression": "error",
    "input.collect_config": "error",
    "input.catalog_not_found": "error",
    "input.nothing_evaluated": "error",
    "input.init_exists": "error",
    "input.profile_missing": "error",
    "input.records_no_genai_spans": "error",
    "input.records_out_collides": "error",
    "input.records_none_recognised": "error",
    "input.records_not_a_directory": "error",
    "input.records_source_ambiguous": "error",
    "input.package_requires_bundle": "error",
    "input.package_path_overlap": "error",
    "input.diff_field_not_string": "error",
    "input.diff_extra_argument": "error",
    "input.diff_unrecognized_flag": "error",
    "input.quickstart_unrecognized_flag": "error",
    "input.readiness_unrecognized_flag": "error",
    "input.sign_role": "error",
    "input.sign_profile": "error",
    "input.sign_unrecognized_flag": "error",
    "input.verify_unrecognized_flag": "error",
    "environment.cryptography_unavailable": "error",
    "environment.python_unsupported": "error",
    "internal.unexpected": "error",
    "sign.trust_root_requires_kms": "error",
    "sign.key_unreadable": "error",
    "sign.not_ready": "error",
    "sign.no_claim": "error",
    "sign.claim_malformed": "error",
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
    "catalog.shape.sparql_forbidden": "error",
    "catalog.shape.script_forbidden": "error",
    "catalog.shape.outside_profile": "error",
    "catalog.shape.parse_error": "error",
    "verify.certificate_algorithm": "error",
    "verify.certificate_validity_malformed": "error",
    "verify.certificate_validity_inverted": "error",
    # verify's verified:false refusals (18.64): each cause is the exact reason text.
    "verify.certificate_signature_invalid": "error",
    "verify.certificate_issuer_unknown": "error",
    "verify.keyid_untrusted": "error",
    "verify.envelope_malformed": "error",
    "verify.envelope_no_signatures": "error",
    "verify.signature_sig_missing": "error",
    "verify.signature_not_base64": "error",
    "verify.signature_invalid": "error",
    "verify.no_signature_verified": "error",
    "verify.statement_unreadable": "error",
    "verify.statement_no_digest": "error",
    "verify.catalog_unsigned": "error",
    "verify.catalog_signature_unreadable": "error",
    "verify.catalog_digest_mismatch": "error",
    "verify.release_envelope_unreadable": "error",
    "verify.release_manifest_unreadable": "error",
    "verify.release_manifest_uncanonical": "error",
    "verify.release_artifact_unnamed": "error",
    "verify.release_artifact_missing": "error",
    "verify.release_artifact_digest": "error",
    "verify.release_signatures_unreadable": "error",
    "verify.release_signature": "error",
    "verify.release_signature_not_object": "error",
    "verify.release_envelope_missing": "error",
    "verify.release_manifest_not_covered": "error",
    "verify.release_no_signatures": "error",
    "verify.json_too_deep": "error",
    "verify.report_statement_shape": "error",
    "verify.report_role_not_claimant": "error",
    "verify.report_no_claimant_entry": "error",
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
    # Further raised refusals (VG-UX-NEXT-STEP checks every literal-key raise is registered).
    "report.missing_evidence_pointer": "error",
    "input.bundle_manifest_path": "error",
    "input.bundle_manifest_files": "error",
    "input.bundle_manifest_entry": "error",
    "input.bundle_manifest_invalid": "error",
    "input.release_missing": "error",
    "input.catalog_unreadable": "error",
    "input.bundle_unreadable": "error",
    "input.release_unreadable": "error",
    "verify.report_unreadable": "error",
    "input.release_bundle": "error",
    "input.adapter_not_found": "error",
    "input.adapter_missing": "error",
    "input.ingest_empty": "error",
    "input.ingest_failed": "error",
    "input.catalog_action": "error",
    "input.conformance_action": "error",
    "input.diff_format": "error",
    "input.quickstart_missing": "error",
    "input.init_role": "error",
    "input.config_action": "error",
    "input.corpus_generate_failed": "error",
    "input.corpus_not_found": "error",
    "input.state_version_incompatible": "error",
    "input.state_dir_unwritable": "error",
    "input.verify_target": "error",
    "input.out_missing": "error",
    "input.out_dir_unwritable": "error",
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
