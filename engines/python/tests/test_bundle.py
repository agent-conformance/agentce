"""Bundle manifest verification (SPEC §8.1: missing or mismatching manifest aborts with exit 3)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import write_bundle

from agentce.bundle import load_bundle
from agentce.errors import InputError


def test_load_valid_bundle(
    make_bundle: Callable[..., Path], example_event: dict[str, Any]
) -> None:
    bundle = load_bundle(make_bundle([example_event]))
    assert len(bundle.event_files) == 1
    assert bundle.digest.startswith("sha256:")


def test_missing_manifest(tmp_path: Path) -> None:
    root = write_bundle(tmp_path / "b", ["{}"], write_manifest=False)
    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert excinfo.value.key == "input.bundle_manifest_missing"


def test_hash_mismatch(tmp_path: Path, example_event: dict[str, Any]) -> None:
    root = write_bundle(tmp_path / "b", [json.dumps(example_event)])
    (root / "events" / "stream.jsonl").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert excinfo.value.key == "input.bundle_manifest_mismatch"


def test_listed_file_missing(tmp_path: Path, example_event: dict[str, Any]) -> None:
    root = write_bundle(tmp_path / "b", [json.dumps(example_event)])
    (root / "events" / "stream.jsonl").unlink()
    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert excinfo.value.key == "input.bundle_manifest_mismatch"


def test_invalid_manifest_json(tmp_path: Path) -> None:
    root = tmp_path / "b"
    root.mkdir()
    (root / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert excinfo.value.key == "input.bundle_manifest_invalid"


def test_no_files_array(tmp_path: Path) -> None:
    root = tmp_path / "b"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps({"agentce_bundle_version": 1}), encoding="utf-8"
    )
    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert excinfo.value.key == "input.bundle_manifest_files"


def test_unsafe_path(tmp_path: Path) -> None:
    root = tmp_path / "b"
    root.mkdir()
    manifest = {"files": [{"path": "../evil", "sha256": "0" * 64}]}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert excinfo.value.key == "input.bundle_manifest_path"


def test_declared_sources_loaded(
    make_bundle: Callable[..., Path], example_event: dict[str, Any]
) -> None:
    bundle = load_bundle(make_bundle([example_event], sources=["urn:agentce:source:a"]))
    assert bundle.sources == frozenset({"urn:agentce:source:a"})
