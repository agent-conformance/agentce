"""The AgentCE command-line interface (SPEC §8.5).

Built on the standard-library :mod:`argparse` (ADR-0004): the command set, the common exit-code
scheme (``0`` ok, ``1`` findings, ``2`` insufficient evidence, ``3`` input/version/signature error;
highest wins, and the JSON carries all applicable codes), a ``--json`` form of every command, and
structured logging on stderr. Usage errors map to exit code ``3`` (an input error), not argparse's
default ``2`` (which this scheme reserves for insufficient evidence).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from typing import Any, NoReturn

from . import __version__, commands, exit_codes, logsetup
from .errors import AgentceError
from .exit_codes import ExitCode
from .result import CommandResult

_log = logsetup.get_logger()


class _Parser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose usage errors exit ``3`` (input error), per the CLI scheme.

    With ``keyed_errors=True`` a usage error is instead raised as the keyed
    ``input.readiness_unrecognized_flag`` error, worded as the TypeScript and Java engines word it.
    """

    def __init__(self, *args: Any, keyed_errors: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._keyed_errors = keyed_errors

    def error(self, message: str) -> NoReturn:
        if self._keyed_errors:
            raise _readiness_usage_error(message)
        self.print_usage(sys.stderr)
        self.exit(int(ExitCode.INPUT_ERROR), f"{self.prog}: error: {message}\n")
        raise AssertionError("unreachable")  # pragma: no cover


_READINESS_FLAG_FIX = "pass --gaps, --deviations, or --catalog-dir, or drop the flag."


def _readiness_argv_error(cause: str, fix: str) -> AgentceError:
    return AgentceError(key="input.readiness_unrecognized_flag", cause=cause, fix=fix)


def _readiness_usage_error(message: str) -> AgentceError:
    """argparse's usage error inside ``readiness`` as the keyed error TypeScript and Java raise."""
    if m := re.fullmatch(r"argument (--[\w-]+): expected one argument", message):
        flag = m.group(1)
        return _readiness_argv_error(
            f"flag '{flag}' needs a value.", f"pass {flag} <path>."
        )
    if m := re.fullmatch(r"argument (--[\w-]+): ignored explicit argument .*", message):
        flag = m.group(1)
        return _readiness_argv_error(
            f"flag '{flag}' takes no value.", f"drop the value: {flag}."
        )
    return _readiness_argv_error(f"{message}.", _READINESS_FLAG_FIX)


def _readiness_unknown_error(token: str) -> AgentceError:
    """The first token the ``readiness`` subparser left unconsumed, as TypeScript and Java word it."""
    if token.startswith("-") and token != "-":
        return _readiness_argv_error(
            f"unrecognized flag '{token}'.", _READINESS_FLAG_FIX
        )
    return _readiness_argv_error(
        f"unrecognized argument '{token}'.",
        "pass exactly one report directory: `agentce readiness <report-dir>`.",
    )


def _common_flags() -> _Parser:
    common = _Parser(add_help=False)
    group = common.add_argument_group("global options")
    group.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit machine-readable JSON on stdout",
    )
    group.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="verbose logs on stderr and a stack trace on unexpected errors",
    )
    group.add_argument(
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="log warnings and errors only",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    """Construct the full argument parser (all commands wired to their handlers)."""
    common = _common_flags()
    parser = _Parser(
        prog="agentce",
        parents=[common],
        description="Agent Conformance Engine — deterministic conformance evidence for AI agents.",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"agentce {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("validate", parents=[common], help="schema-validate a bundle")
    p.add_argument("--bundle", help="the evidence bundle directory")
    p.add_argument("--out", help="write quarantine.jsonl to this directory")
    p.set_defaults(func=commands.cmd_validate)

    p = sub.add_parser(
        "verify",
        parents=[common],
        help="integrity or signature verification",
        description="Integrity or signature verification. `--report <dir>` re-runs a shareable "
        "report bundle (`assess --package-for-sharing`) offline through nine stages, in order, "
        "each with its own message key: (1) the claim exists and is signed "
        "(verify.report_no_claim, verify.report_claim_malformed, verify.report_unsigned); "
        "(2) the trust root resolves (verify.report_no_trust_root, input.trust_root_invalid, "
        "verify.report_keyid_mismatch); (3) a claimant signature verifies "
        "(verify.report_signature_invalid); (4) the signed subjects match what's on disk "
        "(verify.report_subject_missing, verify.report_manifest_tampered, "
        "verify.report_claim_tampered); (5) the engine build matches "
        "(verify.report_engine_mismatch); (6) every manifest-tracked output's digest matches "
        "(verify.report_output_tampered); (7) the packaged evidence, profile, domain binding, "
        "and catalogs match (verify.report_evidence_tampered); (8) an offline re-run reproduces "
        "every canonical output byte for byte (verify.report_reproduction_mismatch); "
        "(9) success (`reproduced: true`).",
    )
    p.add_argument("--bundle", help="verify an evidence bundle's integrity")
    p.add_argument("--catalog", help="verify a catalog's signatures")
    p.add_argument("--release", help="verify a release artifact's signatures")
    p.add_argument(
        "--report",
        help="re-run a shareable report bundle (assess --package-for-sharing) offline and check "
        "it reproduces byte for byte, refusing any tampering",
    )
    p.add_argument(
        "--signer-trust-root",
        dest="signer_trust_root",
        help="verify --report's signature against this trust root file, instead of an embedded "
        "trust-root.json inside the report directory",
    )
    p.add_argument(
        "--expect-keyid",
        dest="expect_keyid",
        help="verify --report: refuse unless the claim signature's keyid matches this value",
    )
    p.set_defaults(func=commands.cmd_verify)

    p = sub.add_parser(
        "assess",
        parents=[common],
        help="run a full assessment",
        description="Run a full assessment: `agentce assess <folder>` over a folder of trace "
        "exports, or `agentce assess --bundle <dir> --profile <file>` over an evidence bundle.",
    )
    p.add_argument(
        "folder",
        nargs="?",
        help="a folder of OpenTelemetry GenAI or OpenInference trace exports (.json, .jsonl, "
        ".ndjson): assess reads it and writes a default profile, so no other flag is needed",
    )
    p.add_argument("--bundle", help="the evidence bundle directory")
    p.add_argument(
        "--catalog",
        help="catalog ids, comma-separated: <id@ver>[,<id@ver>...] (default: the profile's catalogs, else the baseline)",
    )
    p.add_argument("--profile", help="the applicability profile file")
    p.add_argument(
        "--deviations",
        help="the deviation register file: a lint-clean, unexpired entry flips its control's "
        "non-conformant outcome to partial",
    )
    p.add_argument("--domain", help="the domain ontology binding file")
    p.add_argument(
        "--catalog-dir",
        dest="catalog_dir",
        action="append",
        help="a catalog directory to evaluate (repeatable)",
    )
    p.add_argument(
        "--trust-root",
        dest="trust_root",
        help="trust root every --catalog-dir signature is verified against (default: "
        "AGENTCE_TRUST_ROOT, else the vendored development root)",
    )
    p.add_argument(
        "--allow-unverified-catalog",
        dest="allow_unverified_catalog",
        action="store_true",
        help="assess a --catalog-dir catalog whose signature is absent or does not verify, "
        "recording the override as a limitation in the manifest and the claim (SPEC 8.7)",
    )
    p.add_argument("--manual", help="the manual-records directory")
    p.add_argument("--probes", help="the probe-results directory")
    p.add_argument(
        "--package-for-sharing",
        dest="package_for_sharing",
        action="store_true",
        help="copy the evidence bundle, profile, domain binding and every --catalog-dir into "
        "--out/bundle/ so the directory is self-contained: `agentce sign` then `agentce verify "
        "--report` re-runs it offline on another machine. Requires --bundle (not a records "
        "folder).",
    )
    p.add_argument("--out", help="the output directory (default: ./out)")
    p.add_argument("--state", help="the incremental state directory")
    p.add_argument(
        "--report-language",
        dest="report_language",
        help="message-key catalogue for the report; does not affect assertions.json (SPEC 9.3)",
    )
    p.add_argument(
        "--emit",
        help="comma-separated report formats to render (default: "
        + ", ".join(sorted(commands.ASSESS_DEFAULT_EMIT))
        + "); one or more of: "
        + ", ".join(commands.EMIT_FORMATS)
        + ". Setting CI adds junit to the default automatically (see --for); an explicit "
        "--emit is never extended.",
    )
    p.add_argument(
        "--for",
        dest="for_preset",
        metavar="PRESET",
        help="report preset for an audience, in place of --emit: "
        + "; ".join(
            f"{k} ({', '.join(sorted(v))})" for k, v in commands.PRESET_EMIT.items()
        )
        + ". CI detected automatically (adds junit to the default) when neither --for nor "
        "--emit is given.",
    )
    p.add_argument(
        "--fail-on",
        dest="fail_on",
        help="gate the exit code on a tiny deterministic expression over assertion fields "
        "(control, subject, outcome, severity, family, rung, mode), e.g. "
        '\'outcome=="non-conformant" and severity=="high"\' (comparisons joined by and/or; '
        "never a general expression language). Replaces the default any-non-conformant rule "
        "when given.",
    )
    p.set_defaults(func=commands.cmd_assess)

    p = sub.add_parser(
        "report", parents=[common], help="re-render a report, or validate one"
    )
    p.add_argument("--from", dest="from_", help="an assertions.json to re-render")
    p.add_argument(
        "--format", choices=commands.REPORT_FORMATS, help="the output format"
    )
    p.add_argument(
        "--role", choices=("provider", "deployer"), help="evidence-pack role variant"
    )
    p.add_argument(
        "--catalog", help="catalog labels for the public statement, comma-separated"
    )
    p.add_argument(
        "--language", help="message-key catalogue for md/html rendering (SPEC 9.3)"
    )
    p.add_argument("--out", help="write the rendering to this file")
    p.add_argument("--validate", help="validate every artifact in a report directory")
    p.set_defaults(func=commands.cmd_report)

    p = sub.add_parser(
        "collect",
        parents=[common],
        help="run a scheduled collection job over sources with a local export, or plan one",
        description=(
            "Plan or run a scheduled collection job from a config. --dry-run lists what would be "
            "collected and resolves no credential. A real run adapts every source that names a local "
            "export (already written by its own pipeline, e.g. an OTel Collector's file exporter, "
            "SPEC 5.4) and records it complete; a source with no export, or whose export cannot be "
            "adapted, is recorded incomplete, reason 'no source connector in the reference collector', "
            "and the run exits 1. To get evidence into a bundle today without a config, emit it with "
            "agentce-emit, or adapt one export file directly with `agentce ingest`."
        ),
    )
    p.add_argument("--config", help="the collection config file")
    p.add_argument("--out", help="the output bundle path")
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="plan only; write nothing",
    )
    p.add_argument(
        "--adapters-root",
        dest="adapters_root",
        help="the adapters checkout a source's `export` is resolved through (default: ./adapters)",
    )
    p.set_defaults(func=commands.cmd_collect)

    p = sub.add_parser(
        "ingest",
        parents=[common],
        help="adapt a real adapter export into an evidence bundle",
        description=(
            "Turn an already-exported adapter payload (e.g. an OTLP/JSON trace export, or one an "
            "OTel Collector's file exporter wrote, SPEC 5.4) into an evidence bundle `agentce "
            "validate` accepts -- no live collect connector needed."
        ),
    )
    p.add_argument("--in", dest="in_path", help="the adapter export file")
    p.add_argument("--out", help="the output bundle directory")
    p.add_argument("--adapter", help="the adapter to use, e.g. otel-genai")
    p.add_argument(
        "--adapters-root",
        dest="adapters_root",
        help="the adapters checkout (default: ./adapters)",
    )
    p.add_argument(
        "--subject",
        help="the assessed subject id (default: agentce:subject/local)",
    )
    p.add_argument(
        "--source-class",
        dest="source_class",
        help="self_report | enforcement_point | independent_system (default: self_report)",
    )
    p.add_argument("--source", help="override the per-event source URI")
    p.add_argument(
        "--engine",
        help="the underlying engine, for adapters that need one (e.g. policy-engines)",
    )
    p.set_defaults(func=commands.cmd_ingest)

    p = sub.add_parser("catalog", parents=[common], help="catalog tools")
    csub = p.add_subparsers(dest="catalog_action", metavar="<action>")
    lint = csub.add_parser(
        "lint", parents=[common], help="validate controls, shapes, and test cases"
    )
    lint.add_argument("dir", nargs="?", help="the catalog directory")
    lint.add_argument(
        "--require-verification-flags",
        dest="require_verification_flags",
        action="store_true",
        help="require a verified_against_text flag on every crosswalk entry (SPEC 7.3 B14)",
    )
    lint.add_argument(
        "--require-provenance",
        dest="require_provenance",
        action="store_true",
        help="require a provenance block (source, version, digest) on the catalog (SPEC 14.5 CP-3)",
    )
    lint.add_argument(
        "--support-matrix",
        dest="support_matrix",
        help="write a per-control view of which adapters can supply each requirement's evidence",
    )
    matrix = csub.add_parser(
        "coverage-matrix",
        parents=[common],
        help="regenerate the automation coverage matrix (SPEC 7.5)",
    )
    matrix.add_argument("dir", nargs="?", help="the catalog directory")
    matrix.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed matrix differs from the regenerated one",
    )
    init = csub.add_parser(
        "init", parents=[common], help="scaffold a new, lint-clean custom catalog"
    )
    init.add_argument("dir", help="where to write the new catalog")
    init.add_argument(
        "--id",
        dest="family",
        help="the control-family prefix, 2-4 uppercase letters (default: derived from <dir>)",
    )
    init.add_argument(
        "--title", help="the catalog title (default: '<family> custom catalog')"
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="overwrite any of the five scaffolded files",
    )
    sign = csub.add_parser(
        "sign",
        parents=[common],
        help="sign a catalog directory with an operator-held key",
    )
    sign.add_argument("dir", help="the catalog directory to sign")
    sign_keys = sign.add_mutually_exclusive_group(required=True)
    sign_keys.add_argument("--key", help="an existing Ed25519 private key (PEM)")
    sign_keys.add_argument(
        "--new-key",
        dest="new_key",
        help="generate a fresh Ed25519 private key (PEM) at this path and sign with it",
    )
    sign.add_argument(
        "--write-trust-root",
        dest="write_trust_root",
        help="write a trust root for this key to this path (for `assess --trust-root`)",
    )
    sign.add_argument(
        "--identity",
        help="a human-readable label for the signer, recorded in the trust root",
    )
    sign.add_argument(
        "--force", action="store_true", help="overwrite an existing catalog.sig.json"
    )
    p.set_defaults(func=commands.cmd_catalog)

    p = sub.add_parser("conformance", parents=[common], help="engine conformance suite")
    nsub = p.add_subparsers(dest="conformance_action", metavar="<action>")
    run = nsub.add_parser(
        "run", parents=[common], help="run the ECS and emit an implementation report"
    )
    run.add_argument("--engine", help="the engine path under test")
    run.add_argument("--corpus", help="the corpus directory")
    run.add_argument("--out", help="the output directory for the implementation report")
    run.add_argument(
        "--adapters",
        help="the adapters directory; also run adapter conformance (SPEC 11.5, 12.3)",
    )
    p.set_defaults(func=commands.cmd_conformance)

    p = sub.add_parser(
        "diff", parents=[common], help="deterministic diff of two assertion sets"
    )
    p.add_argument("report_a", nargs="?", help="the first assertions.json")
    p.add_argument("report_b", nargs="?", help="the second assertions.json")
    # An extra positional is a keyed `input.diff_extra_argument` error (`cmd_diff`), not argparse's
    # own "unrecognized arguments" usage error -- so it is captured here, not left unconsumed.
    p.add_argument("extra", nargs="*", help=argparse.SUPPRESS)
    p.add_argument(
        # No `choices=`: an invalid value is a keyed `input.diff_format` error raised by `cmd_diff`
        # itself, not argparse's own pre-envelope usage error -- unlike `report --format` (unchanged,
        # still `choices=`), a disclosed divergence recorded in TRADEOFFS.md (2026-09-30).
        "--format",
        help="how to render the diff (default: text)",
    )
    p.set_defaults(func=commands.cmd_diff)

    p = sub.add_parser(
        "sign", parents=[common], help="sign a report as claimant or assessor"
    )
    p.add_argument("report_dir", nargs="?", help="the report directory")
    p.add_argument(
        "--as", dest="as_role", choices=commands.SIGN_ROLES, help="the signing role"
    )
    p.add_argument(
        "--profile", choices=commands.SIGN_PROFILES, help="the signing profile"
    )
    p.add_argument(
        "--key", help="operator Ed25519 private key (PEM) for the kms profile"
    )
    p.add_argument(
        "--dry-run", dest="dry_run", action="store_true", help="plan only; sign nothing"
    )
    p.add_argument(
        "--write-trust-root",
        dest="write_trust_root",
        action="store_true",
        help="write trust-root.json (the signer's public key) into the report directory, so a "
        "recipient can `agentce verify --report` this bundle without any other key exchange. "
        "Requires --profile kms (the only profile with an exportable key).",
    )
    p.set_defaults(func=commands.cmd_sign)

    p = sub.add_parser(
        "readiness",
        parents=[common],
        help="compute the report-readiness verdict (SPEC 13.3.4)",
        # No prefix matching (`--deviation` for `--deviations`): TS and Java refuse an abbreviated
        # flag as unrecognized, so Python does too, with the same keyed error.
        allow_abbrev=False,
        keyed_errors=True,
    )
    p.add_argument("report_dir", nargs="?", help="the report directory")
    p.add_argument(
        "--gaps", help="a gaps file listing accepted high-severity evidence gaps"
    )
    p.add_argument("--deviations", help="a deviation register to validate")
    p.add_argument(
        "--catalog-dir",
        dest="catalog_dir",
        action="append",
        help="a catalog directory whose control severities the verdict reads (repeatable)",
    )
    p.set_defaults(func=commands.cmd_readiness)

    p = sub.add_parser(
        "doctor",
        parents=[common],
        help="diagnose a project and name the exact fix (SPEC 13.4)",
    )
    p.add_argument(
        "--project",
        help="the project directory to diagnose (default: the current directory)",
    )
    p.add_argument(
        "--write-errors",
        dest="write_errors",
        help="regenerate the message-key catalogue at this path instead of diagnosing",
    )
    p.set_defaults(func=commands.cmd_doctor)

    p = sub.add_parser(
        "quickstart", parents=[common], help="assess the bundled quickstart project"
    )
    p.add_argument("--out", help="the output directory for the report (default: ./out)")
    p.set_defaults(func=commands.cmd_quickstart)

    p = sub.add_parser(
        "init", parents=[common], help="write a starter applicability profile"
    )
    p.add_argument(
        "--non-interactive",
        dest="non_interactive",
        action="store_true",
        help="accepted for compatibility; init never prompts",
    )
    p.add_argument(
        "--framework", help="the agent framework, e.g. custom-loop, langgraph"
    )
    p.add_argument(
        "--subject",
        help="the assessed subject id (default: the id agentce_emit.auto() emits under)",
    )
    p.add_argument(
        "--role",
        choices=commands.INIT_ROLES,
        help="the subject's role: deployer, provider, or both",
    )
    p.add_argument(
        "--out", help="the output directory (default: the current directory)"
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="overwrite a profile or domain binding that already exists",
    )
    p.set_defaults(func=commands.cmd_init)

    p = sub.add_parser("config", parents=[common], help="show engine configuration")
    csub = p.add_subparsers(dest="config_action", metavar="<action>")
    csub.add_parser(
        "show", parents=[common], help="print each config value and its source"
    )
    p.set_defaults(func=commands.cmd_config)

    p = sub.add_parser(
        "version", parents=[common], help="print engine, spec, and no_ml information"
    )
    p.set_defaults(func=commands.cmd_version)

    return parser


def _emit_result(result: CommandResult, *, want_json: bool) -> None:
    if want_json:
        print(json.dumps(result.envelope(), sort_keys=True, indent=2))
        return
    if result.human_lines:
        for line in result.human_lines:
            print(line)
    else:
        print(f"{result.command}: {exit_codes.name_of(result.exit_code)}")


def _emit_error(err: AgentceError, *, command: str, want_json: bool) -> int:
    if want_json:
        envelope = {
            "command": command,
            "error": err.to_dict(),
            "exit_code": int(err.exit_code),
            "exit_codes": [int(err.exit_code)],
            "exit_status": [exit_codes.name_of(err.exit_code)],
        }
        print(json.dumps(envelope, sort_keys=True, indent=2))
    else:
        print(f"error [{err.key}]: {err.cause}", file=sys.stderr)
        if err.fix:
            print(f"  fix: {err.fix}", file=sys.stderr)
    _log.error("command.error", extra={"command": command, "key": err.key})
    return int(err.exit_code)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` (default ``sys.argv``), run the command, and return the process exit code."""
    parser = build_parser()
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        ns, unknown = parser.parse_known_args(args)
        if unknown and getattr(ns, "command", None) != "readiness":
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        if unknown:
            raise _readiness_unknown_error(unknown[0])
    except SystemExit as exc:  # argparse: -h/--version exit 0; usage errors exit 3
        return exc.code if isinstance(exc.code, int) else int(ExitCode.INPUT_ERROR)
    except AgentceError as err:  # a keyed `readiness` usage error (see `_Parser`)
        return _emit_error(err, command="readiness", want_json="--json" in args)

    debug = bool(getattr(ns, "debug", False))
    quiet = bool(getattr(ns, "quiet", False))
    logsetup.configure(debug=debug, quiet=quiet)
    want_json = bool(getattr(ns, "json", False))

    func = getattr(ns, "func", None)
    if func is None:
        parser.print_help()
        return int(ExitCode.OK)

    command = str(getattr(ns, "command", "?"))
    _log.debug("command.start", extra={"command": command})
    try:
        result: CommandResult = func(ns)
    except AgentceError as err:
        return _emit_error(err, command=command, want_json=want_json)
    except Exception as exc:  # noqa: BLE001 - the top-level guard (AX-6: no traceback without --debug)
        if debug:
            raise
        internal = AgentceError(
            key="internal.unexpected",
            cause=f"an unexpected error occurred: {type(exc).__name__}.",
            fix="re-run with --debug to see the stack trace, then file an issue.",
            exit_code=int(ExitCode.INPUT_ERROR),
        )
        return _emit_error(internal, command=command, want_json=want_json)

    _emit_result(result, want_json=want_json)
    _log.debug("command.end", extra={"command": command, "exit_code": result.exit_code})
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
