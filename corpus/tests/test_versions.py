"""VERSIONS.md pin and dry-run publication (SPEC §11.7).

The full-corpus integration (a freshly generated v1 matching the committed pin) is exercised by the
item's acceptance, ``publish.py --dry-run``; these unit tests cover the pin parsing and the
digest comparison with small fixed manifests, so they stay fast.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus import check_versions, publish
from corpus.check_versions import VERSIONS_MD, main as check_versions_main
from corpus.check_versions import pinned_digest, verify_versions
from corpus.generator.generate import AUTHORED_FILES, authored_digests, corpus_digest

PINNED = pinned_digest(VERSIONS_MD)
PROJECT_ID = "credit/x/known-pass"


def _fake_corpus(root: Path) -> tuple[Path, str]:
    """A small consistent corpus on disk: one project with its authored files and a manifest whose
    per-project digests and overall digest are the ones the generator would record. Returns the
    corpus directory and that overall digest."""
    proj_dir = root / "projects" / PROJECT_ID
    for rel in AUTHORED_FILES.values():
        path = proj_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"authored {rel}\n", encoding="utf-8")
    project = {
        "id": PROJECT_ID,
        "bundle_digest": "sha256:" + "1" * 64,
        "events": 1,
        **authored_digests(proj_dir),
    }
    digest = corpus_digest([project])
    (root / "corpus-manifest.json").write_text(
        json.dumps({"digest": digest, "projects": [project]}), encoding="utf-8"
    )
    return root, digest


def _pinning(tmp_path: Path, digest: str) -> Path:
    versions = tmp_path / "VERSIONS.md"
    versions.write_text(f"| Manifest SHA-256 | `{digest}` |\n", encoding="utf-8")
    return versions


@pytest.fixture
def pinned_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, str]:
    """A small consistent corpus, the version pin naming its digest, and that digest; the module's
    default pin is redirected to it for the command-line entry points."""
    corpus_dir, digest = _fake_corpus(tmp_path / "c")
    versions = _pinning(tmp_path, digest)
    real = check_versions.pinned_digest
    monkeypatch.setattr(
        check_versions, "pinned_digest", lambda path=versions: real(versions)
    )
    return corpus_dir, versions, digest


def test_committed_versions_pins_one_valid_digest() -> None:
    assert PINNED.startswith("sha256:")
    assert len(PINNED) == len("sha256:") + 64


def test_pinned_digest_rejects_multiple(tmp_path: Path) -> None:
    versions = tmp_path / "V.md"
    versions.write_text(f"`{PINNED}` and also `{PINNED}`", encoding="utf-8")
    with pytest.raises(ValueError):
        pinned_digest(versions)


def test_pinned_digest_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        pinned_digest(tmp_path / "nope.md")


def test_verify_versions_matches(pinned_fake: tuple[Path, Path, str]) -> None:
    corpus_dir, versions, _ = pinned_fake
    ok, message = verify_versions(corpus_dir, versions)
    assert ok, message


def test_verify_versions_detects_a_digest_that_is_not_the_pin(tmp_path: Path) -> None:
    corpus_dir, _ = _fake_corpus(tmp_path / "c")
    ok, message = verify_versions(corpus_dir, _pinning(tmp_path, "sha256:" + "1" * 64))
    assert not ok
    assert "does not match" in message


@pytest.mark.parametrize("key", sorted(AUTHORED_FILES))
def test_verify_versions_detects_drift_in_each_authored_file(
    tmp_path: Path, key: str
) -> None:
    corpus_dir, digest = _fake_corpus(tmp_path / "c")
    versions = _pinning(tmp_path, digest)
    target = corpus_dir / "projects" / PROJECT_ID / AUTHORED_FILES[key]
    target.write_text("drifted\n", encoding="utf-8")
    ok, message = verify_versions(corpus_dir, versions)
    assert not ok
    assert AUTHORED_FILES[key] in message


def test_verify_versions_detects_a_missing_authored_file(tmp_path: Path) -> None:
    corpus_dir, digest = _fake_corpus(tmp_path / "c")
    (corpus_dir / "projects" / PROJECT_ID / "applicability.yaml").unlink()
    ok, message = verify_versions(corpus_dir, _pinning(tmp_path, digest))
    assert not ok
    assert "cannot be read" in message


def test_verify_versions_refuses_a_manifest_that_pins_no_authored_files(
    tmp_path: Path,
) -> None:
    corpus_dir, digest = _fake_corpus(tmp_path / "c")
    manifest_path = corpus_dir / "corpus-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in AUTHORED_FILES:
        del manifest["projects"][0][key]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ok, message = verify_versions(corpus_dir, _pinning(tmp_path, digest))
    assert not ok
    assert "does not pin" in message


def test_verify_versions_refuses_a_digest_that_is_not_of_its_own_projects(
    tmp_path: Path,
) -> None:
    corpus_dir, _ = _fake_corpus(tmp_path / "c")
    forged = "sha256:" + "2" * 64
    manifest_path = corpus_dir / "corpus-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["digest"] = forged
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ok, message = verify_versions(corpus_dir, _pinning(tmp_path, forged))
    assert not ok
    assert "not the digest of its own projects" in message


def test_check_versions_main_ok(
    pinned_fake: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    corpus_dir, _, _ = pinned_fake
    rc = check_versions_main(["--corpus", str(corpus_dir)])
    assert rc == 0
    assert "VERSIONS OK" in capsys.readouterr().out


def test_check_versions_main_drift_exits_one(
    pinned_fake: tuple[Path, Path, str],
) -> None:
    corpus_dir, _, _ = pinned_fake
    target = corpus_dir / "projects" / PROJECT_ID / "expected" / "outcomes.json"
    target.write_text("drifted\n", encoding="utf-8")
    assert check_versions_main(["--corpus", str(corpus_dir)]) == 1


def test_publish_dry_run_ok(
    pinned_fake: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    corpus_dir, _, _ = pinned_fake
    rc = publish.main(["--dry-run", "--corpus", str(corpus_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN OK" in out
    assert "agent-conformance/corpus" in out


def test_publish_refuses_without_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert publish.main([]) == 3
