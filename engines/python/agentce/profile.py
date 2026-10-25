"""The applicability profile (SPEC §6.5): what the adopter declares about the assessed system.

The profile names the observation window, the subjects (agents) under assessment, each subject's
evidence sources and their trust classes, the independent coverage denominators, the declared
decision types and oversight modalities, and the catalogs to apply. This module parses the normative
fields the coverage and applicability stages need; unknown fields are ignored so the profile can
carry more than one engine version reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import InputError
from .safe_yaml import load_untrusted_yaml


@dataclass
class EvidenceSource:
    adapter: str
    source: str
    cls: str
    manifest: str | None = None


@dataclass
class CoverageDenominator:
    kind: str
    source: str
    covers: list[str] = field(default_factory=list)
    manifest: str | None = None
    statement: str | None = None


@dataclass
class Subject:
    id: str
    name: str | None = None
    role: str | None = None
    evidence_sources: list[EvidenceSource] = field(default_factory=list)
    coverage_denominators: list[CoverageDenominator] = field(default_factory=list)
    declared_decision_types: list[str] = field(default_factory=list)
    declared_oversight: dict[str, str] = field(default_factory=dict)
    declared_components: list[str] = field(default_factory=list)
    #: Tools/models the adopter expects (18.4), the same shape as ``declared_decision_types`` but
    #: read only by ``agentce.activity``: informational display, not a conformance-relevant drift
    #: finding like ``declared_decision_types``/``declared_components`` (``agentce.applicability``) --
    #: an agent trying a new tool is everyday behaviour to surface, not itself a violation.
    declared_tools: list[str] = field(default_factory=list)
    declared_models: list[str] = field(default_factory=list)

    @property
    def source_ids(self) -> set[str]:
        return {s.source for s in self.evidence_sources}


@dataclass
class Profile:
    profile_version: int = 1
    observation_window: dict[str, str] = field(default_factory=dict)
    subjects: list[Subject] = field(default_factory=list)
    catalogs: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        profile = cls(
            profile_version=int(data.get("profile_version", 1)),
            observation_window=_str_map(data.get("observation_window")),
            catalogs=[str(c) for c in data.get("catalogs", []) or []],
        )
        for raw in data.get("subjects", []) or []:
            if isinstance(raw, dict) and "id" in raw:
                profile.subjects.append(_subject(raw))
        return profile

    @classmethod
    def load(cls, path: Path) -> Profile:
        data = (
            load_untrusted_yaml(
                path, key="input.profile_invalid", what="the applicability profile"
            )
            or {}
        )
        if not isinstance(data, dict):
            return cls()
        _check_window(data, path)
        return cls.from_dict(data)


def _check_window(data: dict[str, Any], path: Path) -> None:
    """Refuse a declared observation window that is not a mapping, or whose ``start`` or ``end`` is not
    an RFC 3339 date or date-time (``parse_date``'s grammar, the one deviation dates use): the window end
    is the date deviation expiries are checked against, so an unreadable one would apply an expired
    deviation (18.87). An absent window, or an absent bound, still falls back to the records' times."""
    from .readiness import (
        parse_date,
    )  # readiness imports coverage, which imports this module

    if "observation_window" not in data:
        return
    window = data["observation_window"]
    shown = f"the applicability profile at {str(path)!r}"
    if not isinstance(window, dict):
        raise InputError(
            "input.profile_invalid",
            f"{shown} has an observation_window that is not a mapping of start and end.",
            _WINDOW_FIX,
        )
    for bound in ("start", "end"):
        if bound in window and parse_date(str(window[bound])) is None:
            raise InputError(
                "input.profile_invalid",
                f"{shown} has an observation_window {bound} that is not an RFC 3339 date or date-time.",
                _WINDOW_FIX,
            )


_WINDOW_FIX = (
    'write observation_window as start and end date-times, such as start: "2026-01-01T00:00:00Z" '
    'and end: "2026-02-01T00:00:00Z".'
)


def _str_map(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _subject(raw: dict[str, Any]) -> Subject:
    subject = Subject(
        id=str(raw["id"]),
        name=_opt(raw.get("name")),
        role=_opt(raw.get("role")),
        declared_decision_types=[
            str(t) for t in raw.get("declared_decision_types", []) or []
        ],
        declared_oversight=_str_map(raw.get("declared_oversight")),
        declared_components=[
            str(c["name"])
            for c in raw.get("third_party_components", []) or []
            if isinstance(c, dict) and "name" in c
        ],
        declared_tools=[str(t) for t in raw.get("declared_tools", []) or []],
        declared_models=[str(m) for m in raw.get("declared_models", []) or []],
    )
    for entry in raw.get("evidence_sources", []) or []:
        if isinstance(entry, dict) and "source" in entry:
            subject.evidence_sources.append(
                EvidenceSource(
                    adapter=str(entry.get("adapter", "")),
                    source=str(entry["source"]),
                    cls=str(entry.get("class", "")),
                    manifest=_opt(entry.get("manifest")),
                )
            )
    for entry in raw.get("coverage_denominators", []) or []:
        if isinstance(entry, dict) and "source" in entry:
            subject.coverage_denominators.append(
                CoverageDenominator(
                    kind=str(entry.get("kind", "")),
                    source=str(entry["source"]),
                    covers=[str(c) for c in entry.get("covers", []) or []],
                    manifest=_opt(entry.get("manifest")),
                    statement=_opt(entry.get("statement")),
                )
            )
    return subject


def _opt(value: object) -> str | None:
    return str(value) if isinstance(value, str) else None
