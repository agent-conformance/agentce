"""VERSIONS.md pin and dry-run publication (SPEC §11.7).

The full-corpus integration (a freshly generated v1 matching the committed pin) is exercised by the
item's acceptance, ``publish.py --dry-run``; these unit tests cover the pin parsing and the
digest comparison with small fixed manifests, so they stay fast.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus import publish
from corpus.check_versions import VERSIONS_MD, main as check_versions_main
from corpus.check_versions import pinned_digest, verify_versions

PINNED = pinned_digest(VERSIONS_MD)


def _fake_corpus(root: Path, digest: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "corpus-manifest.json").write_text(
        json.dumps(
            {"digest": digest, "projects": [{"id": "credit/x/known-pass", "events": 1}]}
        ),
        encoding="utf-8",
    )
    return root


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


def test_verify_versions_matches(tmp_path: Path) -> None:
    ok, message = verify_versions(_fake_corpus(tmp_path / "c", PINNED))
    assert ok, message


def test_verify_versions_detects_drift(tmp_path: Path) -> None:
    ok, message = verify_versions(_fake_corpus(tmp_path / "c", "sha256:" + "0" * 64))
    assert not ok
    assert "does not match" in message


def test_check_versions_main_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = check_versions_main(["--corpus", str(_fake_corpus(tmp_path / "c", PINNED))])
    assert rc == 0
    assert "VERSIONS OK" in capsys.readouterr().out


def test_check_versions_main_drift_exits_one(tmp_path: Path) -> None:
    rc = check_versions_main(
        ["--corpus", str(_fake_corpus(tmp_path / "c", "sha256:" + "1" * 64))]
    )
    assert rc == 1


def test_publish_dry_run_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = publish.main(
        ["--dry-run", "--corpus", str(_fake_corpus(tmp_path / "c", PINNED))]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN OK" in out
    assert "agent-conformance/corpus" in out


def test_publish_refuses_without_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert publish.main([]) == 3
