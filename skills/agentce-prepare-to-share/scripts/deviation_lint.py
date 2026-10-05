"""deviation_lint — validate a deviation register before it is applied (SPEC §13.3.4 stage 3).

Wraps the engine's ``agentce.readiness.deviation_lint``. A deviation is valid only for a
``non-conformant`` outcome (never ``insufficient_evidence``), never on the INT family, with every
field present, an approver distinct from the owner, and an expiry within the catalog maximum.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from _common import (
    FINDINGS,
    INPUT_ERROR,
    OK,
    arg_value,
    default_catalog_dirs,
    emit,
    run_guarded,
)

from agentce import readiness
from agentce.catalog import load_catalog
from agentce.commands import _load_deviation_register
from agentce.errors import AgentceError


def _control_ids(argv: list[str]) -> set[str]:
    ids: set[str] = set()
    dirs = [
        Path(argv[i + 1])
        for i, a in enumerate(argv)
        if a == "--catalog-dir" and i + 1 < len(argv)
    ]
    if not dirs:
        dirs = default_catalog_dirs()
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
    try:
        deviations = _load_deviation_register(Path(dev_file))
    except AgentceError as exc:
        emit(
            {"status": "input_error", "message": str(exc)},
            want_json=want_json,
            human=f"INPUT ERROR: {exc}",
        )
        return INPUT_ERROR
    assertions = json.loads((Path(report) / "assertions.json").read_text("utf-8"))
    outcomes_by_control: dict[str, set[str]] = {}
    applied_controls: set[str] = set()
    window_ends: list[str] = []
    for a in assertions:
        control = str(a.get("control"))
        outcomes_by_control.setdefault(control, set()).add(str(a.get("outcome")))
        if a.get("deviation"):
            applied_controls.add(control)
        end = (a.get("window") or {}).get("end")
        if end:
            window_ends.append(str(end))
    problems = readiness.deviation_lint(
        deviations,
        control_ids=_control_ids(argv),
        outcomes_by_control={
            control: frozenset(outcomes)
            for control, outcomes in outcomes_by_control.items()
        },
        applied_controls=frozenset(applied_controls),
        as_of=max(window_ends) if window_ends else None,
    )
    emit(
        {"problems": problems, "clean": not problems},
        want_json=want_json,
        human="DEVIATIONS OK" if not problems else "\n".join(problems),
    )
    return OK if not problems else FINDINGS


if __name__ == "__main__":
    raise SystemExit(run_guarded(sys.argv[1:], body))
