"""deviation_lint — validate a deviation register before it is applied (SPEC §13.3.4 stage 3).

Wraps the engine's ``agentce.readiness.deviation_lint``. A deviation is valid only for a
``non-conformant`` outcome (never ``insufficient_evidence``), never on the INT family, with every
field present, an approver distinct from the owner, and an expiry within the catalog maximum.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from _common import FINDINGS, INPUT_ERROR, OK, arg_value, emit, run_guarded

from agentce import readiness
from agentce.catalog import load_catalog


def _control_ids(argv: list[str]) -> set[str]:
    ids: set[str] = set()
    repo = Path(__file__).resolve().parents[3]
    dirs = [
        Path(argv[i + 1])
        for i, a in enumerate(argv)
        if a == "--catalog-dir" and i + 1 < len(argv)
    ]
    if not dirs:
        dirs = [
            p.parent
            for p in sorted(
                (repo / "spec" / "catalogs" / "base").glob("*/catalog.yaml")
            )
        ]
    for directory in dirs:
        ids.update(c.id for c in load_catalog(directory).controls)
    return ids


def body(argv: list[str]) -> int:
    want_json = "--json" in argv
    dev_file = arg_value(argv, "--deviations")
    report = arg_value(argv, "--report")
    if not dev_file or not report:
        emit(
            {
                "status": "input_error",
                "message": "pass --deviations <file> --report <report-dir>",
            },
            want_json=want_json,
            human="INPUT ERROR: pass --deviations <file> --report <report-dir>",
        )
        return INPUT_ERROR
    deviations = list(
        (yaml.safe_load(Path(dev_file).read_text("utf-8")) or {}).get("deviations", [])
    )
    assertions = json.loads((Path(report) / "assertions.json").read_text("utf-8"))
    outcome_by_control = {
        str(a.get("control")): str(a.get("outcome")) for a in assertions
    }
    problems = readiness.deviation_lint(
        deviations,
        control_ids=_control_ids(argv),
        outcome_by_control=outcome_by_control,
    )
    emit(
        {"problems": problems, "clean": not problems},
        want_json=want_json,
        human="DEVIATIONS OK" if not problems else "\n".join(problems),
    )
    return OK if not problems else FINDINGS


if __name__ == "__main__":
    raise SystemExit(run_guarded(sys.argv[1:], body))
