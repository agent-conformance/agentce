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

import yaml


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

    @property
    def source_ids(self) -> set[str]:
        return {s.source for s in self.evidence_sources}

    @property
    def denominator_ids(self) -> set[str]:
        return {d.source for d in self.coverage_denominators}


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
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return cls()
        return cls.from_dict(data)


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
