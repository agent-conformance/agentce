"""The buyer view (18.18): generated questionnaire answers, a one-page summary, and how to check
the report.

``compute_buyer_view`` adds no new rollup and re-derives no verdict (SPEC §9.2): one ``Assertion``
already is one clause's full record, and a CAIQ/AI Controls Matrix question is answered by selecting
the subset of ``Assertion.crosswalk`` entries whose ``framework`` is one of the two questionnaire
frameworks this view cites -- the same selection ``auditor_view.by_clause`` already makes for every
external framework, narrowed to two. A question with zero mapped controls is simply absent from
``answers`` (auditor view's own semantics: "maps to nothing in scope, not nothing"); "not enough
evidence" applies at the control level, for a question that IS mapped but whose control's outcome is
``insufficient_evidence``.

Only CAIQ and the AI Controls Matrix are built here (the source row's "licensed ones by id only"):
SIG and other licensed questionnaires are out of scope, since no licensed-questionnaire crosswalk
exists in this repo today.
"""

from __future__ import annotations

from typing import Any

from . import messages
from .assertions import Assertion, aggregate

#: The two questionnaire frameworks this view answers, in fixed render order (a tuple, not a set --
#: deterministic order matters for the rendered page and the golden file). ``caiq`` first: it is the
#: only one with real answers today.
BUYER_FRAMEWORKS: tuple[str, ...] = ("caiq", "ai-controls-matrix")

#: Each framework's own `version:` field, copied by hand from its crosswalk file under
#: `spec/catalogs/base/eu-ai-act/crosswalk/` (the `security_view.FRAMEWORK_VERSIONS` idiom).
#: Crosswalk files are not engine-loaded at assessment time, so this is the one place a version bump
#: is made when a cited questionnaire's file changes.
BUYER_QUESTIONNAIRE_VERSIONS: dict[str, str] = {
    "caiq": "4.0.2",
    "ai-controls-matrix": "1.1",
}

#: Each framework's display title, for the "evidence against {title} {version}, not a certification"
#: heading -- ``BUYER_QUESTIONNAIRE_VERSIONS`` names only the version, not a human-readable name.
BUYER_QUESTIONNAIRE_TITLES: dict[str, str] = {
    "caiq": "the CAIQ",
    "ai-controls-matrix": "the AI Controls Matrix",
}

#: Modes whose ``not_assessed`` outcome gets the disclosed not-yet-evaluated note (the
#: ``auditor_view._MANUAL_MODES`` idiom, reused verbatim).
_MANUAL_MODES = frozenset({"manual", "semi-automated"})


def _check_ref_matches(ref: dict[str, str], a: Assertion) -> bool:
    """Whether a ``compute_blind_spots`` check-ref names the same (subject, control,
    control_version) triple as ``a``. A check-ref also carries ``catalog``, which ``Assertion`` does
    not -- this can tie across two catalogs that happen to share a control id and version; a real but
    narrow, disclosed limitation (no other view in this codebase resolves it either)."""
    return (
        ref["subject"] == a.subject
        and ref["control"] == a.control
        and ref["control_version"] == a.control_version
    )


def _buyer_gap_step(a: Assertion, blind_spots: dict[str, Any]) -> dict[str, Any] | None:
    """The "not enough evidence" gap step for an ``insufficient_evidence`` answer, or ``None`` when
    ``a``'s triple matches neither of :func:`agentce.blind_spots.compute_blind_spots`'s own two
    buckets (an assertion outcome other than ``insufficient_evidence`` never reaches this function at
    all, so ``None`` here means only "not classified in either bucket", never "not looked up").

    Never raises: a caller that renders ``write_report``'s own ``blind_spots`` local always has it
    (normalized to an empty-but-present shape before this view ever runs), but a direct caller (a
    test, a re-render from ``assertions.json`` alone) may pass a genuinely empty ``blind_spots``; that
    is an honest "no step known", not a bug to assert against."""
    missing = [
        {
            "event": bs["event"],
            "class": bs["class"],
            "ladder_rung": bs["ladder_rung"],
            "owner_key": bs["owner_key"],
            "step_kind": bs["step_kind"],
        }
        for bs in blind_spots.get("blind_spots", [])
        if any(
            _check_ref_matches(ref, a)
            for ref in bs["unlocked_checks"] + bs["needed_by_checks"]
        )
    ]
    if missing:
        return {"kind": "blind_spot", "missing": missing}
    if any(_check_ref_matches(ref, a) for ref in blind_spots.get("no_population", [])):
        return {"kind": "no_population"}
    return None


def compute_buyer_view(
    assertions: list[Assertion],
    blind_spots: dict[str, Any],
    *,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return the questionnaire-answer selection of ``assertions`` for a whole run.

    ``blind_spots`` is :func:`agentce.blind_spots.compute_blind_spots`'s own output for this same run
    -- the same already-computed sibling artifact :func:`agentce.project.compute_project_view` already
    takes, passed through here rather than recomputed (no new I/O, no new catalog read). ``counts``
    lets a caller that already holds :func:`agentce.assertions.aggregate`'s result pass it straight
    through, matching ``compute_auditor_view``'s own ``counts`` parameter.

    Deterministic: no wall-clock, no locale, no assertion-order dependency. ``answers`` is sorted by
    ``(framework, question, control, control_version, subject, outcome)`` -- ``control_version`` and
    ``outcome`` are in the key (not just ``control``) so two entries citing the same control id from
    two different catalogs, or two runs of the same control with different outcomes, still sort into a
    stable, disambiguated order."""
    cat = messages.catalogue()
    note = cat["report.manual_checklist_not_yet_evaluated"]

    answers: list[dict[str, Any]] = []
    for a in assertions:
        for xw in a.crosswalk:
            framework = str(xw.get("framework", ""))
            if framework not in BUYER_FRAMEWORKS:
                continue
            entry: dict[str, Any] = {
                "framework": framework,
                "question": str(xw.get("clause", "")),
                "control": a.control,
                "control_version": a.control_version,
                "subject": a.subject,
                "outcome": a.outcome,
                "mode": a.mode,
                "verified": bool(xw.get("verified", False)),
                "evidence": [e.to_json() for e in a.evidence],
                "gap_step": (
                    _buyer_gap_step(a, blind_spots)
                    if a.outcome == "insufficient_evidence"
                    else None
                ),
            }
            if a.mode in _MANUAL_MODES and a.outcome == "not_assessed":
                entry["manual_checklist_note"] = note
            answers.append(entry)
    answers.sort(
        key=lambda e: (
            e["framework"],
            e["question"],
            e["control"],
            e["control_version"],
            e["subject"],
            e["outcome"],
        )
    )

    by_question: dict[str, dict[str, list[int]]] = {fw: {} for fw in BUYER_FRAMEWORKS}
    for index, entry in enumerate(answers):
        by_question[entry["framework"]].setdefault(entry["question"], []).append(index)

    return {
        "answers": answers,
        "by_question": by_question,
        "counts": counts if counts is not None else aggregate(assertions),
    }
