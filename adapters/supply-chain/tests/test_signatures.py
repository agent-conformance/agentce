"""Attestations are ``verified`` only after a DSSE signature cryptographically verifies (SPEC 12.2).

Every envelope here is built with this file's own DSSE pre-authentication encoding and its own keys, and
the adapter is reached only through its public ``adapt``: a fault shared by a signer and a verifier that
import the same helper would otherwise go unseen.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from agentce_adapters import AdapterError, adapt

INTOTO = "application/vnd.in-toto+json"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Published test seeds: these keys sign fixtures only and protect nothing.
KEY_A = ed25519.Ed25519PrivateKey.from_private_bytes(bytes([0xA1]) * 32)
KEY_B = ed25519.Ed25519PrivateKey.from_private_bytes(bytes([0xB2]) * 32)
KEY_ROGUE = ed25519.Ed25519PrivateKey.from_private_bytes(bytes([0xEE]) * 32)
P256 = ec.derive_private_key(0x1234567, ec.SECP256R1())


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _raw_pub(key: ed25519.Ed25519PrivateKey) -> str:
    return _b64(
        key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )


def _pem_pub(key: Any) -> str:
    return (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode("ascii")
    )


def _pae(payload_type: str, payload: bytes) -> bytes:
    kind = payload_type.encode("utf-8")
    return b"DSSEv1 %d %s %d %s" % (len(kind), kind, len(payload), payload)


def _statement(digest: str = "aaaa1111", **extra: Any) -> dict[str, Any]:
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "bundle", "digest": {"sha256": digest}}],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {},
        **extra,
    }


def _payload(statement: dict[str, Any]) -> bytes:
    return json.dumps(statement, sort_keys=True).encode("utf-8")


def _sign(
    key: Any,
    keyid: str,
    statement: dict[str, Any] | None = None,
    *,
    payload_type: str = INTOTO,
) -> dict[str, Any]:
    payload = _payload(statement or _statement())
    message = _pae(payload_type, payload)
    if isinstance(key, ec.EllipticCurvePrivateKey):
        signature = key.sign(message, ec.ECDSA(hashes.SHA256()))
    else:
        signature = key.sign(message)
    return {
        "payloadType": payload_type,
        "payload": _b64(payload),
        "signatures": [{"keyid": keyid, "sig": _b64(signature)}],
    }


def _record(envelope: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "timestamp": "2026-05-08T09:00:00.000Z",
        "id": "att",
        "kind": "attestation",
        "format": "in-toto",
        "convention_version": "1.0",
        "observed_digests": ["sha256:aaaa1111"],
    }
    if envelope is not None:
        record["dsse"] = envelope
    record.update(extra)
    return record


TRUSTED: dict[str, str | None] = {
    "key-a": _raw_pub(KEY_A),
    "key-b": _raw_pub(KEY_B),
    "key-p256": _pem_pub(P256),
    "key-nomaterial": None,
}


def _data(record: dict[str, Any], trusted: Any = TRUSTED) -> dict[str, Any]:
    result = adapt(json.dumps(record), subject="s", trusted_keys=trusted)
    assert len(result.events) == 1
    return result.events[0]["data"]


def _status(record: dict[str, Any], trusted: Any = TRUSTED) -> str:
    return _data(record, trusted)["verification"]["status"]


def test_pae_known_answer() -> None:
    # The DSSE specification's own worked example.
    assert (
        _pae("http://example.com/HelloWorld", b"hello world")
        == b"DSSEv1 29 http://example.com/HelloWorld 11 hello world"
    )


def test_valid_ed25519_signature_is_verified() -> None:
    data = _data(_record(_sign(KEY_A, "key-a")))
    assert data["verification"] == {"status": "verified", "method": "dsse-ed25519"}
    assert data["signer"] == "key-a"
    assert data["statement_type"] == "https://in-toto.io/Statement/v1"
    assert data["subject_digests"] == ["sha256:aaaa1111"]


def test_p256_valid_is_verified() -> None:
    data = _data(_record(_sign(P256, "key-p256")))
    assert data["verification"] == {"status": "verified", "method": "dsse-ecdsa-p256"}


def test_p256_raw_rs_signature_is_failed() -> None:
    envelope = _sign(P256, "key-p256")
    der = base64.b64decode(envelope["signatures"][0]["sig"])
    r, s = decode_dss_signature(der)
    envelope["signatures"][0]["sig"] = _b64(
        r.to_bytes(32, "big") + s.to_bytes(32, "big")
    )
    assert _status(_record(envelope)) == "failed"


def test_forged_signature_under_trusted_key_is_failed() -> None:
    envelope = _sign(KEY_A, "key-a")
    envelope["signatures"][0]["sig"] = _b64(bytes(64))
    assert _status(_record(envelope)) == "failed"


def test_untrusted_signer_valid_signature_is_failed() -> None:
    assert _status(_record(_sign(KEY_ROGUE, "key-rogue"))) == "failed"


def test_payload_swap_is_failed() -> None:
    envelope = _sign(KEY_A, "key-a", _statement("aaaa1111"))
    envelope["payload"] = _b64(_payload(_statement("bbbb2222")))
    record = _record(envelope, observed_digests=["sha256:bbbb2222"])
    assert _status(record) == "failed"


def test_payload_edited_is_failed() -> None:
    envelope = _sign(KEY_A, "key-a")
    edited = json.loads(base64.b64decode(envelope["payload"]))
    edited["predicate"] = {"injected": True}
    envelope["payload"] = _b64(_payload(edited))
    assert _status(_record(envelope)) == "failed"


def test_wrong_payload_type_is_failed() -> None:
    # A validly signed envelope of another payload type is not an in-toto attestation.
    envelope = _sign(KEY_A, "key-a", payload_type="text/plain")
    assert _status(_record(envelope)) == "failed"


def test_keyid_relabel_is_failed() -> None:
    # A genuine signature by key-a presented as key-b's.
    envelope = _sign(KEY_A, "key-b")
    assert _status(_record(envelope)) == "failed"


def test_key_type_mismatch_is_failed() -> None:
    # An Ed25519 signature checked against a P-256 trusted key, and the reverse.
    assert _status(_record(_sign(KEY_A, "key-p256"))) == "failed"
    assert _status(_record(_sign(P256, "key-a"))) == "failed"


def test_no_key_material_is_unverified() -> None:
    assert _status(_record(_sign(KEY_A, "key-nomaterial"))) == "unverified"
    # The legacy form: an iterable of key ids, none with material.
    assert _status(_record(_sign(KEY_A, "key-a")), ["key-a"]) == "unverified"


def test_unsigned_is_unverified() -> None:
    assert _status(_record(None)) == "unverified"
    envelope = _sign(KEY_A, "key-a")
    envelope["signatures"] = []
    assert _status(_record(envelope)) == "unverified"


def test_legacy_key_id_only_is_unverified() -> None:
    record = _record(None, subject_digests=["sha256:aaaa1111"])
    record["signature"] = {"key_id": "key-a"}
    assert _status(record) == "unverified"


def test_malformed_envelope_is_failed() -> None:
    bad_base64 = _sign(KEY_A, "key-a")
    bad_base64["payload"] = "!!not base64!!"
    assert _status(_record(bad_base64)) == "failed"
    not_json = _sign(KEY_A, "key-a")
    not_json["payload"] = _b64(b"not json")
    assert _status(_record(not_json)) == "failed"
    no_subject = _sign(KEY_A, "key-a", {"_type": "https://in-toto.io/Statement/v1"})
    assert _status(_record(no_subject)) == "failed"


def test_mixed_signatures_untrusted_plus_keyless_is_failed() -> None:
    # An attacker cannot downgrade a tamper signal by adding a keyless or no-material entry.
    envelope = _sign(KEY_ROGUE, "key-rogue")
    envelope["signatures"].append({"sig": _b64(bytes(64))})
    envelope["signatures"].append({"keyid": "key-nomaterial", "sig": _b64(bytes(64))})
    assert _status(_record(envelope)) == "failed"


def test_keyless_signature_alone_is_unverified() -> None:
    envelope = _sign(KEY_A, "key-a")
    del envelope["signatures"][0]["keyid"]
    assert _status(_record(envelope)) == "unverified"


def test_one_verifying_signature_among_others_is_verified() -> None:
    envelope = _sign(KEY_A, "key-a")
    envelope["signatures"].insert(0, {"keyid": "key-rogue", "sig": _b64(bytes(64))})
    assert _status(_record(envelope)) == "verified"


def test_digest_mismatch_is_failed() -> None:
    record = _record(_sign(KEY_A, "key-a"), observed_digests=["sha256:cccc3333"])
    assert _status(record) == "failed"


def test_no_observed_digests_is_unverified() -> None:
    record = _record(_sign(KEY_A, "key-a"))
    del record["observed_digests"]
    assert _status(record) == "unverified"


def test_observed_digests_match_is_order_and_case_independent() -> None:
    statement = _statement()
    statement["subject"].append({"name": "b", "digest": {"sha256": "BBBB2222"}})
    record = _record(
        _sign(KEY_A, "key-a", statement),
        observed_digests=["sha256:bbbb2222", "sha256:aaaa1111"],
    )
    data = _data(record)
    assert data["verification"]["status"] == "verified"
    assert data["subject_digests"] == ["sha256:aaaa1111", "sha256:bbbb2222"]


def test_leak_forged_signer_logref_statement_fields_not_surfaced() -> None:
    record = _record(
        _sign(KEY_A, "key-a"),
        signer="CN=release-manager",
        log_ref="rekor://index/1",
        method="sigstore-bundle",
        statement_type="https://example.com/forged",
        subject_digests=["sha256:forged"],
    )
    data = _data(record)
    assert data["verification"] == {"status": "verified", "method": "dsse-ed25519"}
    assert data["signer"] == "key-a"
    assert data["statement_type"] == "https://in-toto.io/Statement/v1"
    assert data["subject_digests"] == ["sha256:aaaa1111"]
    unverified = _data(_record(None, signer="CN=release-manager", log_ref="rekor://x"))
    assert "signer" not in unverified
    assert unverified["verification"] == {"status": "unverified"}


def test_bad_trusted_key_raises() -> None:
    for material in ("not a key", _b64(bytes(10))):
        with pytest.raises(AdapterError) as excinfo:
            adapt("", subject="s", trusted_keys={"k": material})
        assert excinfo.value.reason == "bad_trusted_key"
    p384 = ec.derive_private_key(0x1234567, ec.SECP384R1())
    with pytest.raises(AdapterError) as excinfo:
        adapt("", subject="s", trusted_keys={"k": _pem_pub(p384)})
    assert excinfo.value.reason == "bad_trusted_key"


def test_url_safe_and_unpadded_base64_is_accepted() -> None:
    envelope = _sign(KEY_A, "key-a", _statement(">>>???"))
    payload = _payload(_statement(">>>???"))
    envelope["payload"] = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = KEY_A.sign(_pae(INTOTO, payload))
    envelope["signatures"][0]["sig"] = base64.urlsafe_b64encode(signature).decode(
        "ascii"
    )
    record = _record(envelope, observed_digests=["sha256:>>>???"])
    assert _status(record) == "verified"


def test_interop_envelope_signed_by_another_implementation_is_verified() -> None:
    # fixtures/attestations-interop/ was signed by Node's crypto over a PAE built by sign.mjs.
    directory = FIXTURES / "attestations-interop"
    kwargs = json.loads((directory / "adapt.json").read_text(encoding="utf-8"))
    payload = (directory / "input.jsonl").read_bytes()
    result = adapt(payload, **kwargs)
    statuses = [e["data"]["verification"]["status"] for e in result.events]
    assert statuses == ["verified"]
