"""The three ``verify`` targets: --bundle, --catalog, and --release (SPEC §8.5, §8.7).

Catalog and release verification run offline against a trust root. These tests build their own
ephemeral trust root and signed material and monkeypatch :func:`agentce.signing.vendored_trust`, so
they exercise the real verification path without depending on the repository's committed keys.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentce import cli, signing


def run(
    argv: Sequence[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _trust_for(key: Ed25519PrivateKey) -> signing.TrustRoot:
    keyid = signing.keyid_for(key.public_key())
    return signing.TrustRoot(
        keys={keyid: key.public_key()},
        key_identities={keyid: "test-signer"},
    )


def _sign_catalog(catalog_dir: Path, key: Ed25519PrivateKey) -> None:
    digest = signing.digest_tree(
        catalog_dir, exclude=frozenset({signing.CATALOG_SIGNATURE_NAME})
    )
    statement = signing.intoto_statement(
        catalog_dir.name,
        digest,
        "https://agent-conformance.org/attestation/catalog/v1",
        {},
    )
    envelope = signing.sign_statement(statement, signing.KmsSigner(private_key=key))
    (catalog_dir / signing.CATALOG_SIGNATURE_NAME).write_text(
        json.dumps(envelope), encoding="utf-8"
    )


def test_verify_catalog_signed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signing, "vendored_trust", lambda: _trust_for(key))
    (tmp_path / "catalog.yaml").write_text("id: demo\n", encoding="utf-8")
    _sign_catalog(tmp_path, key)
    code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    assert code == 0
    assert env["verified"] is True
    assert env["signer"] == "test-signer"
    assert env["digest"].startswith("sha256:")


def test_verify_catalog_unsigned_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "catalog.yaml").write_text("id: demo\n", encoding="utf-8")
    code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False


def test_verify_catalog_tampered_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signing, "vendored_trust", lambda: _trust_for(key))
    (tmp_path / "catalog.yaml").write_text("id: demo\n", encoding="utf-8")
    _sign_catalog(tmp_path, key)
    (tmp_path / "catalog.yaml").write_text("id: tampered\n", encoding="utf-8")
    code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False


def test_verify_release_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signing, "vendored_trust", lambda: _trust_for(key))
    statement = signing.intoto_statement(
        "release",
        "sha256:" + "0" * 64,
        "https://agent-conformance.org/attestation/release/v1",
        {},
    )
    envelope = signing.sign_statement(statement, signing.KmsSigner(private_key=key))
    artifact = tmp_path / "release.dsse.json"
    artifact.write_text(json.dumps(envelope), encoding="utf-8")
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 0
    assert env["verified"] is True
    assert env["signer"] == "test-signer"


def test_verify_release_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(
        ["verify", "--release", str(tmp_path / "nope.tgz"), "--json"], capsys
    )
    assert code == 3
    assert env["error"]["key"] == "input.release_missing"


def test_verify_two_targets_is_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(
        ["verify", "--bundle", str(tmp_path), "--catalog", str(tmp_path), "--json"],
        capsys,
    )
    assert code == 3
    assert env["error"]["key"] == "input.verify_target"


def test_report_default_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "assertions.json"
    src.write_text("{}", encoding="utf-8")
    code, env = run(["report", "--from", str(src), "--json"], capsys)
    assert code == 0
    assert env["format"] == "md"
