"""Untrusted evidence-bundle input hardening (SPEC §8.1, §8.7).

An evidence bundle's manifest, YAML files, and event lines are adversarial input to the parsing and
file-system layer, independent of what the evidence itself claims about the assessed agent. Every
hazard here must be refused deliberately, under a specific ``input.*`` key or a named quarantine
reason -- never fall through to the generic ``internal.unexpected`` catch-all a genuinely unforeseen
bug uses (``docs/threat-model.md``, "Untrusted evidence-bundle input hardening").
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agentce.bundle import load_bundle
from agentce.domain import DomainBinding
from agentce.errors import InputError
from agentce.ingest import ingest
from agentce.profile import Profile
from agentce.quarantine import QuarantineReason


def _sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(root: Path, files: list[dict[str, Any]]) -> None:
    (root / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")


# --- Path confinement: a manifest-listed path that resolves outside the bundle root -----------


def test_symlink_escaping_the_bundle_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "events").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the bundle root\n", encoding="utf-8")
    link = root / "events" / "evil.jsonl"
    link.symlink_to(outside)
    _write_manifest(
        root, [{"path": "events/evil.jsonl", "sha256": _sha256_hex(outside)}]
    )

    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert excinfo.value.key == "input.bundle_manifest_path"


def test_literal_dotdot_path_still_refused(tmp_path: Path) -> None:
    """Regression guard: the pre-existing literal-``..`` refusal keeps working."""
    root = tmp_path / "bundle"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x\n", encoding="utf-8")
    _write_manifest(root, [{"path": "../outside.txt", "sha256": _sha256_hex(outside)}])

    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert excinfo.value.key == "input.bundle_manifest_path"


def test_symlink_resolving_inside_bundle_root_is_accepted(tmp_path: Path) -> None:
    """Positive control: confinement checks the *resolved* target, not "is it a symlink" --

    a symlink that stays inside the bundle root is ordinary and must still load.
    """
    root = tmp_path / "bundle"
    (root / "events").mkdir(parents=True)
    real = root / "events" / "real.jsonl"
    real.write_text("{}\n", encoding="utf-8")
    link = root / "events" / "alias.jsonl"
    link.symlink_to(real)
    _write_manifest(root, [{"path": "events/alias.jsonl", "sha256": _sha256_hex(real)}])

    bundle = load_bundle(root)
    assert bundle.event_files == (link,)


# --- Per-manifest-file size ceiling, checked before the file is opened or hashed ----------------


def test_manifest_file_over_the_size_ceiling_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agentce.bundle.DEFAULT_MAX_MANIFEST_FILE_BYTES", 8)
    root = tmp_path / "bundle"
    (root / "events").mkdir(parents=True)
    big = root / "events" / "big.jsonl"
    big.write_text(
        "0123456789\n", encoding="utf-8"
    )  # 11 bytes, over the 8-byte ceiling
    _write_manifest(root, [{"path": "events/big.jsonl", "sha256": _sha256_hex(big)}])

    with pytest.raises(InputError) as excinfo:
        load_bundle(root)
    assert excinfo.value.key == "input.bundle_manifest_file_too_large"


def test_manifest_file_under_the_size_ceiling_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agentce.bundle.DEFAULT_MAX_MANIFEST_FILE_BYTES", 8)
    root = tmp_path / "bundle"
    (root / "events").mkdir(parents=True)
    small = root / "events" / "small.jsonl"
    small.write_text("{}\n", encoding="utf-8")  # 3 bytes, under the 8-byte ceiling
    _write_manifest(
        root, [{"path": "events/small.jsonl", "sha256": _sha256_hex(small)}]
    )

    bundle = load_bundle(root)
    assert bundle.event_files == (small,)


# --- YAML hazards: a disallowed tag, or a pathologically deep structure -------------------------


def test_profile_with_disallowed_yaml_tag_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    path.write_text('!!python/object/apply:os.system ["id"]\n', encoding="utf-8")

    with pytest.raises(InputError) as excinfo:
        Profile.load(path)
    assert excinfo.value.key == "input.profile_invalid"


def test_profile_with_pathologically_deep_yaml_is_refused(tmp_path: Path) -> None:
    depth = 2000
    path = tmp_path / "profile.yaml"
    path.write_text("[" * depth + "1" + "]" * depth, encoding="utf-8")

    with pytest.raises(InputError) as excinfo:
        Profile.load(path)
    assert excinfo.value.key == "input.profile_invalid"


def test_domain_binding_with_disallowed_yaml_tag_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "domain.yaml"
    path.write_text('!!python/object/apply:os.system ["id"]\n', encoding="utf-8")

    with pytest.raises(InputError) as excinfo:
        DomainBinding.load(path)
    assert excinfo.value.key == "input.domain_binding_invalid"


def test_domain_binding_with_pathologically_deep_yaml_is_refused(
    tmp_path: Path,
) -> None:
    depth = 2000
    path = tmp_path / "domain.yaml"
    path.write_text("[" * depth + "1" + "]" * depth, encoding="utf-8")

    with pytest.raises(InputError) as excinfo:
        DomainBinding.load(path)
    assert excinfo.value.key == "input.domain_binding_invalid"


# --- JSON structural-depth hazard in a single evidence-event line -------------------------------


def test_event_line_with_pathologically_deep_json_is_refused(
    make_bundle: Callable[..., Path],
) -> None:
    depth = 20000
    deep_line = "[" * depth + "1" + "]" * depth
    bundle = load_bundle(make_bundle([deep_line]))

    with pytest.raises(InputError) as excinfo:
        ingest(bundle)
    assert excinfo.value.key == "input.event_structure_too_deep"


def test_oversize_event_line_still_quarantined(
    make_bundle: Callable[..., Path],
) -> None:
    """Regression guard: the pre-existing per-event byte-size cap keeps working."""
    huge = json.dumps({"pad": "x" * 1_100_000})
    bundle = load_bundle(make_bundle([huge]))

    result = ingest(bundle)
    assert [q.reason for q in result.quarantined] == [QuarantineReason.OVERSIZE]


# --- A small deterministic fuzz corpus: many structural variants of each hazard class -----------
#
# Offline and reproducible (a fixed seed, no external fuzzing dependency): each run explores a wider,
# regenerable set of depths, path shapes, and symlink chains than one hand-picked fixture, per the
# bootstrap -> fix-phase graduation the contract for this hardening work calls for.


def _deep_flow_sequence(depth: int) -> str:
    return "[" * depth + "1" + "]" * depth


def test_fuzz_yaml_depth_variants_are_all_refused_deliberately(tmp_path: Path) -> None:
    rng = random.Random(1337)
    for i in range(25):
        depth = rng.randint(600, 6000)
        path = tmp_path / f"profile-{i}.yaml"
        path.write_text(_deep_flow_sequence(depth), encoding="utf-8")
        with pytest.raises(InputError) as excinfo:
            Profile.load(path)
        assert excinfo.value.key == "input.profile_invalid"


def test_fuzz_json_depth_variants_are_all_refused_deliberately(
    make_bundle: Callable[..., Path],
) -> None:
    rng = random.Random(1337)
    for i in range(10):
        depth = rng.randint(5000, 30000)
        bundle = load_bundle(make_bundle([_deep_flow_sequence(depth)]))
        with pytest.raises(InputError) as excinfo:
            ingest(bundle)
        assert excinfo.value.key == "input.event_structure_too_deep"


def test_fuzz_symlink_chain_variants_all_escape_refused(tmp_path: Path) -> None:
    rng = random.Random(1337)
    for i in range(15):
        root = tmp_path / f"bundle-{i}"
        (root / "events").mkdir(parents=True)
        outside = tmp_path / f"outside-{i}.txt"
        outside.write_text(f"secret {i}\n", encoding="utf-8")

        # A chain of 1-4 intermediate symlinks before the final escape, and a randomised nested
        # relative path for the manifest-listed member -- the string itself stays clean (no '..'),
        # only the resolved target escapes.
        chain_len = rng.randint(1, 4)
        target = outside
        for hop in range(chain_len):
            hop_link = tmp_path / f"hop-{i}-{hop}"
            hop_link.symlink_to(target)
            target = hop_link
        nested = root / "events" / f"nested-{i}"
        nested.mkdir(parents=True)
        member = nested / "evil.jsonl"
        member.symlink_to(target)
        rel = f"events/nested-{i}/evil.jsonl"
        _write_manifest(root, [{"path": rel, "sha256": _sha256_hex(outside)}])

        with pytest.raises(InputError) as excinfo:
            load_bundle(root)
        assert excinfo.value.key == "input.bundle_manifest_path"
