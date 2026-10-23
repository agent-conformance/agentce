"""Bundle manifest verification (SPEC §8.1: missing or mismatching manifest aborts with exit 3)."""

from __future__ import annotations

import json
import os
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
    copy_bundle(load_bundle(src), dst)
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
    copy_bundle(load_bundle(src), dst)
    assert not (dst / ".env").exists()
    assert not (dst / "unlisted.txt").exists()
    load_bundle(dst)  # still loads cleanly: only manifest-listed files were copied


def _place_manifest(tmp_path: Path, kind: str) -> Path:
    """A valid bundle at ``tmp_path/b`` whose manifest.json is moved and linked back per ``kind``
    (18.183); returns the folder to load."""
    root = write_bundle(tmp_path / "b", ["{}"])
    manifest = root / "manifest.json"
    out = tmp_path / "out"
    out.mkdir()
    if kind == "plain":
        return root
    if kind == "root_link":
        (tmp_path / "rl").symlink_to(root)
        return tmp_path / "rl"
    if kind == "dangling":
        manifest.unlink()
        manifest.symlink_to(out / "none.json")
        return root
    targets = {
        "outside": ("../out/manifest.json", out / "manifest.json"),
        "inside": ("meta/m.json", root / "meta" / "m.json"),
        "folder_link_out": ("meta/m.json", out / "m.json"),
        "link_to_link_out": ("inner", out / "m.json"),
        "out_and_back": ("../b/m.json", root / "m.json"),
        "sibling_prefix": (
            "../b-evil/manifest.json",
            tmp_path / "b-evil" / "manifest.json",
        ),
    }
    link, target = targets[kind]
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest.rename(target)
    if kind == "folder_link_out":
        (root / "meta").symlink_to(out)
    if kind == "link_to_link_out":
        (root / "inner").symlink_to(target)
    manifest.symlink_to(link)
    return root


@pytest.mark.parametrize(
    "kind",
    ["outside", "folder_link_out", "link_to_link_out", "sibling_prefix"],
)
def test_manifest_linked_outside_the_bundle_is_refused(
    tmp_path: Path, kind: str
) -> None:
    with pytest.raises(InputError) as excinfo:
        load_bundle(_place_manifest(tmp_path, kind))
    assert excinfo.value.key == "input.bundle_manifest_path"


@pytest.mark.parametrize("kind", ["plain", "inside", "root_link", "out_and_back"])
def test_manifest_resolving_inside_the_bundle_loads(tmp_path: Path, kind: str) -> None:
    assert load_bundle(_place_manifest(tmp_path, kind)).manifest["files"]


def test_manifest_dangling_link_is_still_missing(tmp_path: Path) -> None:
    with pytest.raises(InputError) as excinfo:
        load_bundle(_place_manifest(tmp_path, "dangling"))
    assert excinfo.value.key == "input.bundle_manifest_missing"


# --- 18.184f: a member is read once, through the file the confinement checked ---


def _swap_after_confinement(
    monkeypatch: pytest.MonkeyPatch, outside: Path, target: str
) -> list[str]:
    """Patch ``confine_to_root`` so that, the first time it confines ``target``, the member is swapped for a link
    to an outside copy of its own bytes right after the check passes (the matrix cell's seam)."""
    from agentce import bundle

    original = bundle.confine_to_root
    swapped: list[str] = []

    def racing(root: Path, rel: str) -> Path | None:
        found = original(root, rel)
        if found is not None and rel == target and not swapped:
            outside.write_bytes(found.read_bytes())
            found.unlink()
            found.symlink_to(outside)
            swapped.append(rel)
        return found

    monkeypatch.setattr(bundle, "confine_to_root", racing)
    return swapped


@pytest.mark.parametrize("target", ["events/stream.jsonl", "manifest.json"])
def test_member_swapped_after_confinement_is_refused(
    tmp_path: Path,
    example_event: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    root = write_bundle(tmp_path / "b", [json.dumps(example_event)])
    swapped = _swap_after_confinement(monkeypatch, tmp_path / "outside", target)
    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert swapped == [target]
    assert excinfo.value.key == "input.bundle_manifest_path"
    assert f"{target} changed while it was being read" in str(excinfo.value)


def test_member_swapped_after_the_open_is_read_from_the_checked_file(
    tmp_path: Path, example_event: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A swap after the file is open and re-checked changes nothing: the bytes come from the open file."""
    from agentce import bundle

    root = write_bundle(tmp_path / "b", [json.dumps(example_event)])
    member = root / "events/stream.jsonl"
    checked = member.read_bytes()
    outside = tmp_path / "outside"
    outside.write_bytes(b'{"swapped": true}\n')
    original_samestat, calls = bundle.os.path.samestat, []

    def check_then_swap(a: os.stat_result, b: os.stat_result) -> bool:
        result = original_samestat(a, b)
        calls.append(result)
        member.unlink()
        member.symlink_to(outside)
        return result

    monkeypatch.setattr(bundle.os.path, "samestat", check_then_swap)
    assert bundle.read_confined(root, "events/stream.jsonl", member) == checked
    assert calls == [True]


def test_ingest_and_copy_use_the_checked_bytes(
    tmp_path: Path, example_event: dict[str, Any]
) -> None:
    """A member rewritten after load_bundle checked it never reaches ingest or the copy for sharing."""
    from agentce.bundle import copy_bundle
    from agentce.ingest import ingest

    root = write_bundle(tmp_path / "b", [json.dumps(example_event)])
    loaded = load_bundle(root)
    checked = (root / "events/stream.jsonl").read_bytes()
    (root / "events/stream.jsonl").write_bytes(b"not json\n")
    assert len(ingest(loaded).accepted) == 1
    copy_bundle(loaded, tmp_path / "copy")
    assert (tmp_path / "copy/events/stream.jsonl").read_bytes() == checked
    assert load_bundle(tmp_path / "copy").digest == loaded.digest


def test_coverage_denominator_rewritten_after_load_counts_the_checked_bytes(
    tmp_path: Path, example_event: dict[str, Any]
) -> None:
    """A listed denominator manifest rewritten after load_bundle hashed it: coverage reads the hashed counts."""
    import hashlib

    from agentce.coverage import compute_coverage
    from agentce.profile import Profile

    root = write_bundle(tmp_path / "b", [json.dumps(example_event)])
    checked = b'{"counts": {"ToolCall": 1}}'
    (root / "reference").mkdir()
    (root / "reference/counts.json").write_bytes(checked)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"].append(
        {"path": "reference/counts.json", "sha256": hashlib.sha256(checked).hexdigest()}
    )
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    loaded = load_bundle(root)
    (root / "reference/counts.json").write_bytes(b'{"counts": {"ToolCall": 999}}')
    profile = Profile.from_dict(
        {
            "subjects": [
                {
                    "id": "urn:agentce:subject:test",
                    "coverage_denominators": [
                        {
                            "kind": "registry",
                            "source": "urn:src:egress",
                            "covers": ["ToolCall"],
                            "manifest": "reference/counts.json",
                        }
                    ],
                }
            ]
        }
    )
    tool = compute_coverage([], profile, members=loaded.members)["subjects"][
        "urn:agentce:subject:test"
    ]["event_types"]["ToolCall"]
    assert tool["expected"] == 1


def test_manifest_unreadable_at_the_read_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, example_event: dict[str, Any]
) -> None:
    """Census odd-inputs line 90 (18.184r): readable at the pre-check, refused at the read: one key per cause."""
    from agentce import bundle

    root = write_bundle(tmp_path / "b", [json.dumps(example_event)])
    original = bundle.read_confined

    def unreadable_at_read(base: Path, rel: str, member: Path, *more: Any) -> bytes:
        if rel == "manifest.json":
            member.chmod(0)
        return original(base, rel, member, *more)

    monkeypatch.setattr(bundle, "read_confined", unreadable_at_read)
    try:
        with pytest.raises(InputError) as caught:
            load_bundle(root)
    finally:
        (root / "manifest.json").chmod(0o644)
    assert caught.value.key == "input.bundle_unreadable"
    assert "cannot be read: manifest.json." in str(caught.value)
