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


# --- 18.8 C1: copy_bundle (contracts/P18-18.8.md's --package-for-sharing) ---


def test_copy_bundle_reproduces_a_loadable_bundle(
    make_bundle: Callable[..., Path], example_event: dict[str, Any], tmp_path: Path
) -> None:
    from agentce.bundle import copy_bundle

    src = make_bundle([example_event])
    dst = tmp_path / "copy"
    copy_bundle(src, dst)
    copied = load_bundle(dst)
    assert copied.digest == load_bundle(src).digest


def test_copy_bundle_skips_unlisted_files(
    make_bundle: Callable[..., Path], example_event: dict[str, Any], tmp_path: Path
) -> None:
    """A file present on disk but not in the manifest (e.g. a stray ``.env``) must never reach the
    copy: `copy_bundle` walks the manifest's own file list, never the directory tree."""
    from agentce.bundle import copy_bundle

    src = make_bundle([example_event])
    (src / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (src / "unlisted.txt").write_text("not in the manifest\n", encoding="utf-8")
    dst = tmp_path / "copy"
    copy_bundle(src, dst)
    assert not (dst / ".env").exists()
    assert not (dst / "unlisted.txt").exists()
    load_bundle(dst)  # still loads cleanly: only manifest-listed files were copied
