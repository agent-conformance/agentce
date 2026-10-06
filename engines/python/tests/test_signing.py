"""DSSE signing and offline verification (SPEC §8.7, §9.1).

Ed25519 signatures are deterministic (RFC 8032), so the module's output is exercised against a fixed
golden as well as roundtrip, tamper, and order/clock-independence checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentce import error_catalogue, signing
from agentce.canonical import canonicalize

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


def _signed_cert_body(
    ca_key: Ed25519PrivateKey, body: dict[str, object]
) -> dict[str, object]:
    signature = ca_key.sign(canonicalize(body))
    return {**body, "signature": signing._b64e(signature)}


def test_keyless_certificate_non_string_identity_fails() -> None:
    """A validly CA-signed certificate whose `identity` is not a string must collapse to the fixed
    `certificate signature does not verify` message, not be accepted with a non-string identity --
    verifier round-2 (adjacent probes; TypeScript and Java already reject this, Python did not)."""
    ca = Ed25519PrivateKey.generate()
    leaf = Ed25519PrivateKey.generate()
    cert = _signed_cert_body(
        ca,
        {
            "issuer": "dev-ca",
            "identity": 5,
            "algorithm": "ed25519",
            "public_key": signing.public_ed25519_b64(leaf.public_key()),
            "not_before": "2026-01-01T00:00:00.000Z",
            "not_after": "2027-01-01T00:00:00.000Z",
        },
    )
    trust = signing.TrustRoot(authorities={"dev-ca": ca.public_key()})
    env = signing.sign_statement(
        _statement(), signing.KeylessSigner(private_key=leaf, cert=cert)
    )
    with pytest.raises(
        signing.VerificationError, match="certificate signature does not verify"
    ):
        signing.verify_envelope(env, trust)


def test_keyless_certificate_wrong_length_leaf_key_fails() -> None:
    """A validly CA-signed certificate whose `public_key` is not a 32-byte Ed25519 key must collapse
    to the fixed `certificate signature does not verify` message in all three engines -- verifier
    round-2 (adjacent probes; TypeScript/Java deferred leaf-key loading past this function's own
    collapsing error handler and leaked a native message instead)."""
    ca = Ed25519PrivateKey.generate()
    cert = _signed_cert_body(
        ca,
        {
            "issuer": "dev-ca",
            "identity": "ci@agent-conformance.org",
            "algorithm": "ed25519",
            "public_key": signing._b64e(b"too-short"),
            "not_before": "2026-01-01T00:00:00.000Z",
            "not_after": "2027-01-01T00:00:00.000Z",
        },
    )
    trust = signing.TrustRoot(authorities={"dev-ca": ca.public_key()})
    env = {
        "payloadType": "application/vnd.in-toto+json",
        "payload": signing._b64e(json.dumps(_statement()).encode("utf-8")),
        "signatures": [{"sig": signing._b64e(bytes(64)), "cert": cert}],
    }
    with pytest.raises(
        signing.VerificationError, match="certificate signature does not verify"
    ):
        signing.verify_envelope(env, trust)


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


# --- 18.8 round-2 critic defect 2: TrustRoot.from_dict's content-addressed keyid invariant ---


def test_trust_root_from_dict_rejects_a_mismatched_keyid() -> None:
    """A trust-root entry's declared id must equal `keyid_for` the key it maps to. Without this check
    an attacker's own embedded `trust-root.json` could label an attacker key with the victim's real
    keyid string, and `verify_envelope`/`--expect-keyid` would accept it as the real signer
    (round-2 critic, verdicts/P18-18.8-critic-r2.md defect 2, live-demonstrated)."""
    real_key = Ed25519PrivateKey.from_private_bytes(bytes(32))
    attacker_key = Ed25519PrivateKey.from_private_bytes(bytes([1] * 32))
    real_keyid = signing.keyid_for(real_key.public_key())
    forged = {
        "keys": {
            real_keyid: {
                "public_key": signing.public_ed25519_b64(attacker_key.public_key()),
                "identity": "Original Corp",
            }
        }
    }
    with pytest.raises(signing.VerificationError):
        signing.TrustRoot.from_dict(forged)


def test_trust_root_from_dict_accepts_a_correctly_addressed_key() -> None:
    key = Ed25519PrivateKey.from_private_bytes(bytes(32))
    keyid = signing.keyid_for(key.public_key())
    data = {
        "keys": {
            keyid: {
                "public_key": signing.public_ed25519_b64(key.public_key()),
                "identity": "op",
            }
        }
    }
    trust = signing.TrustRoot.from_dict(data)
    assert keyid in trust.keys


def _keyless_envelope(**fields: object) -> tuple[dict, signing.TrustRoot]:
    """An envelope signed through a certificate the test authority issues and then re-signs after
    `fields` change its body (a value of `None` deletes the field), so the authority's signature
    verifies and only the field checks can refuse it."""
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
    body = {k: v for k, v in cert.items() if k != "signature"}
    for name, value in fields.items():
        if value is None:
            body.pop(name, None)
        else:
            body[name] = value
    cert = {**body, "signature": signing._b64e(ca.sign(canonicalize(body)))}
    env = signing.sign_statement(
        _statement(), signing.KeylessSigner(private_key=leaf, cert=cert)
    )
    return env, signing.TrustRoot(authorities={"dev-ca": ca.public_key()})


def _assert_refused(key: str, **fields: object) -> None:
    env, trust = _keyless_envelope(**fields)
    with pytest.raises(signing.VerificationError) as exc:
        signing.verify_envelope(env, trust)
    cause = error_catalogue.MESSAGE_KEYS[key].cause.removesuffix(".")
    assert (
        str(exc.value)
        == f"no signature verified against the trust root: {key}: {cause}"
    )


@pytest.mark.parametrize(
    "algorithm", ["ecdsa-p256", "ED25519", "ed25519 ", 1, ["ed25519"], None]
)
def test_keyless_cert_algorithm_must_be_ed25519(algorithm: object) -> None:
    _assert_refused("verify.certificate_algorithm", algorithm=algorithm)


@pytest.mark.parametrize(
    "fields",
    [
        {"not_before": "2026-01-01 00:00:00Z"},
        {"not_before": "2026-01-01T00:00:00+00:00"},
        {"not_before": "2026-01-01T00:00:00"},
        {"not_before": "2026-02-29T00:00:00Z"},
        {"not_before": "2026-01-01T24:00:00Z"},
        {"not_before": "2026-01-01T00:00:00Z\n"},
        {"not_before": "٢٠٢٦-01-01T00:00:00Z"},
        {"not_before": 1767225600},
        {"not_before": None},
        {"not_after": "2027-01-32T00:00:00Z"},
        {"not_after": "2027-01-01T00:00:00.Z"},
        {"not_after": None},
    ],
)
def test_keyless_cert_validity_must_be_rfc3339_utc(fields: dict) -> None:
    _assert_refused("verify.certificate_validity_malformed", **fields)


@pytest.mark.parametrize(
    "not_before, not_after",
    [
        ("2027-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        ("2026-01-01T00:00:00.5Z", "2026-01-01T00:00:00.49Z"),
    ],
)
def test_keyless_cert_window_must_not_be_reversed(
    not_before: str, not_after: str
) -> None:
    _assert_refused(
        "verify.certificate_validity_inverted",
        not_before=not_before,
        not_after=not_after,
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"not_before": "2027-01-01T00:00:00Z", "not_after": "2027-01-01T00:00:00Z"},
        {"not_before": "2026-01-01t00:00:00z"},
        {"not_after": "2026-12-31T23:59:60Z"},
        {"not_before": "2024-02-29T00:00:00Z"},
        {
            "not_before": "2026-01-01T00:00:00.5Z",
            "not_after": "2026-01-01T00:00:00.50Z",
        },
    ],
)
def test_keyless_cert_well_formed_windows_verify(fields: dict) -> None:
    env, trust = _keyless_envelope(**fields)
    assert signing.verify_envelope(env, trust).keyless is True


def test_keyless_cert_algorithm_is_checked_before_the_window() -> None:
    _assert_refused("verify.certificate_algorithm", algorithm="rsa", not_before="junk")
