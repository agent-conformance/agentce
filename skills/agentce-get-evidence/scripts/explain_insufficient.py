"""explain_insufficient (SPEC 13.3.3 step C.3): explain each insufficient_evidence outcome.

For every assertion whose outcome is ``insufficient_evidence``, this reports the control, the missing
event member, the minimum source class the control requires (read from its catalog with ``--catalogs``;
an assertion does not carry it), and -- by consulting the adapters' support matrices (SPEC 12.3) --
which adapter could supply the missing member. An assertion whose ``source_class_satisfied`` is false
had the records it needed, but from a source below the control's minimum (SPEC 6.4, HR-5): its root
cause is ``min_source_class=<class>``, a record of that class, not a member. Findings are
grouped by root cause (the missing member) so an operator fixes collection once, not control by
control. Deterministic, offline, model-free (rule S-4). Exit 0 when nothing is insufficient, 1 when
something is, 2 on input error, 3 on version mismatch.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from _common import FINDINGS, INPUT_ERROR, OK, arg_value, emit, run_guarded


def _adapters_supplying(support_dir: Path) -> dict[str, list[str]]:
    """Map member path -> sorted adapters that can populate it (from every support-matrix.yaml)."""
    supplies: dict[str, set[str]] = {}
    for matrix_path in sorted(support_dir.glob("*/support-matrix.yaml")):
        matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
        if not isinstance(matrix, dict):
            continue
        adapter = str(matrix.get("adapter", matrix_path.parent.name))
        events = matrix.get("events")
        if not isinstance(events, dict):
            continue
        for spec in events.values():
            for member in (
                (spec or {}).get("members", []) if isinstance(spec, dict) else []
            ):
                supplies.setdefault(str(member), set()).add(adapter)
    return {member: sorted(adapters) for member, adapters in supplies.items()}


def _minimums(catalogs_dir: Path) -> dict[str, str]:
    """Map control id -> its evaluation.min_source_class, from every controls/*.yaml under the folder."""
    minimums: dict[str, str] = {}
    for path in sorted(catalogs_dir.rglob("controls/*.yaml")):
        control = yaml.safe_load(path.read_text(encoding="utf-8"))
        evaluation = control.get("evaluation") if isinstance(control, dict) else None
        if isinstance(evaluation, dict) and isinstance(
            evaluation.get("min_source_class"), str
        ):
            minimums[str(control.get("id"))] = evaluation["min_source_class"]
    return minimums


def explain(
    assertions: list[dict[str, Any]],
    supplies: dict[str, list[str]],
    minimums: Mapping[str, str] = MappingProxyType({}),
) -> dict[str, Any]:
    """Group insufficient_evidence assertions by the missing member and suggest adapters."""
    by_cause: dict[str, dict[str, Any]] = {}
    for assertion in assertions:
        if str(assertion.get("outcome")) != "insufficient_evidence":
            continue
        control = str(assertion.get("control", assertion.get("id", "<control>")))
        minimum = minimums.get(control)
        missing = [
            str(m)
            for m in assertion.get("missing_members") or assertion.get("missing") or []
        ]
        if assertion.get("source_class_satisfied") is False:
            missing.append(f"min_source_class={minimum or '<unknown>'}")
        for member in missing or ["<unspecified>"]:
            cause = by_cause.setdefault(
                member,
                {
                    "missing_member": member,
                    "controls": [],
                    "min_source_class": minimum,
                    "suggested_adapters": supplies.get(member, []),
                },
            )
            if control not in cause["controls"]:
                cause["controls"].append(control)
    causes = [by_cause[key] for key in sorted(by_cause)]
    for cause in causes:
        cause["controls"] = sorted(cause["controls"])
    return {
        "insufficient": sum(len(c["controls"]) for c in causes),
        "root_causes": causes,
    }


def _body(argv: list[str]) -> int:
    want_json = "--json" in argv
    assertions_arg = arg_value(argv, "--assertions")
    support_arg = arg_value(argv, "--support-matrices")
    if assertions_arg is None or not Path(assertions_arg).is_file():
        emit(
            {"status": "input_error", "message": "pass --assertions <file>"},
            want_json=want_json,
            human="INPUT ERROR: pass --assertions <assertions.json>",
        )
        return INPUT_ERROR
    try:
        raw = json.loads(Path(assertions_arg).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        emit(
            {"status": "input_error", "message": exc.msg},
            want_json=want_json,
            human=f"INPUT ERROR: {exc.msg}",
        )
        return INPUT_ERROR
    assertions = raw if isinstance(raw, list) else raw.get("assertions", [])
    supplies = (
        _adapters_supplying(Path(support_arg))
        if support_arg and Path(support_arg).is_dir()
        else {}
    )
    catalogs_arg = arg_value(argv, "--catalogs")
    minimums = (
        _minimums(Path(catalogs_arg))
        if catalogs_arg and Path(catalogs_arg).is_dir()
        else {}
    )
    report = explain(
        assertions if isinstance(assertions, list) else [], supplies, minimums
    )
    human = (
        "EXPLAIN INSUFFICIENT: none"
        if report["insufficient"] == 0
        else f"EXPLAIN INSUFFICIENT: {len(report['root_causes'])} root cause(s), {report['insufficient']} control(s)"
    )
    emit(report, want_json=want_json, human=human)
    return OK if report["insufficient"] == 0 else FINDINGS


def main(argv: list[str] | None = None) -> int:
    return run_guarded(list(sys.argv[1:] if argv is None else argv), _body)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
