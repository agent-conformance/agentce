"""Release dry-run tooling (SPEC §8.7, P5.2): reproducible build, SBOM, OpenVEX, signing, no publish.

These tests are hermetic (no git push, no network) and assert the determinism the reproducible-build
and offline-verification claims rest on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import dev_trust
import release
from agentce import signing


def test_run_dry_run_all_true() -> None:
    result = release.run(
        dry_run=True, profiles=list(release.ALL_PROFILES), offline=True, out_dir=None
    )
    assert result["reproducible"] is True
    assert result["sbom_valid"] is True
    assert result["vex_valid"] is True
    assert result["signatures_verify"] is True
    assert result["no_publish"] is True
    assert {p["profile"] for p in result["signed_profiles"]} == set(
        release.ALL_PROFILES
    )


def test_reproducible_build_is_byte_identical() -> None:
    first = release.build_release_sources()
    second = release.build_release_sources()
    assert release.source_release_digest(first) == release.source_release_digest(second)
    # The tar bytes themselves are identical, not merely the digest.
    for a, b in zip(first, second, strict=True):
        assert a["bytes"] == b["bytes"]


def test_non_dry_run_is_refused() -> None:
    with pytest.raises(release.ReleaseError):
        release.run(dry_run=False, profiles=["kms"], offline=True, out_dir=None)


def test_sbom_validates_and_lists_locked_python_deps() -> None:
    artifacts = release.build_release_sources()
    sbom = release.generate_sbom(artifacts, "2026-01-01T00:00:00.000Z")
    release.validate_sbom(sbom)  # raises on failure
    libraries = {
        (c["name"], c["version"]) for c in sbom["components"] if c["type"] == "library"
    }
    for dep in release._python_dependencies():
        assert (dep["name"], dep["version"]) in libraries
    # cryptography is a real dependency and must be inventoried.
    assert any(name == "cryptography" for name, _ in libraries)


def test_sbom_consistency_catches_a_missing_dependency() -> None:
    artifacts = release.build_release_sources()
    sbom = release.generate_sbom(artifacts, "2026-01-01T00:00:00.000Z")
    sbom["components"] = [c for c in sbom["components"] if c["name"] != "cryptography"]
    with pytest.raises(release.ReleaseError):
        release.validate_sbom(sbom)


def test_vex_validates() -> None:
    vex = release.generate_vex("sha256:" + "0" * 64, "2026-01-01T00:00:00.000Z")
    release.validate_vex(vex)


def test_stub_registry_never_publishes() -> None:
    registry = release.StubRegistry()
    registry.plan("sha256:deadbeef")
    assert registry.published == []
    with pytest.raises(release.ReleaseError):
        registry.publish("sha256:deadbeef")


def test_bundle_round_trips_through_vendored_trust(tmp_path: Path) -> None:
    release.run(
        dry_run=True,
        profiles=list(release.ALL_PROFILES),
        offline=True,
        out_dir=tmp_path,
    )
    manifest = json.loads((tmp_path / "release-manifest.json").read_text("utf-8"))
    from agentce.canonical import canonicalize

    manifest_digest = signing.sha256_prefixed(canonicalize(manifest))
    trust = signing.vendored_trust()
    signatures = json.loads((tmp_path / "signatures.json").read_text("utf-8"))
    assert {s["profile"] for s in signatures} == set(release.ALL_PROFILES)
    for entry in signatures:
        verified = signing.verify_envelope(entry["envelope"], trust)
        covered = json.loads(verified.payload)["subject"][0]["digest"]["sha256"]
        assert f"sha256:{covered}" == manifest_digest
    # Every artifact digest in the manifest matches the file on disk.
    for artifact in manifest["artifacts"]:
        data = (tmp_path / artifact["name"]).read_bytes()
        assert signing.sha256_prefixed(data) == artifact["digest"]


def test_manifest_and_kms_signature_are_clock_independent() -> None:
    a = release.run(dry_run=True, profiles=["kms"], offline=True, out_dir=None)
    b = release.run(dry_run=True, profiles=["kms"], offline=True, out_dir=None)
    assert a["manifest_digest"] == b["manifest_digest"]
    assert a["release_digest"] == b["release_digest"]


def test_vendored_root_matches_derivation() -> None:
    """The engine's vendored trust root never drifts from the seed it is derived from."""
    on_disk = json.loads(dev_trust.VENDORED_ROOT.read_text("utf-8"))
    assert on_disk == dev_trust.public_root()


def test_committed_eu_ai_act_signature_verifies() -> None:
    catalog = release.REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
    envelope = json.loads((catalog / signing.CATALOG_SIGNATURE_NAME).read_text("utf-8"))
    verified = signing.verify_envelope(envelope, signing.vendored_trust())
    recomputed = signing.digest_tree(
        catalog, exclude=frozenset({signing.CATALOG_SIGNATURE_NAME})
    )
    signed = "sha256:" + json.loads(verified.payload)["subject"][0]["digest"]["sha256"]
    assert signed == recomputed


def test_cli_dry_run_prints_sentinels(capsys: pytest.CaptureFixture[str]) -> None:
    assert release.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "RELEASE DRY-RUN OK" in out
    assert "SIGNING PROFILES OK" not in out


def test_cli_profiles_prints_signing_sentinel(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = release.main(
        ["--dry-run", "--profiles", "sigstore-public,sigstore-private,kms", "--offline"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "SIGNING PROFILES OK" in out


def test_cli_without_dry_run_refuses() -> None:
    assert release.main([]) == 2


def test_release_dryrun_gate_json(capsys: pytest.CaptureFixture[str]) -> None:
    import release_dryrun

    assert release_dryrun.main(["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reproducible"] and out["sbom_valid"]
    assert out["signatures_verify"] and out["no_publish"]
