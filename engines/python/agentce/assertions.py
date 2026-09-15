"""Assertions: one machine-readable record per (control, subject) (SPEC §9.4, §9.2).

``assertions.json`` is the source of truth from which report.md/html, OSCAL, and SARIF are rendered; a
translation never changes it, and after RFC 8785 canonicalisation it is byte-identical across engines.
Every non-``not_applicable`` verdict that asserts support (``conformant``, ``non-conformant``,
``partial``) must cite at least one evidence pointer; the report generator refuses to emit one that
does not (DC-5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import AgentceError
from .exit_codes import ExitCode

OUTCOMES = (
    "conformant",
    "non-conformant",
    "partial",
    "not_applicable",
    "not_assessed",
    "insufficient_evidence",
)

#: Outcomes that assert support and therefore require evidence pointers (DC-5).
_REQUIRE_EVIDENCE = frozenset({"conformant", "non-conformant", "partial"})


@dataclass(frozen=True)
class EvidencePointer:
    ref: str
    digest: str
    source_class: str

    def to_json(self) -> dict[str, str]:
        return {
            "ref": self.ref,
            "digest": self.digest,
            "source_class": self.source_class,
        }


@dataclass
class Assertion:
    control: str
    control_version: str
    subject: str
    outcome: str
    rung: int
    mode: str
    window: tuple[str, str]
    population: tuple[int, int]
    expectations: list[dict[str, str]] = field(default_factory=list)
    violations: list[dict[str, str]] = field(default_factory=list)
    evidence: list[EvidencePointer] = field(default_factory=list)
    source_class_satisfied: bool | None = None
    evidence_strength: str | None = None
    deviation: str | None = None
    crosswalk: list[dict[str, str]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "control": self.control,
            "control_version": self.control_version,
            "subject": self.subject,
            "outcome": self.outcome,
            "rung": self.rung,
            "mode": self.mode,
            "window": {"start": self.window[0], "end": self.window[1]},
            "population": {
                "applicable": self.population[0],
                "failed": self.population[1],
            },
        }
        if self.expectations:
            record["expectations"] = self.expectations
        if self.violations:
            record["violations"] = self.violations
        if self.evidence:
            record["evidence"] = [e.to_json() for e in self.evidence]
        if self.source_class_satisfied is not None:
            record["source_class_satisfied"] = self.source_class_satisfied
        if self.evidence_strength is not None:
            record["evidence_strength"] = self.evidence_strength
        if self.deviation is not None:
            record["deviation"] = self.deviation
        if self.crosswalk:
            record["crosswalk"] = self.crosswalk
        return record


def aggregate(assertions: list[Assertion]) -> dict[str, int]:
    """Return the six-outcome counts (SPEC §9.2); every outcome is present, even at zero."""
    counts = dict.fromkeys(OUTCOMES, 0)
    for assertion in assertions:
        counts[assertion.outcome] = counts.get(assertion.outcome, 0) + 1
    return counts


def check_dc5(assertions: list[Assertion]) -> None:
    """Refuse to emit a supporting verdict with no evidence pointer (DC-5); raise on the first."""
    for assertion in assertions:
        if assertion.outcome in _REQUIRE_EVIDENCE and not assertion.evidence:
            raise AgentceError(
                key="report.missing_evidence_pointer",
                cause=(
                    f"control {assertion.control} on {assertion.subject} is {assertion.outcome} "
                    "but cites no evidence pointer."
                ),
                fix="every conformant, non-conformant, or partial outcome must cite evidence (DC-5).",
                exit_code=int(ExitCode.INPUT_ERROR),
            )
