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


def test_verify_release_tampered_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An envelope whose keyid is not in the trust root is refused, not a crash (the item's named
    defect: this used to surface as `internal.unexpected`)."""
    trusted_key = Ed25519PrivateKey.generate()
    other_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signing, "vendored_trust", lambda: _trust_for(trusted_key))
    statement = signing.intoto_statement(
        "release",
        "sha256:" + "0" * 64,
        "https://agent-conformance.org/attestation/release/v1",
        {},
    )
    envelope = signing.sign_statement(
        statement, signing.KmsSigner(private_key=other_key)
    )
    artifact = tmp_path / "release.dsse.json"
    artifact.write_text(json.dumps(envelope), encoding="utf-8")
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "reason" in env
    assert "error" not in env


@pytest.mark.parametrize(
    "envelope",
    [
        {
            "payloadType": "application/vnd.in-toto+json",
            "payload": "e30=",
            "signatures": ["x"],
        },
        {"payloadType": "application/vnd.in-toto+json", "payload": 5, "signatures": []},
        {"payloadType": 5, "payload": "e30=", "signatures": []},
    ],
    ids=["signatures-not-objects", "payload-wrong-type", "payloadType-wrong-type"],
)
def test_verify_release_malformed_types_refused(
    envelope: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release envelope with a wrong JSON type for a required field is refused cleanly -- these
    crash with `internal.unexpected` today even under a narrower, single-exception-tuple fix."""
    monkeypatch.setattr(
        signing, "vendored_trust", lambda: _trust_for(Ed25519PrivateKey.generate())
    )
    artifact = tmp_path / "release.dsse.json"
    artifact.write_text(json.dumps(envelope), encoding="utf-8")
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "reason" in env
    assert "error" not in env


def test_verify_release_bundle_manifest_not_json_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "release-manifest.json").write_text("not json", encoding="utf-8")
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert env["reason"] == "release manifest is not readable JSON"


def test_verify_release_bundle_signatures_not_json_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": []}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("not json", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert env["reason"] == "release signatures are not readable JSON"


def test_verify_release_bundle_signatures_entry_not_object_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": []}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text(json.dumps(["x"]), encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "signature (None): signature entry is not an object" in env["reason"]


def test_verify_release_bundle_artifact_missing_name_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": [{"digest": "sha256:" + "0" * 64}]}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "release manifest has an artifact entry with no name" in env["reason"]


def test_verify_release_bundle_unsigned_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bundle with no signature entries at all must not be `verified: true` -- the verifier's
    round-1 Finding 1: no problems and no signers used to mean `verified: true`, an unsigned bundle
    reported as verified."""
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": []}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert env["reason"] == "release bundle carries no signatures"
    assert env["signers"] == []


@pytest.mark.parametrize(
    "artifacts",
    [None, 5, "ab"],
    ids=["artifacts-null", "artifacts-int", "artifacts-str"],
)
def test_verify_release_bundle_artifacts_not_a_list_is_empty(
    artifacts: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-list `artifacts` field is treated as `[]`, not a crash (`TypeError` on `None`/`int`)
    and not an iterable of characters (`str`) -- verifier round-1 Finding 2."""
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": artifacts}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "error" not in env
    assert env["reason"] == "release bundle carries no signatures"


def test_verify_release_bundle_manifest_not_canonicalizable_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A manifest holding a float crashes `canonicalize()` with `CanonicalizationError` today --
    verifier round-1 Finding 3: wrap it in the same soft-fail shape, never `internal.unexpected`."""
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": [], "size": 1.5}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "error" not in env
    assert env["reason"] == "release manifest cannot be canonicalized"


def test_verify_release_envelope_with_bom_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A byte-order mark makes the file unreadable JSON in every engine -- verifier round-1 Finding 4
    (TypeScript's decoder silently stripped it; this pins the cross-engine refusal)."""
    artifact = tmp_path / "release.dsse.json"
    artifact.write_text(
        "﻿" + json.dumps({"payloadType": "x", "payload": "", "signatures": []}),
        encoding="utf-8",
    )
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert env["reason"] == "release envelope is not readable JSON"


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
