"""The cross-engine comparison covers the whole canonical set, not just assertions.json (SPEC §11.5).

These tests build two engines' output trees by hand, so they run everywhere and pin what the comparator
treats as a divergence: a data artifact that differs by a byte, a missing artifact, a per-assertion
evidence array that one engine omits, and — as the one tolerated difference — the engine name and version
each engine deliberately carries in the OSCAL and SARIF translations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ecs

PID = "p1"
ASSERTIONS = b'[{"control":"DAT-01","outcome":"conformant"}]'


def _pack(evidence: bool) -> bytes:
    row: dict[str, object] = {
        "control": "DAT-01",
        "mode": "automated",
        "outcome": "conformant",
    }
    if evidence:
        row["evidence"] = ["ev/1"]
    return json.dumps(
        {"subject": "s", "assertions": [row], "evidence": ["ev/1"]}, sort_keys=True
    ).encode()


def _sarif(name: str, version: str = "0.1.0") -> bytes:
    driver = {"name": name, "version": version, "rules": [{"id": "DAT-01"}]}
    return json.dumps(
        {"version": "2.1.0", "runs": [{"tool": {"driver": driver}, "results": []}]}
    ).encode()


def _oscal(version: str = "0.1.0") -> bytes:
    metadata = {"title": "t", "version": version}
    return json.dumps({"assessment-results": {"metadata": metadata}}).encode()


def _write(out: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        target = out / "projects" / PID / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return out


def _engine(
    out: Path,
    *,
    name: str = "agentce-py",
    version: str = "0.1.0",
    evidence: bool = True,
    assertions: bytes = ASSERTIONS,
) -> Path:
    return _write(
        out,
        {
            "assertions.json": assertions,
            "oscal-ar.json": _oscal(version),
            "results.sarif": _sarif(name, version),
            "packs/s/pack.json": _pack(evidence),
        },
    )


def test_identical_engines_have_no_divergence(tmp_path: Path) -> None:
    ref = _engine(tmp_path / "ref")
    other = _engine(tmp_path / "other")
    assert ecs.canonical_divergences(ref, other, PID) == []


def test_each_engine_keeps_its_own_name_and_version(tmp_path: Path) -> None:
    ref = _engine(tmp_path / "ref", name="agentce-py", version="0.1.0")
    other = _engine(tmp_path / "other", name="agentce-ts", version="0.2.0")
    assert ecs.canonical_divergences(ref, other, PID) == []


def test_a_pack_missing_the_per_assertion_evidence_diverges(tmp_path: Path) -> None:
    ref = _engine(tmp_path / "ref")
    other = _engine(tmp_path / "other", evidence=False)
    assert ecs.canonical_divergences(ref, other, PID) == ["packs/s/pack.json"]


def test_a_differing_assertions_byte_diverges(tmp_path: Path) -> None:
    ref = _engine(tmp_path / "ref")
    other = _engine(tmp_path / "other", assertions=ASSERTIONS + b"\n")
    assert ecs.canonical_divergences(ref, other, PID) == ["assertions.json"]


@pytest.mark.parametrize(
    "missing", ["oscal-ar.json", "results.sarif", "packs/s/pack.json"]
)
def test_an_artifact_one_engine_did_not_write_diverges(
    tmp_path: Path, missing: str
) -> None:
    ref = _engine(tmp_path / "ref")
    other = _engine(tmp_path / "other")
    (other / "projects" / PID / missing).unlink()
    assert ecs.canonical_divergences(ref, other, PID) == [missing]


def test_a_translation_differing_beyond_engine_identity_diverges(
    tmp_path: Path,
) -> None:
    ref = _engine(tmp_path / "ref")
    other = _engine(tmp_path / "other")
    changed = json.loads(_sarif("agentce-ts"))
    changed["runs"][0]["results"] = [{"ruleId": "DAT-01"}]
    (other / "projects" / PID / "results.sarif").write_bytes(
        json.dumps(changed).encode()
    )
    assert ecs.canonical_divergences(ref, other, PID) == ["results.sarif"]


def test_engines_that_wrote_nothing_are_not_identical(tmp_path: Path) -> None:
    (tmp_path / "ref" / "projects" / PID).mkdir(parents=True)
    (tmp_path / "other" / "projects" / PID).mkdir(parents=True)
    assert ecs.canonical_divergences(tmp_path / "ref", tmp_path / "other", PID) == [
        "assertions.json"
    ]
