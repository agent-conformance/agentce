"""The message-key catalogue: a cause and a fix for every error, warning, quarantine reason, and
``insufficient_evidence`` explanation (SPEC §13.4 AX-6).

Every message the engine surfaces to a user carries a stable key so automation keys on the code, not
the prose. This module is the single registry of those keys with a one-sentence cause and an
actionable fix; ``docs/errors.md`` is generated from it, and ``errors_check`` (the phase-4 P4.8 gate)
fails if any key lacks a cause or a fix, or if a quarantine reason or the insufficient-evidence
explanation is missing.
"""

from __future__ import annotations

from .quarantine import QuarantineReason


class Entry:
    __slots__ = ("kind", "cause", "fix")

    def __init__(self, kind: str, cause: str, fix: str) -> None:
        self.kind = kind
        self.cause = cause
        self.fix = fix


#: key -> (kind, cause, fix). Kinds: error, warning, quarantine, explanation.
MESSAGE_KEYS: dict[str, Entry] = {
    # Quarantine reasons (SPEC Appendix F).
    "schema_invalid": Entry(
        "quarantine",
        "an event failed schema validation and was not ingested.",
        "fix the emitter or adapter so the event matches agentce-evidence.schema.json.",
    ),
    "duplicate_id": Entry(
        "quarantine",
        "two events share the same id; the later one was quarantined.",
        "give every event a stable, unique id at the source.",
    ),
    "time_order": Entry(
        "quarantine",
        "an event's timestamp is out of order within its stream.",
        "emit events in time order, or correct the source clock.",
    ),
    "unknown_type": Entry(
        "quarantine",
        "the event's @type is not a recognised evidence type.",
        "map the event to a canonical evidence type in the adapter (SPEC §12).",
    ),
    "unknown_source": Entry(
        "quarantine",
        "the event's source is not declared in the applicability profile.",
        "declare the source under the subject's evidence_sources with its trust class.",
    ),
    "class_mismatch": Entry(
        "quarantine",
        "the event's source class differs from the class declared for its source.",
        "align the emitted agentcesourceclass with the profile's declared class.",
    ),
    "oversize": Entry(
        "quarantine",
        "the event exceeds the size limit and was refused.",
        "reference bulk content by an opaque locator instead of inlining it (SPEC R12).",
    ),
    "context_mismatch": Entry(
        "quarantine",
        "the event's JSON-LD @context is not the canonical evidence context.",
        "set @context to https://agent-conformance.org/contexts/evidence/v1.",
    ),
    # Outcome explanation.
    "insufficient_evidence": Entry(
        "explanation",
        "the minimum evidence to judge the control was not present in the window.",
        "instrument the missing corroborating stream (see the control's techniques).",
    ),
    # Errors.
    "input.bundle_manifest_missing": Entry(
        "error",
        "the evidence bundle has no manifest.json.",
        "an agent writes a bundle by running with the agentce_emit emitter on: set "
        "`AGENTCE_EMIT=1 AGENTCE_EMIT_OUT=<dir>` and see docs/integrate.md; to watch one built, run "
        "`examples/custom-loop/run.sh <dir>` from a checkout, then `agentce validate --bundle <dir>`.",
    ),
    "input.bundle_manifest_mismatch": Entry(
        "error",
        "a stream file's digest does not match the manifest.",
        "regenerate the manifest after any change to the stream files.",
    ),
    "input.bundle_manifest_file_too_large": Entry(
        "error",
        "a manifest-listed file is over the per-file size limit.",
        "split large evidence into more, smaller files, or reference bulk content by an opaque "
        "locator instead of inlining it (SPEC R12).",
    ),
    "input.profile_invalid": Entry(
        "error",
        "the applicability profile is not safe YAML: a disallowed construct, or a structure "
        "nested too deeply to parse safely.",
        "remove custom YAML tags and flatten deep nesting; regenerate the profile from "
        "`agentce init`.",
    ),
    "input.domain_binding_invalid": Entry(
        "error",
        "the domain binding is not safe YAML: a disallowed construct, or a structure nested too "
        "deeply to parse safely.",
        "remove custom YAML tags and flatten deep nesting in the domain binding file.",
    ),
    "input.event_structure_too_deep": Entry(
        "error",
        "an evidence event line is nested too deeply to parse safely.",
        "flatten the event's structure; reference deeply nested content by an opaque locator "
        "instead (SPEC R12).",
    ),
    "input.catalog_missing": Entry(
        "error",
        "assess was given no catalog to evaluate and the profile declares none.",
        "pass --catalog <id@version>, or list the catalogs to apply under `catalogs:` in the profile.",
    ),
    "input.catalog_unresolved": Entry(
        "error",
        "a requested catalog id@version does not resolve to any catalog directory.",
        "use an available <id>@<version>, or pass --catalog-dir <dir> for a catalog on disk.",
    ),
    "input.catalog_mismatch": Entry(
        "error",
        "a --catalog-dir carries an id@version the --catalog request did not name.",
        "pass --catalog-dir for the catalog you named, or name the id@version the directory carries.",
    ),
    "input.catalog_unverified": Entry(
        "error",
        "a --catalog-dir catalog is unsigned, or its signature does not verify against the effective trust root.",
        "point --catalog-dir at a catalog whose catalog.sig.json verifies, or pass --trust-root <file> (or set AGENTCE_TRUST_ROOT) for the root that signed it.",
    ),
    "input.trust_root_invalid": Entry(
        "error",
        "the trust root supplied by --trust-root or AGENTCE_TRUST_ROOT could not be loaded.",
        "pass --trust-root <file> pointing at a trust root in the form of the engine's vendored data/trust/dev-root.json.",
    ),
    "input.catalog_not_found": Entry(
        "error",
        "no base catalog was found under the expected path.",
        "run from the repository root or pass --catalog-dir to a catalog directory.",
    ),
    "input.nothing_evaluated": Entry(
        "error",
        "no control reached conformant, non-conformant, or insufficient_evidence, so the run judged nothing.",
        "emit under the subject and source the profile declares, and record the evidence the catalog's controls apply to.",
    ),
    "input.init_exists": Entry(
        "error",
        "init would overwrite a profile or domain binding that already exists.",
        "pass --force to overwrite, or --out <dir> to write somewhere else.",
    ),
    "input.profile_missing": Entry(
        "error",
        "the applicability profile was not supplied.",
        "pass --profile agentce/applicability.yaml (start from agentce init).",
    ),
    "environment.cryptography_unavailable": Entry(
        "error",
        "the cryptography package the engine signs and verifies with is missing or does not import.",
        "install the engine's dependencies with `uv sync`; where no prebuilt wheel exists, install Rust and OpenSSL 3 first.",
    ),
    "environment.python_unsupported": Entry(
        "error",
        "the running Python is older than the interpreter the engine supports.",
        "run the engine under Python 3.12 or newer (`uv python install 3.12`).",
    ),
    "internal.unexpected": Entry(
        "error",
        "an unexpected internal error occurred.",
        "re-run with --debug to see the stack trace, then file an issue.",
    ),
    # Warnings.
    "environment.cryptography_source_build": Entry(
        "warning",
        "cryptography was built from source because no prebuilt wheel is published for this platform.",
        "install Rust and OpenSSL 3 before `uv sync` (macOS: `brew install openssl@3`, then set OPENSSL_DIR to its prefix).",
    ),
    "warning.pilot_window": Entry(
        "warning",
        "the observation window is shorter than 90 days and is recorded as a pilot.",
        "extend the window to at least 90 days for a full assessment.",
    ),
    "warning.stale_component": Entry(
        "warning",
        "a declared component digest differs from the one observed in the window (STALE).",
        "update the profile's declared component digests and re-run.",
    ),
}


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
