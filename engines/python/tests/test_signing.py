"""DSSE signing and offline verification (SPEC §8.7, §9.1).

Ed25519 signatures are deterministic (RFC 8032), so the module's output is exercised against a fixed
golden as well as roundtrip, tamper, and order/clock-independence checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentce import signing

# A fixed key (all-zero seed) and its deterministic signature over a fixed statement.
_ZERO_KEY = Ed25519PrivateKey.from_private_bytes(bytes(32))
_ZERO_KEYID = "sha256:139e3940e64b5491722088d9a0d741628fc826e09475d341a780acde3c4b8070"
_ZERO_SIG = "Go7hgoO4BGVxzPThwcdJZOxMy4yn8uAuE/8Ifasb0rKi7q0fYtkVfWFm6o9lwMiArbZCxqhlohJXmLM7+gT4BQ=="


def _statement() -> dict:
    return signing.intoto_statement(
        "demo", "sha256:" + "ab" * 32, "https://example/pred", {"k": "v"}
    )


def test_pae_is_the_dsse_encoding() -> None:
    assert signing._pae("t", b"body") == b"DSSEv1 1 t 4 body"


def test_sign_statement_is_deterministic_golden() -> None:
    env = signing.sign_statement(_statement(), signing.KmsSigner(private_key=_ZERO_KEY))
    assert env["payloadType"] == signing.INTOTO_PAYLOAD_TYPE
    assert env["signatures"][0]["keyid"] == _ZERO_KEYID
    assert env["signatures"][0]["sig"] == _ZERO_SIG
    # Signing again yields byte-identical output (clock/order independent).
    again = signing.sign_statement(
        _statement(), signing.KmsSigner(private_key=_ZERO_KEY)
    )
    assert again == env


def test_kms_roundtrip() -> None:
    key = Ed25519PrivateKey.generate()
    trust = signing.TrustRoot(
        keys={signing.keyid_for(key.public_key()): key.public_key()},
        key_identities={signing.keyid_for(key.public_key()): "op"},
    )
    env = signing.sign_statement(_statement(), signing.KmsSigner(private_key=key))
    verified = signing.verify_envelope(env, trust)
    assert verified.identity == "op"
    assert verified.keyless is False
    assert json.loads(verified.payload)["subject"][0]["name"] == "demo"


def test_tampered_payload_fails() -> None:
    key = Ed25519PrivateKey.generate()
    trust = signing.TrustRoot(
        keys={signing.keyid_for(key.public_key()): key.public_key()}
    )
    env = signing.sign_statement(_statement(), signing.KmsSigner(private_key=key))
    env["payload"] = signing._b64e(b'{"_type":"tampered"}')
    with pytest.raises(signing.VerificationError):
        signing.verify_envelope(env, trust)


def test_wrong_key_is_untrusted() -> None:
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    trust = signing.TrustRoot(
        keys={signing.keyid_for(other.public_key()): other.public_key()}
    )
    env = signing.sign_statement(_statement(), signing.KmsSigner(private_key=key))
    with pytest.raises(signing.VerificationError):
        signing.verify_envelope(env, trust)


def test_keyless_certificate_roundtrip() -> None:
    ca = Ed25519PrivateKey.generate()
    leaf = Ed25519PrivateKey.generate()
    cert = signing.issue_certificate(
        ca,
        issuer="dev-ca",
        identity="ci@agent-conformance.org",
        leaf_public=leaf.public_key(),
        not_before="2026-01-01T00:00:00.000Z",
        not_after="2027-01-01T00:00:00.000Z",
    )
    trust = signing.TrustRoot(authorities={"dev-ca": ca.public_key()})
    env = signing.sign_statement(
        _statement(), signing.KeylessSigner(private_key=leaf, cert=cert)
    )
    verified = signing.verify_envelope(env, trust)
    assert verified.identity == "ci@agent-conformance.org"
    assert verified.keyless is True


def test_keyless_certificate_forged_ca_fails() -> None:
    real_ca = Ed25519PrivateKey.generate()
    forger = Ed25519PrivateKey.generate()
    leaf = Ed25519PrivateKey.generate()
    cert = signing.issue_certificate(
        forger,
        issuer="dev-ca",
        identity="ci@agent-conformance.org",
        leaf_public=leaf.public_key(),
        not_before="2026-01-01T00:00:00.000Z",
        not_after="2027-01-01T00:00:00.000Z",
    )
    trust = signing.TrustRoot(authorities={"dev-ca": real_ca.public_key()})
    env = signing.sign_statement(
        _statement(), signing.KeylessSigner(private_key=leaf, cert=cert)
    )
    with pytest.raises(signing.VerificationError):
        signing.verify_envelope(env, trust)


def test_digest_tree_is_order_independent_and_excludes(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("two", encoding="utf-8")
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("three", encoding="utf-8")
    first = signing.digest_tree(tmp_path)
    # A dotfile, a __pycache__ entry, and an excluded name never change the digest.
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_text("x", encoding="utf-8")
    (tmp_path / "catalog.sig.json").write_text("x", encoding="utf-8")
    second = signing.digest_tree(tmp_path, exclude=frozenset({"catalog.sig.json"}))
    assert first == second
    # Content changes the digest.
    (sub / "c.txt").write_text("changed", encoding="utf-8")
    assert (
        signing.digest_tree(tmp_path, exclude=frozenset({"catalog.sig.json"})) != first
    )


def test_vendored_trust_loads() -> None:
    trust = signing.vendored_trust()
    assert trust.keys
    assert "development" in trust.description.lower()
