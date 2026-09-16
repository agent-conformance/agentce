"""checklist_lint — validate completed manual-checklist records (SPEC §13.3.4 stage 3).

Wraps the engine's ``agentce.readiness.checklist_lint``: each record validates against
``checklist.schema.json``, carries the assessor identity, and its inspected artifact digests are
fresh. Reads a directory of record YAML/JSON files or a single file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from _common import FINDINGS, INPUT_ERROR, OK, arg_value, emit, run_guarded

from agentce import readiness


def _load_records(path: Path) -> list[dict]:
    files = (
        sorted(path.glob("*.y*ml")) + sorted(path.glob("*.json"))
        if path.is_dir()
        else [path]
    )
    records: list[dict] = []
    for file in files:
        data = yaml.safe_load(file.read_text("utf-8"))
        if isinstance(data, dict):
            records.append(data)
    return records


def body(argv: list[str]) -> int:
    want_json = "--json" in argv
    records_arg = arg_value(argv, "--records")
    if not records_arg or not Path(records_arg).exists():
        emit(
            {"status": "input_error", "message": "pass --records <dir-or-file>"},
            want_json=want_json,
            human="INPUT ERROR: pass --records <dir-or-file>",
        )
        return INPUT_ERROR
    digests_arg = arg_value(argv, "--digests")
    current = json.loads(Path(digests_arg).read_text("utf-8")) if digests_arg else None
    problems = readiness.checklist_lint(
        _load_records(Path(records_arg)), current_digests=current
    )
    emit(
        {"problems": problems, "clean": not problems},
        want_json=want_json,
        human="CHECKLISTS OK" if not problems else "\n".join(problems),
    )
    return OK if not problems else FINDINGS


if __name__ == "__main__":
    raise SystemExit(run_guarded(sys.argv[1:], body))
