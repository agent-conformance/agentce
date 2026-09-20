"""The applicability profiles the deterministic generator writes (SPEC §6.5, §11.2).

Every profile must validate against the applicability-profile schema the engine hands adopters
(:func:`agentce.tools.validate_profile.validate_profile`), so the corpus that backs the accuracy claims
is itself a set of valid inputs, and the coverage-gap projects declare their independent denominator
the way the schema defines it: a kind from its enumeration, the source, and what it covers.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from agentce.tools.validate_profile import validate_profile

import corpus.generator.generate as generate

DENOMINATOR_KINDS = {
    "provider_usage_export",
    "egress_proxy_log",
    "firewall_log",
    "system_of_record_export",
    "admission_attestation",
}


@pytest.fixture(scope="module")
def full_corpus(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    original = generate.FILLER_EVENTS
    generate.FILLER_EVENTS = 40  # a fast, representative high-volume bundle
    try:
        out = tmp_path_factory.mktemp("corpus-full")
        generate.build_corpus(out, "full")
        yield out
    finally:
        generate.FILLER_EVENTS = original


def _profiles(corpus_dir: Path) -> dict[Path, dict]:
    paths = sorted(corpus_dir.glob("projects/**/applicability.yaml"))
    assert paths, "the generator wrote no applicability profiles"
    return {p: yaml.safe_load(p.read_text(encoding="utf-8")) for p in paths}


def _event_sources(project_dir: Path) -> set[str]:
    return {
        json.loads(line)["source"]
        for path in (project_dir / "evidence" / "events").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def test_every_generated_profile_validates_against_the_schema(
    full_corpus: Path,
) -> None:
    invalid = {
        str(path.relative_to(full_corpus)): errors[0]
        for path, profile in _profiles(full_corpus).items()
        if (errors := validate_profile(profile))
    }
    assert not invalid


def test_every_evidence_source_carries_its_trust_class_justification(
    full_corpus: Path,
) -> None:
    for profile in _profiles(full_corpus).values():
        for subject in profile["subjects"]:
            for source in subject["evidence_sources"]:
                assert source["class_justification"].strip()


def test_coverage_gap_denominators_use_a_schema_kind_and_name_a_source_with_events(
    full_corpus: Path,
) -> None:
    gap_profiles = {
        path: profile
        for path, profile in _profiles(full_corpus).items()
        if path.parent.name == "coverage-gap"
    }
    assert gap_profiles
    for path, profile in gap_profiles.items():
        denominators = profile["subjects"][0]["coverage_denominators"]
        assert denominators
        for denominator in denominators:
            assert denominator["kind"] in DENOMINATOR_KINDS
            assert set(denominator) <= {"kind", "source", "covers", "statement"}
            assert denominator["source"] in _event_sources(path.parent)
