"""Message-key catalogue for report rendering (SPEC §9.3, §8.4).

All human-readable text in ``report.md`` and ``report.html`` comes from message keys, so a
translation changes only the report — never ``assertions.json``, the manifest digests, or the claim.
v1 ships the ``en`` catalogue; a partial ``de`` catalogue demonstrates the translation mechanism (any
key it omits falls back to ``en``). The report language is recorded in the manifest as
``run.report_language`` and has no effect on the machine-readable outputs.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

_EN: dict[str, str] = {
    "report.title": "AgentCE conformance report",
    "report.summary_heading": "Outcome summary",
    "report.assertions_heading": "Assertions",
    "report.no_controls": "No controls were evaluated.",
    "report.affected_persons": (
        "Affected persons may obtain an explanation and raise concerns through the deployer's "
        "published contact channel (EU AI Act Arts. 26(11), 85, 86)."
    ),
    "outcome.conformant": "conformant",
    "outcome.non-conformant": "non-conformant",
    "outcome.partial": "partial",
    "outcome.not_applicable": "not applicable",
    "outcome.not_assessed": "not assessed",
    "outcome.insufficient_evidence": "insufficient evidence",
}

#: A partial translation, to exercise the mechanism; missing keys fall back to ``en``.
_DE: dict[str, str] = {
    "report.title": "AgentCE-Konformitätsbericht",
    "report.summary_heading": "Ergebnisübersicht",
    "report.assertions_heading": "Aussagen",
    "report.no_controls": "Es wurden keine Kontrollen bewertet.",
}

_CATALOGUES: dict[str, dict[str, str]] = {"en": _EN, "de": _DE}


def available_languages() -> list[str]:
    return sorted(_CATALOGUES)


def catalogue(language: str = DEFAULT_LANGUAGE) -> dict[str, str]:
    """Return the message catalogue for ``language``, backed by ``en`` for any missing key."""
    merged = dict(_EN)
    merged.update(_CATALOGUES.get(language, {}))
    return merged
