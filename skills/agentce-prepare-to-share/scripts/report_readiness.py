"""report_readiness — the post-run readiness verdict (SPEC §13.3.4 stage 4).

A thin wrapper over the engine's readiness logic (``agentce.readiness``), where the verdict lives, so
signing never depends on a skill script (S-5, SPEC §8.5). It reads a finished report directory, the
catalogs the report used (for control severities), and optional gaps and deviation files, and prints
the verdict: READY, READY WITH LIMITATIONS, or NOT READY. Exit 0 for the first two, 1 for NOT READY.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import replace
from pathlib import Path

from _common import (
    FINDINGS,
    INPUT_ERROR,
    OK,
    emit,
    run_guarded,
)

from agentce import argv as argv_grammar
from agentce import readiness
from agentce.commands import (
    _effective_trust_root,
    _load_deviation_register,
    _readiness_severities,
)
from agentce.errors import AgentceError


class _ArgError(Exception):
    """A malformed command line: refused, never read as a missing option."""


def _grammar() -> argv_grammar.Command:
    """`agentce readiness`'s grammar (SPEC §8.5), with ``--report`` in place of its report-dir
    positional."""
    cmd = argv_grammar.command("readiness")
    report = argv_grammar.Option(names=("--report",), kind="value", repeatable=False)
    level = replace(
        cmd.level, options={**cmd.level.options, "--report": report}, max_positionals=0
    )
    return replace(cmd, level=level)


def _parse(argv: list[str]) -> argparse.Namespace:
    """The options as `agentce readiness` reads them, through the engine's own scanner (SPEC §8.5):
    `--flag=value` is the same as `--flag value`, an option given twice, an abbreviation and a flag
    without its value are refused."""
    try:
        scan = argv_grammar.scan(_grammar(), argv)
    except argv_grammar.ArgvRefusal as exc:
        raise _ArgError(str(exc)) from None
    if scan.help:
        raise _ArgError("pass --report <report-dir>")
    return argparse.Namespace(
        report=scan.values.get("--report"),
        gaps=scan.values.get("--gaps"),
        deviations=scan.values.get("--deviations"),
        catalog_dir=scan.lists.get("--catalog-dir"),
        trust_root=scan.values.get("--trust-root"),
        allow_unverified_catalog="--allow-unverified-catalog" in scan.flags,
        json="--json" in scan.flags,
    )


def body(argv: list[str]) -> int:
    want_json = "--json" in argv
    try:
        ns = _parse(argv)
    except _ArgError as exc:
        emit(
            {"status": "input_error", "message": str(exc)},
            want_json=want_json,
            human=f"INPUT ERROR: {exc}",
        )
        return INPUT_ERROR
    report = ns.report
    if not report or not Path(report).is_dir():
        emit(
            {"status": "input_error", "message": "pass --report <report-dir>"},
            want_json=want_json,
            human="INPUT ERROR: pass --report <report-dir>",
        )
        return INPUT_ERROR
    gaps: set[str] = set()
    gaps_file = ns.gaps
    if gaps_file:
        gaps = set(
            re.findall(r"\b[A-Z]{2,4}-[0-9]{2}\b", Path(gaps_file).read_text("utf-8"))
        )
    deviations: list[dict] = []
    dev_file = ns.deviations
    try:
        if dev_file:
            deviations = _load_deviation_register(Path(dev_file))
        # --catalog-dir is verified as `agentce readiness` verifies it (SPEC §8.7).
        severities, limitations = _readiness_severities(ns, _effective_trust_root(ns))
    except AgentceError as exc:
        emit(
            {"status": "input_error", "message": str(exc)},
            want_json=want_json,
            human=f"INPUT ERROR: {exc}",
        )
        return INPUT_ERROR
    verdict = readiness.compute_readiness(
        Path(report),
        severities=severities,
        deviations=deviations,
        gaps=gaps,
        limitations=limitations,
    )
    emit(
        verdict,
        want_json=want_json,
        human=f"{verdict['verdict']}"
        + (f" ({'; '.join(verdict['reasons'])})" if verdict["reasons"] else ""),
    )
    return OK if verdict["verdict"] != readiness.NOT_READY else FINDINGS


if __name__ == "__main__":
    raise SystemExit(run_guarded(sys.argv[1:], body))
