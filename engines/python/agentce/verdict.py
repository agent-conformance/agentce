"""The run verdict: one categorical word, the six-outcome tally, and the top gaps (SPEC §9.2, §9.3).

The verdict is derived from the assertions and nothing else. It is deliberately not a score: SPEC §9.2
forbids a single composite number, so the verdict is one of three fixed states and the report still shows
all six counts beside it. ``summarize`` is the one place the tally, the verdict, and the gap list are
computed; ``report.md``, ``report.html``, the ``--json`` envelope, and the command-line summary all render
that one dictionary, so they cannot disagree.
"""

from __future__ import annotations

from typing import Any

from .assertions import Assertion, aggregate

#: A control failed for at least one subject.
NON_CONFORMANT = "non-conformant"
#: Nothing failed, but at least one control is partial, lacks evidence, or was not assessed.
INCOMPLETE = "incomplete"
#: Every applicable control met its expectations with evidence.
CONFORMANT = "conformant"

#: Outcomes that count as a gap, most urgent first (a failure before a shortfall in evidence).
GAP_OUTCOMES = ("non-conformant", "partial", "insufficient_evidence", "not_assessed")

#: How many controls a gap line names before it says how many more there are.
MAX_LISTED = 5


def summarize(assertions: list[Assertion]) -> dict[str, Any]:
    """Return ``{"verdict", "counts", "top_gaps"}`` for ``assertions``.

    ``counts`` is :func:`agentce.assertions.aggregate` (all six outcomes, in report order). ``top_gaps``
    lists, per gap outcome that occurred, up to :data:`MAX_LISTED` distinct control ids in byte order and
    how many more there are. Nothing here depends on assertion order, the clock, or the locale.
    """
    counts = aggregate(assertions)
    if counts["non-conformant"]:
        verdict = NON_CONFORMANT
    elif counts["partial"] or counts["insufficient_evidence"] or counts["not_assessed"]:
        verdict = INCOMPLETE
    else:
        verdict = CONFORMANT
    top_gaps: list[dict[str, Any]] = []
    for outcome in GAP_OUTCOMES:
        controls = sorted({a.control for a in assertions if a.outcome == outcome})
        if controls:
            top_gaps.append(
                {
                    "outcome": outcome,
                    "controls": controls[:MAX_LISTED],
                    "more": max(0, len(controls) - MAX_LISTED),
                }
            )
    return {"verdict": verdict, "counts": counts, "top_gaps": top_gaps}


def gap_text(gap: dict[str, Any], catalogue: dict[str, str]) -> str:
    """One gap as text: ``insufficient evidence: DAT-01, DAT-02 (+14 more)``."""
    label = catalogue.get(f"outcome.{gap['outcome']}", gap["outcome"])
    text = f"{label}: {', '.join(gap['controls'])}"
    if gap["more"]:
        text += f" ({catalogue['report.gaps_more'].replace('{n}', str(gap['more']))})"
    return text


def cli_lines(
    summary: dict[str, Any], catalogue: dict[str, str], *, report_dir: str
) -> list[str]:
    """The lines a command prints for ``summary``: the verdict, the tally, the top gaps, the next step."""
    verdict = summary["verdict"]
    tally = ", ".join(
        f"{catalogue.get(f'outcome.{outcome}', outcome)} {count}"
        for outcome, count in summary["counts"].items()
    )
    lines = [
        f"{catalogue['report.verdict_heading']}: {catalogue[f'verdict.{verdict}']}",
        f"{catalogue['report.outcomes_label']}: {tally}",
    ]
    if summary["top_gaps"]:
        lines.append(f"{catalogue['report.top_gaps_heading']}:")
        lines += [f"  {gap_text(gap, catalogue)}" for gap in summary["top_gaps"]]
    else:
        lines.append(
            f"{catalogue['report.top_gaps_heading']}: {catalogue['report.no_gaps']}"
        )
    lines.append(
        f"{catalogue['report.next_step_heading']}: {catalogue[f'next.{verdict}']} "
        + catalogue["report.see_report"].replace("{dir}", report_dir)
    )
    return lines
