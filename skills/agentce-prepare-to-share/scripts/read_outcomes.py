"""read_outcomes — summarise a report's six outcomes per family, and refuse a composite score.

SPEC §13.3.4 stage 5 and DC-4: the report is read in the fixed six-outcome vocabulary
(conformant, non-conformant, partial, not_applicable, not_assessed, insufficient_evidence) per
control family; the skill refuses to produce a single "percent compliant" figure. Asking for a
composite score (``--score`` or ``--composite``) is refused with the fixed explanation.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from _common import FINDINGS, INPUT_ERROR, OK, arg_value, emit, run_guarded

_REFUSAL = (
    "AgentCE does not produce a composite score or a percent-compliant figure (DC-4). A report "
    "states each control's outcome in six categories (conformant, non-conformant, partial, "
    "not_applicable, not_assessed, insufficient_evidence); read those, not a single number."
)
_OUTCOMES = (
    "conformant",
    "non-conformant",
    "partial",
    "not_applicable",
    "not_assessed",
    "insufficient_evidence",
)


def body(argv: list[str]) -> int:
    want_json = "--json" in argv
    if "--score" in argv or "--composite" in argv:
        emit(
            {"status": "refused", "reason": _REFUSAL},
            want_json=want_json,
            human=f"REFUSED: {_REFUSAL}",
        )
        return FINDINGS
    report = arg_value(argv, "--report")
    if not report or not (Path(report) / "assertions.json").is_file():
        emit(
            {"status": "input_error", "message": "pass --report <report-dir>"},
            want_json=want_json,
            human="INPUT ERROR: pass --report <report-dir>",
        )
        return INPUT_ERROR
    assertions = json.loads((Path(report) / "assertions.json").read_text("utf-8"))
    by_family: dict[str, dict[str, int]] = defaultdict(
        lambda: {o: 0 for o in _OUTCOMES}
    )
    for assertion in assertions:
        family = str(assertion.get("control", "")).split("-", 1)[0]
        outcome = str(assertion.get("outcome", ""))
        if outcome in _OUTCOMES:
            by_family[family][outcome] += 1
    counts = {family: by_family[family] for family in sorted(by_family)}
    emit(
        {"families": counts},
        want_json=want_json,
        human="\n".join(
            f"{family}: "
            + ", ".join(
                f"{o}={counts[family][o]}" for o in _OUTCOMES if counts[family][o]
            )
            for family in counts
        ),
    )
    return OK


if __name__ == "__main__":
    raise SystemExit(run_guarded(sys.argv[1:], body))
