"""DSSE signing and offline signature verification (SPEC §8.7, §9.1).

Digests are SHA-256 and signatures are Ed25519 carried in a DSSE envelope over an in-toto Statement;
the algorithm identifier is explicit in every digest (``sha256:``) and key (``ed25519``). The engine
uses the platform's standard cryptographic primitives (`cryptography`, OpenSSL-backed) behind the
narrow surface of this module, so a deployment that requires FIPS-validated modules substitutes a
validated provider without touching the callers (SPEC §8.7).

Two directions, one core:

* **Verify** — ``verify_envelope`` checks a DSSE envelope against a :class:`TrustRoot` and never
  contacts a transparency log (HR-1). The engine ships one trust root, :func:`vendored_trust`, that
  verifies the repository's own signed catalogs, corpora, and dry-run release artifacts.
* **Sign** — given an operator-held key, :func:`sign_statement` produces the envelope. The engine
  holds no signing identity of its own; ``agentce sign`` signs on the invoking identity's behalf and
  the release tooling supplies the development keys for the dry-run (SPEC §9.1).

Signing profiles (SPEC §9.1):

* ``kms`` — a DSSE signature by a KMS/HSM-held key; the trust root pins the key by content-addressed
  ``keyid``.
* ``sigstore-private`` / ``sigstore-public`` — keyless: an ephemeral key signs, and a short-lived
  certificate binds the signer identity to that key. The trust root pins the issuing authority; the
  ``public`` profile additionally records transparency-log inclusion in production, skipped and noted
  in an offline dry-run.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import canonicalize

#: DSSE payload type for an in-toto Statement (in-toto attestation framework).
INTOTO_PAYLOAD_TYPE = "application/vnd.in-toto+json"
#: in-toto Statement schema version.
INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
#: The detached signature a signed catalog (or corpus) directory carries (SPEC §8.7).
CATALOG_SIGNATURE_NAME = "catalog.sig.json"

__all__ = [
    "CATALOG_SIGNATURE_NAME",
    "SigningError",
    "VerificationError",
    "Verified",
    "TrustRoot",
    "Signer",
    "KmsSigner",
    "KeylessSigner",
    "sha256_prefixed",
    "digest_tree",
    "intoto_statement",
    "issue_certificate",
    "sign_statement",
    "verify_envelope",
    "vendored_trust",
    "vendored_trust_path",
]


class SigningError(Exception):
    """A signature could not be produced."""


class VerificationError(Exception):
    """A signature, certificate, or bound digest failed verification (exit code 3)."""


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def sha256_prefixed(data: bytes) -> str:
    """Return ``sha256:<hex>`` for ``data`` (the digest form used throughout the report, §8.4)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_public_ed25519(raw_b64: str) -> Ed25519PublicKey:
    """Load an Ed25519 public key from its base64 raw (32-byte) encoding."""
    return Ed25519PublicKey.from_public_bytes(_b64d(raw_b64))


def public_ed25519_b64(key: Ed25519PublicKey) -> str:
    """Return the base64 raw (32-byte) encoding of an Ed25519 public key."""
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    return _b64e(key.public_bytes(Encoding.Raw, PublicFormat.Raw))


def keyid_for(key: Ed25519PublicKey) -> str:
    """A content-addressed key id: ``sha256:`` over the raw public key."""
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    return sha256_prefixed(key.public_bytes(Encoding.Raw, PublicFormat.Raw))


def _pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding (the exact bytes a signature covers)."""
    pt = payload_type.encode("utf-8")
    return b"DSSEv1 %d %s %d %s" % (len(pt), pt, len(payload), payload)


# --- Statements and certificates. ---


def digest_tree(root: Path, *, exclude: frozenset[str] = frozenset()) -> str:
    """Content-address a directory: ``sha256:`` over the sorted ``<relpath>\\0<filehash>`` lines.

    The walk is filesystem-based (no git), sorted by POSIX relative path, and skips dotfiles,
    ``__pycache__``, and any name in ``exclude`` (e.g. the signature file itself), so the same
    directory yields the same digest on any host — the basis of the offline catalog/release check.
    """
    lines: list[bytes] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in exclude or any(
            part.startswith(".") or part == "__pycache__"
            for part in path.relative_to(root).parts
        ):
            continue
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(rel.encode("utf-8") + b"\x00" + file_hash.encode("ascii"))
    return "sha256:" + hashlib.sha256(b"\n".join(lines)).hexdigest()


def intoto_statement(
    subject_name: str, digest: str, predicate_type: str, predicate: dict[str, Any]
) -> dict[str, Any]:
    """Build an in-toto Statement v1 whose single subject carries ``digest`` (``sha256:<hex>``)."""
    algo, _, hexval = digest.partition(":")
    return {
        "_type": INTOTO_STATEMENT_TYPE,
        "subject": [{"name": subject_name, "digest": {algo: hexval}}],
        "predicateType": predicate_type,
        "predicate": predicate,
    }


def issue_certificate(
    ca_key: Ed25519PrivateKey,
    *,
    issuer: str,
    identity: str,
    leaf_public: Ed25519PublicKey,
    not_before: str,
    not_after: str,
) -> dict[str, Any]:
    """Bind ``identity`` to ``leaf_public`` with a short-lived certificate the CA signs (keyless).

    A faithful, minimal model of Fulcio's identity binding: the authority attests that ``identity``
    controlled ``leaf_public`` during ``[not_before, not_after]``. The signature is over the RFC 8785
    canonical form of the certificate body.
    """
    body = {
        "issuer": issuer,
        "identity": identity,
        "algorithm": "ed25519",
        "public_key": public_ed25519_b64(leaf_public),
        "not_before": not_before,
        "not_after": not_after,
    }
    signature = ca_key.sign(canonicalize(body))
    return {**body, "signature": _b64e(signature)}


def verify_certificate(
    cert: dict[str, Any], authorities: dict[str, Ed25519PublicKey]
) -> tuple[Ed25519PublicKey, str]:
    """Verify a keyless certificate against the pinned authorities; return ``(leaf_key, identity)``."""
    issuer = cert.get("issuer")
    ca = authorities.get(issuer) if isinstance(issuer, str) else None
    if ca is None:
        raise VerificationError(f"unknown certificate issuer {issuer!r}")
    body = {k: v for k, v in cert.items() if k != "signature"}
    try:
        ca.verify(_b64d(cert["signature"]), canonicalize(body))
    except (InvalidSignature, KeyError, ValueError) as exc:
        raise VerificationError("certificate signature does not verify") from exc
    return load_public_ed25519(cert["public_key"]), cert["identity"]


# --- Signers. ---


@dataclass
class Signer:
    """An operator-held signing identity. Subclasses provide the key and any keyless certificate."""

    def sign(self, data: bytes) -> bytes:  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def keyid(self) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def certificate(self) -> dict[str, Any] | None:
        return None


@dataclass
class KmsSigner(Signer):
    """A key pinned in the trust root by content-addressed ``keyid`` (the ``kms`` profile)."""

    private_key: Ed25519PrivateKey

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(data)

    @property
    def keyid(self) -> str:
        return keyid_for(self.private_key.public_key())


@dataclass
class KeylessSigner(Signer):
    """An ephemeral key plus a CA certificate binding the signer identity (the ``sigstore-*`` profiles)."""

    private_key: Ed25519PrivateKey
    cert: dict[str, Any]

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(data)

    @property
    def keyid(self) -> str:
        return keyid_for(self.private_key.public_key())

    def certificate(self) -> dict[str, Any] | None:
        return self.cert


# --- Trust root. ---


@dataclass
class TrustRoot:
    """The offline material a verifier trusts: pinned KMS keys and keyless certificate authorities."""

    keys: dict[str, Ed25519PublicKey] = field(default_factory=dict)
    key_identities: dict[str, str] = field(default_factory=dict)
    authorities: dict[str, Ed25519PublicKey] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrustRoot":
        keys: dict[str, Ed25519PublicKey] = {}
        identities: dict[str, str] = {}
        for keyid, entry in (data.get("keys") or {}).items():
            keys[keyid] = load_public_ed25519(entry["public_key"])
            identities[keyid] = entry.get("identity", keyid)
        authorities = {
            issuer: load_public_ed25519(entry["public_key"])
            for issuer, entry in (data.get("certificate_authorities") or {}).items()
        }
        return cls(
            keys=keys,
            key_identities=identities,
            authorities=authorities,
            description=str(data.get("description", "")),
        )

    def resolve(self, signature: dict[str, Any]) -> tuple[Ed25519PublicKey, str]:
        """Resolve a DSSE signature entry to the ``(public_key, identity)`` that must verify it."""
        cert = signature.get("cert")
        if cert is not None:
            return verify_certificate(cert, self.authorities)
        keyid = signature.get("keyid")
        if not isinstance(keyid, str) or keyid not in self.keys:
            raise VerificationError(f"no trusted key for keyid {keyid!r}")
        return self.keys[keyid], self.key_identities.get(keyid, keyid)


@dataclass
class Verified:
    """The result of a successful verification."""

    payload: bytes
    identity: str
    keyid: str | None
    keyless: bool


def sign_statement(statement: dict[str, Any], signer: Signer) -> dict[str, Any]:
    """Produce a DSSE envelope over ``statement`` (canonical bytes) signed by ``signer``."""
    payload = canonicalize(statement)
    signature = signer.sign(_pae(INTOTO_PAYLOAD_TYPE, payload))
    entry: dict[str, Any] = {"keyid": signer.keyid, "sig": _b64e(signature)}
    cert = signer.certificate()
    if cert is not None:
        entry["cert"] = cert
    return {
        "payloadType": INTOTO_PAYLOAD_TYPE,
        "payload": _b64e(payload),
        "signatures": [entry],
    }


def verify_envelope(envelope: dict[str, Any], trust: TrustRoot) -> Verified:
    """Verify a DSSE envelope against ``trust``; raise :class:`VerificationError` on any failure."""
    try:
        payload_type = envelope["payloadType"]
        payload = _b64d(envelope["payload"])
        signatures = envelope["signatures"]
    except (KeyError, ValueError, TypeError) as exc:
        raise VerificationError("malformed DSSE envelope") from exc
    if not signatures:
        raise VerificationError("DSSE envelope carries no signatures")
    pae = _pae(payload_type, payload)
    last_error: Exception | None = None
    for signature in signatures:
        try:
            public_key, identity = trust.resolve(signature)
            public_key.verify(_b64d(signature["sig"]), pae)
            return Verified(
                payload=payload,
                identity=identity,
                keyid=signature.get("keyid"),
                keyless="cert" in signature,
            )
        except (InvalidSignature, VerificationError, KeyError, ValueError) as exc:
            last_error = exc
    raise VerificationError(
        f"no signature verified against the trust root: {last_error}"
    )


def vendored_trust_path() -> Path:
    """The path to the trust root vendored in the engine package (SPEC §8.7)."""
    return Path(__file__).resolve().parent / "data" / "trust" / "dev-root.json"


def vendored_trust() -> TrustRoot:
    """Load the trust root vendored in the engine package."""
    import json

    return TrustRoot.from_dict(json.loads(vendored_trust_path().read_text("utf-8")))
