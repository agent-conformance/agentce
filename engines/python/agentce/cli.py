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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NoReturn

from . import __version__, commands, exit_codes, logsetup
from .errors import AgentceError, InputError
from .exit_codes import ExitCode
from .result import CommandResult
from .safe_json import MAX_INT_STR_DIGITS

_log = logsetup.get_logger()


class _ArgvError(InputError):
    """A usage error, carrying the command that refused it for the --json envelope's ``command``."""

    def __init__(self, command: str, key: str, cause: str, fix: str) -> None:
        super().__init__(key, cause, fix)
        self.command = command


@dataclass(frozen=True)
class _ArgvErrors:
    """How one command words its argv errors: every one is a keyed `_ArgvError` (exit 3), never
    argparse's bare usage text. ``key`` is the default; ``value_keys`` and ``choice_errors`` reuse the
    more specific key TypeScript and Java already give for the same argv (18.107)."""

    command: str
    key: str
    flag_fix: str
    extra_arg_hint: str
    value_hint: str = "<value>"
    #: A value-less flag's key, by flag; "*" for every flag of the command.
    value_keys: Mapping[str, str] = field(default_factory=dict)
    #: Per-flag value hints for the fix ("pass --fail-on <expression>.").
    value_hints: Mapping[str, str] = field(default_factory=dict)
    #: Keep argparse's own sentence as the cause of a value-less flag (assess: TypeScript and Java do).
    argparse_value_cause: bool = False
    #: A bad choice's error, by argparse's label for the argument (`--format`, `<action>`), given the value.
    choice_errors: Mapping[str, Callable[[str], tuple[str, str, str]]] = field(
        default_factory=dict
    )

    def error(self, cause: str, fix: str, key: str | None = None) -> _ArgvError:
        return _ArgvError(self.command, key or self.key, cause, fix)

    def no_value(self, flag: str) -> _ArgvError:
        return self.error(f"flag '{flag}' takes no value.", f"drop the value: {flag}.")

    def usage(self, message: str) -> _ArgvError:
        """Map argparse's own message to the keyed error."""
        if m := re.fullmatch(r"argument (--[\w-]+): expected one argument", message):
            flag = m.group(1)
            key = self.value_keys.get(flag, self.value_keys.get("*", self.key))
            cause = (
                message
                if self.argparse_value_cause
                else f"flag '{flag}' needs a value."
            )
            hint = self.value_hints.get(flag, self.value_hint)
            return self.error(cause, f"pass {flag} {hint}.", key)
        if m := re.fullmatch(
            r"argument (--[\w-]+): ignored explicit argument .*", message
        ):
            return self.no_value(m.group(1))
        if m := re.fullmatch(
            r"argument (\S+): invalid choice: (.*?) \(choose from .*\)", message
        ):
            label, raw = m.groups()
            value = raw[1:-1] if raw[:1] in "'\"" else raw
            if (choice_error := self.choice_errors.get(label)) is not None:
                key, cause, fix = choice_error(value)
                return self.error(cause, fix, key)
        return self.error(f"{message}.", self.flag_fix)

    def unknown(self, token: str) -> _ArgvError:
        """A token argparse left unparsed."""
        if token.startswith("-") and token != "-":
            return self.error(f"unrecognized flag '{token}'.", self.flag_fix)
        return self.error(f"unrecognized argument '{token}'.", self.extra_arg_hint)


class _Parser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose usage errors exit ``3`` (input error), per the CLI scheme, as the
    command's keyed `_ArgvError` (English text: argparse is not localised here)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Exact flags only on every parser (TRADEOFFS 2026-10-06 18.50, option b; 18.107): `--bun x`
        # is an unrecognized flag in all three engines, never --bundle in Python alone.
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)
        # Set per command by `_key_argv_errors` once the tree is built.
        self._argv_errors: _ArgvErrors | None = None

    def error(self, message: str) -> NoReturn:
        if self._argv_errors is not None:
            raise self._argv_errors.usage(message)
        self.print_usage(sys.stderr)
        self.exit(int(ExitCode.INPUT_ERROR), f"{self.prog}: error: {message}\n")
        raise AssertionError("unreachable")  # pragma: no cover

    def parse_known_args(  # type: ignore[override]
        self, args: Sequence[str] | None = None, namespace: Any = None
    ) -> tuple[argparse.Namespace, list[str]]:
        if self._argv_errors is not None and args is not None:
            self._refuse_short_clusters(list(args))
        return super().parse_known_args(args, namespace)

    def _refuse_short_clusters(self, args: list[str]) -> None:
        """agentce has no clustered short options, so `-hx` or `-hh` is refused with the command's key,
        never read as -h with the rest dropped (KD-086). Scans this parser's own tokens up to `--`, and
        up to the command or action name for a parser with subcommands. A token argparse itself takes
        as an option's value (`--out -1`, `--out -`) is skipped; where an option needs a value and the
        next token is an option, argparse refuses the missing value first."""
        pending: argparse.Action | None = None
        for token in args:
            if token == "--":
                return
            if pending is not None:
                needs_value, pending = pending.nargs not in ("?", "*"), None
                if self._parse_optional(token) is None:
                    continue
                if needs_value:
                    return
            action = self._option_string_actions.get(token)
            if action is not None and action.nargs != 0:
                pending = action
            if not token.startswith("-"):
                if self._subparsers is not None:
                    return
            elif (
                not token.startswith("--")
                and len(token) > 2
                and action is None
                and not self._negative_number_matcher.match(token)
            ):
                assert self._argv_errors is not None
                raise self._argv_errors.unknown(token)


def _flag_fix(command: str) -> str:
    return f"run `agentce {command} --help` for the flags {command} takes, or drop the flag."


def _action_error(
    make: Callable[[], InputError],
) -> Callable[[str], tuple[str, str, str]]:
    """An unknown action is the error `cmd_<command>` gives for no action."""

    def error(value: str) -> tuple[str, str, str]:
        err = make()
        return err.key, err.cause, err.fix

    return error


_TOP_FIX = "name a command: `agentce <command>`; run `agentce --help` for the commands agentce takes."

_TOP_ARGV = _ArgvErrors(
    "agentce",
    "input.unknown_command",
    "run `agentce --help` for the commands and global flags agentce takes.",
    "run `agentce --help` for the commands agentce takes.",
    choice_errors={
        "<command>": lambda v: (
            "input.unknown_command",
            f"unrecognized command '{v}'.",
            "run `agentce --help` for the commands agentce takes.",
        )
    },
)

_ARGV: dict[str, _ArgvErrors] = {
    "readiness": _ArgvErrors(
        "readiness",
        "input.readiness_unrecognized_flag",
        "pass --gaps, --deviations, or --catalog-dir, or drop the flag.",
        "pass exactly one report directory: `agentce readiness <report-dir>`.",
        value_hint="<path>",
    ),
    "sign": _ArgvErrors(
        "sign",
        "input.sign_unrecognized_flag",
        "pass --as, --profile, --key, --dry-run, or --write-trust-root, or drop the flag.",
        "pass exactly one report directory: `agentce sign <report-dir> --as claimant|assessor`.",
    ),
    "assess": _ArgvErrors(
        "assess",
        "input.assess_unrecognized_flag",
        _flag_fix("assess"),
        "pass at most one records folder: `agentce assess <folder>`.",
        value_keys={"*": "input.assess_flag_needs_value"},
        value_hints={
            "--deviations": "<file>",
            "--fail-on": "<expression>",
            "--emit": "<formats>",
            "--for": "<preset>",
        },
        argparse_value_cause=True,
    ),
    "quickstart": _ArgvErrors(
        "quickstart",
        "input.quickstart_unrecognized_flag",
        _flag_fix("quickstart"),
        "quickstart takes no other argument: `agentce quickstart --out <dir>`.",
        value_hint="<dir>",
    ),
    "verify": _ArgvErrors(
        "verify",
        "input.verify_unrecognized_flag",
        "pass --bundle, --catalog, --release, or --report (with --signer-trust-root or "
        "--expect-keyid for --report), or drop the flag.",
        "pass the target with its flag, e.g. `agentce verify --bundle <dir>`.",
    ),
    "diff": _ArgvErrors(
        "diff",
        "input.diff_unrecognized_flag",
        "pass --format text|json|md, or drop the flag.",
        "pass two assertions files: `agentce diff <a> <b>`.",
    ),
    "validate": _ArgvErrors(
        "validate",
        "input.validate_unrecognized_flag",
        _flag_fix("validate"),
        "pass the bundle with its flag: `agentce validate --bundle <dir>`.",
        value_keys={"--bundle": "input.bundle_missing"},
    ),
    "report": _ArgvErrors(
        "report",
        "input.report_unrecognized_flag",
        _flag_fix("report"),
        "pass the input with its flag: `agentce report --from <file>` or `--validate <dir>`.",
        value_keys={
            "--from": "input.from_missing",
            "--validate": "input.validate_missing",
        },
        choice_errors={
            "--format": lambda v: (
                (err := commands.report_format_error(v)).key,
                err.cause,
                err.fix,
            )
        },
    ),
    "conformance": _ArgvErrors(
        "conformance",
        "input.conformance_unrecognized_flag",
        _flag_fix("conformance run"),
        "pass the inputs with their flags: `agentce conformance run --engine <dir> --corpus <dir>`.",
        value_keys={
            "--engine": "input.engine_missing",
            "--corpus": "input.corpus_missing",
        },
        choice_errors={"<action>": _action_error(commands.conformance_action_error)},
    ),
    "catalog": _ArgvErrors(
        "catalog",
        "input.catalog_unrecognized_flag",
        "run `agentce catalog <action> --help` for the flags it takes, or drop the flag.",
        "pass one catalog directory: `agentce catalog <action> <dir>`.",
        choice_errors={"<action>": _action_error(commands.catalog_action_error)},
    ),
    "config": _ArgvErrors(
        "config",
        "input.config_unrecognized_flag",
        _flag_fix("config show"),
        "config show takes no argument: `agentce config show`.",
        choice_errors={"<action>": _action_error(commands.config_action_error)},
    ),
}
for _name, _extra in (
    ("collect", "pass the config with its flag: `agentce collect --config <file>`."),
    ("ingest", "pass the export with its flag: `agentce ingest --in <file>`."),
    ("doctor", "pass the project with its flag: `agentce doctor --project <dir>`."),
    ("init", "init takes no positional argument: `agentce init --out <dir>`."),
    ("version", "version takes no argument: `agentce version`."),
):
    _ARGV[_name] = _ArgvErrors(
        _name, f"input.{_name}_unrecognized_flag", _flag_fix(_name), _extra
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
        # No prefix matching, as for `readiness` and `sign`: TypeScript and Java read verify's argv
        # with one declared grammar and refuse what argparse would refuse, so `--bun <dir>` is an
        # unrecognized flag in all three engines, not a bundle in Python alone (18.65 round 3).
        allow_abbrev=False,
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
    # `action="append"` (rather than the default `store`, which would silently keep only the last
    # value) lets `cmd_verify` detect and refuse a repeated target flag instead of picking a winner
    # TypeScript/Java don't agree on (verifier round 2, 18.65).
    p.add_argument(
        "--bundle", action="append", help="verify an evidence bundle's integrity"
    )
    p.add_argument("--catalog", action="append", help="verify a catalog's signatures")
    p.add_argument(
        "--release", action="append", help="verify a release artifact's signatures"
    )
    p.add_argument(
        "--report",
        action="append",
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
        # No prefix matching, as for `readiness`, `sign` and `verify`: `--em md` or `--fo ci` is an
        # unrecognized flag in all three engines, never --emit or --for in Python alone (18.105).
        allow_abbrev=False,
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
        "sign",
        parents=[common],
        help="sign a report as claimant or assessor",
        # No prefix matching, same as `readiness` (18.25): TS and Java refuse an abbreviated flag as
        # unrecognized, so Python does too, with the same keyed error (18.26 round-2 verifier fix).
        allow_abbrev=False,
    )
    p.add_argument("report_dir", nargs="?", help="the report directory")
    p.add_argument(
        # No `choices=`: an invalid value is a keyed `input.sign_role` error raised by `cmd_sign`
        # itself, not argparse's own pre-envelope usage error -- the same `diff --format` pattern
        # above. `metavar` keeps `--help`/usage text byte-identical to the old `choices=` rendering
        # (a disclosed divergence, TRADEOFFS.md, 2026-09-30).
        "--as",
        dest="as_role",
        metavar="{claimant,assessor}",
        help="the signing role",
    )
    p.add_argument(
        # No `choices=`: same pattern, `input.sign_profile` fires from `cmd_sign` itself.
        "--profile",
        metavar="{sigstore-public,sigstore-private,kms}",
        help="the signing profile",
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
        "quickstart",
        parents=[common],
        help="assess the bundled quickstart project",
        # No prefix matching, as for `assess`: `--ou ./o` is an unrecognized flag in all three
        # engines, never --out in Python alone, and every argv error is keyed (18.106).
        allow_abbrev=False,
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

    _key_argv_errors(parser)
    return parser


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, Any]:
    return next(
        (
            a.choices
            for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        ),
        {},
    )


def _key_argv_errors(parser: argparse.ArgumentParser) -> None:
    """Give every parser its command's keyed argv errors: the top level, each command and each
    action under it (`catalog lint` answers with catalog's key)."""
    parser._argv_errors = _TOP_ARGV  # type: ignore[attr-defined]
    for name, command in _subparsers(parser).items():
        for p in (command, *_subparsers(command).values()):
            p._argv_errors = _ARGV[name]


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


_GLOBAL_FLAGS = ("--json", "--debug", "--quiet")


def _scan_top_level(args: Sequence[str]) -> str | None:
    """Read the tokens before the command the way the TypeScript and Java engines do (18.108); return
    "help" or "version" when one of those answers the line, None when argparse should run it. Raises the
    shared top-level error (input.unknown_command) for a short cluster, a value on a flag that takes none,
    an unknown flag before the command, -V/--version with anything else on the line, a `--` with nothing
    after it, or global flags with no command. -h/--help win when reached, as in argparse."""
    unknown: str | None = None
    has_command = False
    for i, token in enumerate(args):
        if token == "--":
            if i + 1 == len(args):
                raise _TOP_ARGV.error("no command given after '--'.", _TOP_FIX)
            has_command = True
            break
        if not token.startswith("-") or token == "-":
            has_command = True
            break
        name = token.split("=", 1)[0]
        if not token.startswith("--") and len(token) > 2:
            raise _TOP_ARGV.unknown(token)
        known = name in ("-h", "--help", "-V", "--version", *_GLOBAL_FLAGS)
        if known and "=" in token:
            raise _TOP_ARGV.no_value(name)
        if name in ("-h", "--help"):
            return "help"
        if name in ("-V", "--version"):
            if len(args) != 1:
                raise _TOP_ARGV.error(
                    f"'{name}' takes no other arguments.",
                    "run `agentce --version` alone, or `agentce version --json` for the JSON envelope.",
                )
            return "version"
        if not known and unknown is None:
            unknown = token
    if unknown is not None:
        raise _TOP_ARGV.unknown(unknown)
    if not has_command and args:
        raise _TOP_ARGV.error("no command given.", _TOP_FIX)
    return None if args else "help"


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` (default ``sys.argv``), run the command, and return the process exit code."""
    # One integer rule for the whole run, whatever PYTHONINTMAXSTRDIGITS says: a literal JSON may hold
    # (up to MAX_INT_STR_DIGITS digits) can also be turned back into text, e.g. in a schema error.
    sys.set_int_max_str_digits(MAX_INT_STR_DIGITS)
    parser = build_parser()
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if _scan_top_level(args) == "help":
            parser.print_help()
            return int(ExitCode.OK)
        ns, unknown = parser.parse_known_args(args)
        if unknown:
            errors = _ARGV.get(getattr(ns, "command", "") or "", _TOP_ARGV)
            raise errors.unknown(unknown[0])
    except SystemExit as exc:  # argparse: -h/--version exit 0
        return exc.code if isinstance(exc.code, int) else int(ExitCode.INPUT_ERROR)
    # Every usage error is keyed (`_Parser`, 18.107).
    except _ArgvError as err:
        return _emit_error(err, command=err.command, want_json="--json" in args)

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
            fix="re-run with --debug to see the stack trace, then file an issue for an AgentCE maintainer to investigate.",
            exit_code=int(ExitCode.INPUT_ERROR),
        )
        return _emit_error(internal, command=command, want_json=want_json)

    _emit_result(result, want_json=want_json)
    _log.debug("command.end", extra={"command": command, "exit_code": result.exit_code})
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
