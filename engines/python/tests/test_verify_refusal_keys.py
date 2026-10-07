"""Verify refusals carry stable message keys (18.64): every refusal text is built from its catalogue
cause, a wrapper lists its own key before its inner refusal's keys, and a document nested past
MAX_JSON_DEPTH is refused as verify.json_too_deep."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentce import signing
from agentce.error_catalogue import MESSAGE_KEYS
from agentce.signing import MAX_JSON_DEPTH, VerificationError

PACKAGE = Path(signing.__file__).resolve().parent
VERIFY_SOURCES = (PACKAGE / "signing.py", PACKAGE / "commands" / "__init__.py")


def test_every_verify_key_the_sources_name_is_in_the_catalogue() -> None:
    named = {
        key
        for path in VERIFY_SOURCES
        for key in re.findall(r'"(verify\.[a-z_]+)"', path.read_text(encoding="utf-8"))
    }
    assert "verify.json_too_deep" in named
    assert sorted(named - MESSAGE_KEYS.keys()) == []


def test_leaf_refusal_is_its_cause_with_params_filled() -> None:
    refusal = VerificationError.refusal("verify.keyid_untrusted", keyid="'k1'")
    assert str(refusal) == "no trusted key for keyid 'k1'"
    assert refusal.keys == ("verify.keyid_untrusted",)


def test_wrapper_lists_its_key_then_the_inner_keys() -> None:
    inner = VerificationError.refusal("verify.signature_invalid")
    wrapped = VerificationError.refusal("verify.no_signature_verified", inner)
    outer = VerificationError.refusal(
        "verify.release_signature", wrapped, profile="kms"
    )
    assert str(outer) == (
        "signature (kms): no signature verified against the trust root: "
        "signature does not verify"
    )
    assert outer.keys == (
        "verify.release_signature",
        "verify.no_signature_verified",
        "verify.signature_invalid",
    )


def test_a_missing_sig_field_is_a_sentence_not_a_key_error_repr() -> None:
    public_key = Ed25519PrivateKey.generate().public_key()
    keyid = signing.keyid_for(public_key)
    trust = signing.TrustRoot(keys={keyid: public_key})
    envelope = {
        "payloadType": signing.INTOTO_PAYLOAD_TYPE,
        "payload": "e30=",
        "signatures": [{"keyid": keyid}],
    }
    with pytest.raises(VerificationError) as caught:
        signing.verify_envelope(envelope, trust)
    assert str(caught.value) == (
        "no signature verified against the trust root: a signature entry has no sig field"
    )
    assert caught.value.keys == (
        "verify.no_signature_verified",
        "verify.signature_sig_missing",
    )
    assert MESSAGE_KEYS["verify.release_envelope_missing"].cause == (
        "a signature entry has no envelope"
    )


def _nested(depth: int) -> bytes:
    return b"[" * depth + b"]" * depth


def test_json_too_deep_starts_one_past_the_limit() -> None:
    assert signing.parse_verify_input(
        _nested(MAX_JSON_DEPTH),
        "verify.catalog_signature_unreadable",
        "catalog.sig.json",
    )
    with pytest.raises(VerificationError) as caught:
        signing.parse_verify_input(
            _nested(MAX_JSON_DEPTH + 1),
            "verify.catalog_signature_unreadable",
            "catalog.sig.json",
        )
    assert caught.value.keys == ("verify.json_too_deep",)
    assert str(caught.value) == (
        f"catalog.sig.json nests containers more than {MAX_JSON_DEPTH} levels deep"
    )


@pytest.mark.parametrize(
    "raw",
    [
        _nested(MAX_JSON_DEPTH + 1)[:-1],  # truncated
        b"[" * 100_000,  # truncated, far past the limit
        _nested(MAX_JSON_DEPTH + 1) + b" x",  # trailing garbage
        b'["\\ud800",' + _nested(MAX_JSON_DEPTH) + b"]",  # a lone surrogate first
        b"\xef\xbb\xbf" + _nested(MAX_JSON_DEPTH + 1),  # a byte-order mark
    ],
)
def test_depth_is_found_before_any_other_problem(raw: bytes) -> None:
    with pytest.raises(VerificationError) as caught:
        signing.parse_verify_input(raw, "verify.catalog_signature_unreadable", "x")
    assert caught.value.keys == ("verify.json_too_deep",)


def test_brackets_inside_strings_do_not_count() -> None:
    raw = b'["' + b"[" * (MAX_JSON_DEPTH + 5) + b'\\"["]'
    assert signing.parse_verify_input(raw, "verify.catalog_signature_unreadable", "x")


def test_other_callers_keep_the_not_readable_text() -> None:
    with pytest.raises(ValueError, match="^not readable JSON$"):
        signing.parse_untrusted_json(_nested(MAX_JSON_DEPTH + 1))
