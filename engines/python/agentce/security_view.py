"""The security view (18.16): the same run, read for tool access, enforcement-point evidence, and
external-standard citations.

``compute_security_view`` adds no new rollup: ``summarize_activity`` (activity.py) already counts
tool access, actions by effect class, approvals and denials, and drift, and every ``Assertion``
(assertions.py) already carries its control's own ``crosswalk`` entries. This module only selects and
groups those two already-computed inputs -- it never re-scans events or re-derives a verdict.
"""

from __future__ import annotations

from typing import Any

from .assertions import Assertion

#: The three external-standard frameworks the security view cites (contracts/P18-18.16.md); a
#: control's crosswalk entries against any other framework (`eu-ai-act`, `iso-42001`, `nist-ai-rmf`,
#: `aiuc-1`) are out of scope for this view.
_CITED_FRAMEWORKS = frozenset({"owasp-asi-2026", "mitre-atlas", "owasp-acs"})

#: Each framework's own `version:` field, copied by hand from its crosswalk file under
#: `spec/catalogs/base/eu-ai-act/crosswalk/`. Crosswalk files are not engine-loaded at assessment
#: time (the crosswalk README's own hygiene rule), so this is the one place a version bump is made
#: when a cited framework's file changes; consulted only by the render layer to show
#: "mitre-atlas 2026.09" rather than a bare framework id next to a clause. `security.json` itself
#: carries no version field.
FRAMEWORK_VERSIONS = {
    "owasp-asi-2026": "2025.12",
    "mitre-atlas": "2026.09",
    "owasp-acs": "0.1.0",
}


def compute_security_view(
    activity: dict[str, Any], assertions: list[Assertion]
) -> dict[str, Any]:
    """Return the security-framed selection of ``activity``/``assertions`` for a whole run.

    Runs once over the pooled run regardless of subject count: unlike the project view's per-agent
    split, "what could have stopped this run's actions" is a whole-run posture question. Deterministic:
    no wall-clock, no locale, no assertion-order dependency (``standards_citations`` is deduplicated
    and sorted)."""
    citations = sorted(
        {
            (a.control, str(e["framework"]), str(e["clause"]), bool(e["verified"]))
            for a in assertions
            for e in a.crosswalk
            if e.get("framework") in _CITED_FRAMEWORKS
        }
    )
    return {
        "tool_access": activity["tools"],
        "actions_by_effect_class": activity["actions_by_effect_class"],
        "enforcement_point_evidence": {
            "denied_or_blocked": activity["denied_or_blocked"],
            "approvals_by_recorder": activity["approvals_by_recorder"],
        },
        "drift": {
            "tools": activity["undeclared"]["tools"],
            "models": activity["undeclared"]["models"],
        },
        "standards_citations": [
            {
                "control": control,
                "framework": framework,
                "clause": clause,
                "verified": verified,
            }
            for control, framework, clause, verified in citations
        ],
    }
