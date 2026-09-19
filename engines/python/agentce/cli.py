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
import sys
from collections.abc import Sequence
from typing import NoReturn

from . import __version__, commands, exit_codes, logsetup
from .errors import AgentceError
from .exit_codes import ExitCode
from .result import CommandResult

_log = logsetup.get_logger()


class _Parser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose usage errors exit ``3`` (input error), per the CLI scheme."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(int(ExitCode.INPUT_ERROR), f"{self.prog}: error: {message}\n")
        raise AssertionError("unreachable")  # pragma: no cover


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
        "verify", parents=[common], help="integrity or signature verification"
    )
    p.add_argument("--bundle", help="verify an evidence bundle's integrity")
    p.add_argument("--catalog", help="verify a catalog's signatures")
    p.add_argument("--release", help="verify a release artifact's signatures")
    p.set_defaults(func=commands.cmd_verify)

    p = sub.add_parser("assess", parents=[common], help="run a full assessment")
    p.add_argument("--bundle", help="the evidence bundle directory")
    p.add_argument(
        "--catalog", help="catalog ids, comma-separated: <id@ver>[,<id@ver>...]"
    )
    p.add_argument("--profile", help="the applicability profile file")
    p.add_argument("--deviations", help="the deviation register file")
    p.add_argument("--domain", help="the domain ontology binding file")
    p.add_argument(
        "--catalog-dir",
        dest="catalog_dir",
        action="append",
        help="a catalog directory to evaluate (repeatable)",
    )
    p.add_argument("--manual", help="the manual-records directory")
    p.add_argument("--probes", help="the probe-results directory")
    p.add_argument("--out", help="the output directory")
    p.add_argument("--state", help="the incremental state directory")
    p.add_argument(
        "--report-language",
        dest="report_language",
        help="message-key catalogue for the report; does not affect assertions.json (SPEC 9.3)",
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
        "collect", parents=[common], help="pull evidence from sources via adapters"
    )
    p.add_argument("--config", help="the collection config file")
    p.add_argument("--out", help="the output bundle path")
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="plan only; write nothing",
    )
    p.set_defaults(func=commands.cmd_collect)

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
    p.set_defaults(func=commands.cmd_sign)

    p = sub.add_parser(
        "readiness",
        parents=[common],
        help="compute the report-readiness verdict (SPEC 13.3.4)",
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
    p.add_argument("--project", help="the project directory to diagnose")
    p.add_argument(
        "--write-errors",
        dest="write_errors",
        help="regenerate the message-key catalogue at this path instead of diagnosing",
    )
    p.set_defaults(func=commands.cmd_doctor)

    p = sub.add_parser(
        "quickstart", parents=[common], help="assess the bundled quickstart project"
    )
    p.add_argument("--out", help="the output directory for the report")
    p.set_defaults(func=commands.cmd_quickstart)

    p = sub.add_parser(
        "init", parents=[common], help="write a starter applicability profile"
    )
    p.add_argument(
        "--non-interactive",
        dest="non_interactive",
        action="store_true",
        help="generate without prompting (required)",
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
    p.add_argument("--out", help="the output directory")
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
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: -h/--version exit 0; usage errors exit 3
        return exc.code if isinstance(exc.code, int) else int(ExitCode.INPUT_ERROR)

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
