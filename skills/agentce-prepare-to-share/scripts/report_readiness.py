"""report_readiness — the post-run readiness verdict (SPEC §13.3.4 stage 4).

A thin wrapper over the engine's readiness logic (``agentce.readiness``), where the verdict lives, so
signing never depends on a skill script (S-5, SPEC §8.5). It reads a finished report directory, the
catalogs the report used (for control severities), and optional gaps and deviation files, and prints
the verdict: READY, READY WITH LIMITATIONS, or NOT READY. Exit 0 for the first two, 1 for NOT READY.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml
from _common import FINDINGS, INPUT_ERROR, OK, arg_value, emit, run_guarded

from agentce import readiness
from agentce.catalog import load_catalog


def _catalog_dirs(argv: list[str]) -> list[Path]:
    dirs = [
        Path(argv[i + 1])
        for i, a in enumerate(argv)
        if a == "--catalog-dir" and i + 1 < len(argv)
    ]
    if dirs:
        return dirs
    repo = Path(__file__).resolve().parents[3]
    base = repo / "spec" / "catalogs" / "base"
    return [p.parent for p in sorted(base.glob("*/catalog.yaml"))]


def _severities(argv: list[str]) -> dict[str, str]:
    severities: dict[str, str] = {}
    for directory in _catalog_dirs(argv):
        for control in load_catalog(directory).controls:
            severities[control.id] = control.severity
    return severities


def body(argv: list[str]) -> int:
    want_json = "--json" in argv
    report = arg_value(argv, "--report")
    if not report or not Path(report).is_dir():
        emit(
            {"status": "input_error", "message": "pass --report <report-dir>"},
            want_json=want_json,
            human="INPUT ERROR: pass --report <report-dir>",
        )
        return INPUT_ERROR
    gaps: set[str] = set()
    gaps_file = arg_value(argv, "--gaps")
    if gaps_file:
        gaps = set(
            re.findall(r"\b[A-Z]{2,4}-[0-9]{2}\b", Path(gaps_file).read_text("utf-8"))
        )
    deviations: list[dict] = []
    dev_file = arg_value(argv, "--deviations")
    if dev_file:
        loaded = yaml.safe_load(Path(dev_file).read_text("utf-8")) or {}
        deviations = list(loaded.get("deviations", []))
    verdict = readiness.compute_readiness(
        Path(report), severities=_severities(argv), deviations=deviations, gaps=gaps
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
