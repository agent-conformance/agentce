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
from pathlib import Path
from typing import NoReturn

from _common import (
    FINDINGS,
    INPUT_ERROR,
    OK,
    emit,
    run_guarded,
)

from agentce import readiness
from agentce.commands import (
    _effective_trust_root,
    _load_deviation_register,
    _readiness_severities,
)
from agentce.errors import AgentceError


class _ArgError(Exception):
    """A malformed command line: refused, never read as a missing option."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _ArgError(message)


def _parse(argv: list[str]) -> argparse.Namespace:
    """The options as `agentce readiness` reads them: `--flag=value` is the same as `--flag value`, a
    repeated single-value option keeps its last value, and a flag without its value is refused."""
    p = _Parser(prog="report_readiness", add_help=False, allow_abbrev=False)
    p.add_argument("--report")
    p.add_argument("--gaps")
    p.add_argument("--deviations")
    p.add_argument("--catalog-dir", dest="catalog_dir", action="append")
    p.add_argument("--trust-root", dest="trust_root")
    p.add_argument(
        "--allow-unverified-catalog",
        dest="allow_unverified_catalog",
        action="store_true",
    )
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


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
