"""Generator tests: determinism, structure, and the realism/volume requirements (SPEC §11.2–11.4).

These prove the generator's own contract without the engine: the same set produces byte-identical
output, every project carries the inputs and authored ground truth the layout requires, every event is
a schema-shaped envelope, and the largest project meets the ≥ 200k-event floor. The engine-outcome
proof (that the seeded faults are detected and no known-pass control fires) lives in the corpus
package tests, which run the reference engine.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

import generate

#: The module's configured filler size, captured before any fixture shrinks it for speed.
_DEFAULT_FILLER = generate.FILLER_EVENTS

_ENVELOPE_KEYS = {
    "specversion",
    "id",
    "source",
    "type",
    "time",
    "subject",
    "datacontenttype",
    "agentcesourceclass",
    "data",
}
_OUTCOMES = {
    "conformant",
    "non-conformant",
    "not_applicable",
    "not_assessed",
    "insufficient_evidence",
}


@pytest.fixture(autouse=True)
def _small_filler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the high-volume project so the structural tests stay fast; the volume floor is proven
    against the real filler in its own test."""
    monkeypatch.setattr(generate, "FILLER_EVENTS", 60)


def _build(tmp_path: Path, name: str = "c") -> tuple[Path, dict]:
    out = tmp_path / name
    manifest = generate.build_corpus(out, "v1")
    return out, manifest


def _iter_event_lines(bundle: Path) -> Iterator[str]:
    for events_file in sorted((bundle / "events").glob("*.jsonl")):
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield line


def test_generates_thirty_projects(tmp_path: Path) -> None:
    _out, manifest = _build(tmp_path)
    assert len(manifest["projects"]) == 30
    ids = [p["id"] for p in manifest["projects"]]
    assert len(set(ids)) == 30
    assert ids == sorted(ids)  # deterministic ordering
    assert all(p["id"].startswith("credit/") for p in manifest["projects"])


def test_manifest_projects_have_event_counts(tmp_path: Path) -> None:
    _out, manifest = _build(tmp_path)
    for project in manifest["projects"]:
        assert isinstance(project["events"], int)
        assert project["events"] > 0


def test_deterministic_bytes(tmp_path: Path) -> None:
    _a, manifest_a = _build(tmp_path, "a")
    _b, manifest_b = _build(tmp_path, "b")
    assert manifest_a["digest"] == manifest_b["digest"]

    def tree_hash(root: Path) -> str:
        h = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if path.is_file():
                h.update(path.relative_to(root).as_posix().encode())
                h.update(path.read_bytes())
        return h.hexdigest()

    assert tree_hash(tmp_path / "a") == tree_hash(tmp_path / "b")


def test_project_layout_is_complete(tmp_path: Path) -> None:
    out, manifest = _build(tmp_path)
    for project in manifest["projects"]:
        proj = out / "projects" / project["id"]
        assert (proj / "README.md").is_file()
        assert (proj / "applicability.yaml").is_file()
        assert (proj / "domain.linkml.yaml").is_file()
        assert (proj / "deviations.yaml").is_file()
        assert (proj / "evidence" / "manifest.json").is_file()
        assert (proj / "expected" / "outcomes.json").is_file()


def test_events_are_shaped_envelopes(tmp_path: Path) -> None:
    out, manifest = _build(tmp_path)
    for project in manifest["projects"][
        :3
    ]:  # a sample; every project is built the same way
        bundle = out / "projects" / project["id"] / "evidence"
        for line in _iter_event_lines(bundle):
            event = json.loads(line)
            assert _ENVELOPE_KEYS <= set(event)
            assert event["specversion"] == "1.0"
            assert event["type"].startswith("org.agent-conformance.evidence.")
            assert isinstance(event["data"], dict) and "@type" in event["data"]


def test_bundle_manifest_hashes_match_files(tmp_path: Path) -> None:
    out, manifest = _build(tmp_path)
    proj = out / "projects" / manifest["projects"][0]["id"] / "evidence"
    bundle_manifest = json.loads((proj / "manifest.json").read_text())
    for entry in bundle_manifest["files"]:
        actual = hashlib.sha256((proj / entry["path"]).read_bytes()).hexdigest()
        assert actual == entry["sha256"]


def test_expected_outcomes_are_valid(tmp_path: Path) -> None:
    out, manifest = _build(tmp_path)
    for project in manifest["projects"]:
        payload = json.loads(
            (
                out / "projects" / project["id"] / "expected" / "outcomes.json"
            ).read_text()
        )
        controls = {o["control"] for o in payload["outcomes"]}
        assert controls == {"REC-04", "OVS-03", "INT-01", "INC-02"}
        assert all(o["outcome"] in _OUTCOMES for o in payload["outcomes"])


def test_known_pass_has_no_seeded_faults(tmp_path: Path) -> None:
    out, manifest = _build(tmp_path)
    for project in manifest["projects"]:
        payload = json.loads(
            (
                out / "projects" / project["id"] / "expected" / "outcomes.json"
            ).read_text()
        )
        if project["variant"] == "known-pass":
            assert payload["seeded_faults"] == []
            assert all(o["outcome"] == "conformant" for o in payload["outcomes"])
        if project["variant"] == "known-fail":
            assert all(o["outcome"] == "non-conformant" for o in payload["outcomes"])


def test_unknown_set_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        generate.build_corpus(tmp_path / "x", "v2")


def test_volume_floor_met() -> None:
    # The realism requirement: the largest project carries ≥ 200k events (SPEC §11.4).
    assert _DEFAULT_FILLER >= 200_000


def test_largest_project_reports_the_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate, "FILLER_EVENTS", 200_000)
    _out, manifest = _build(tmp_path)
    largest = max(manifest["projects"], key=lambda p: p["events"])
    assert largest["events"] >= 200_000
