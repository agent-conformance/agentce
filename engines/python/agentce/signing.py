"""DSSE signing and offline signature verification (SPEC §8.7, §9.1).

Digests are SHA-256 and signatures are Ed25519 carried in a DSSE envelope over an in-toto Statement;
the algorithm identifier is explicit in every digest (``sha256:``) and key (``ed25519``). The engine
uses the platform's standard cryptographic primitives (`cryptography`, OpenSSL-backed) behind the
narrow surface of this module, so a deployment that requires FIPS-validated modules substitutes a
validated provider without touching the callers (SPEC §8.7).

Two directions, one core:

* **Verify** — ``verify_envelope`` checks a DSSE envelope against a :class:`TrustRoot` and never
  contacts a transparency log (HR-1). The engine ships one trust root, :func:`vendored_trust`, that
  verifies the repository's own signed catalogs, corpora, and dry-run release artifacts; a deployment
  that signs with its own keys supplies its own root (:func:`load_trust_root`, reached through
  ``agentce assess --trust-root`` or ``AGENTCE_TRUST_ROOT``). :func:`verify_catalog_directory` is the
  one place that decides whether a catalog directory on disk may be used.
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
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import canonicalize
from .error_catalogue import MESSAGE_KEYS
from .i18n_format import format_message

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
    "UnsignedError",
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
    "statement_subject_digest",
    "parse_untrusted_json",
    "describe_untrusted",
    "verify_envelope",
    "verify_catalog_directory",
    "load_trust_root",
    "vendored_trust",
    "vendored_trust_path",
]


class SigningError(Exception):
    """A signature could not be produced."""


class VerificationError(Exception):
    """A signature, certificate, or bound digest failed verification (exit code 3).

    ``keys`` names the catalogue entries its text was built from, in reading order: one key for a
    leaf refusal, a wrapper's own key followed by its inner refusal's keys. ``verify --json`` lists
    them as ``reason_keys`` so a script can match on the key, not the sentence."""

    def __init__(self, text: str, keys: tuple[str, ...] = ()) -> None:
        super().__init__(text)
        self.keys = keys

    @classmethod
    def refusal(
        cls, key: str, inner: Exception | None = None, **params: object
    ) -> "VerificationError":
        """The refusal for catalogue ``key``: its cause with ``{params}`` filled in. A wrapper
        passes the refusal it wraps as ``inner``, which fills ``{inner}`` and adds its keys."""
        keys = (key,)
        if inner is not None:
            params["inner"] = inner
            keys += getattr(inner, "keys", ())
        return cls(format_message(MESSAGE_KEYS[key].cause, **params), keys)


class UnsignedError(VerificationError):
    """The directory carries no detached signature at all, so there is nothing to verify."""


#: The deepest container nesting `parse_untrusted_json` accepts (Jackson's own default limit); all
#: three engines enforce it with the same byte pre-scan, so they refuse at the same depth.
MAX_JSON_DEPTH = 1000


class JsonTooDeep(ValueError):
    """A document nests containers past :data:`MAX_JSON_DEPTH`. Its text stays "not readable JSON"
    for callers that do not tell depth apart; verify's own readers key it as ``verify.json_too_deep``."""

    def __init__(self) -> None:
        super().__init__("not readable JSON")


_STRUCTURE = re.compile(rb'["\\\[\]{}]')


def _too_deep(raw: bytes) -> bool:
    """Whether ``raw`` opens more than :data:`MAX_JSON_DEPTH` containers at once, counting ``[``
    and ``{`` up and ``]`` and ``}`` down outside strings (a backslash in a string skips the next
    byte). Run before parsing, so depth is found first whatever else is wrong with the bytes."""
    depth = 0
    in_string = False
    escaped_until = -1
    for match in _STRUCTURE.finditer(raw):
        i = match.start()
        if i < escaped_until:
            continue
        byte = raw[i]
        if in_string:
            if byte == 0x5C:
                escaped_until = i + 2
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return True
        elif byte in (0x5D, 0x7D):
            depth -= 1
    return False


_SURROGATE = re.compile("[\ud800-\udfff]")
_PLAIN_ASCII = re.compile(r"[ !#-&(-\[\]-~]*")


def _refuse_constant(token: str) -> Any:
    raise ValueError(f"non-standard JSON token {token}")


def parse_untrusted_json(raw: bytes) -> Any:
    """Parse JSON that `verify` reads from an untrusted file or signed payload, or raise ``ValueError``.

    One rule set the three engines share, so the same bytes are refused or accepted alike: strict
    UTF-8 with no byte-order mark, standard JSON only (no ``NaN``/``Infinity``, no trailing data),
    containers nested at most :data:`MAX_JSON_DEPTH` deep, and every string and key well-formed
    Unicode (an escaped lone surrogate such as ``"\\ud800"`` is refused). Callers turn the
    ``ValueError`` into their own fixed reason text; its message is never shown. A document too
    deep raises the :class:`JsonTooDeep` subclass, whatever else is wrong with it."""
    if _too_deep(raw):
        raise JsonTooDeep()
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant)
    except (ValueError, RecursionError) as exc:
        raise ValueError("not readable JSON") from exc
    stack: list[Any] = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            if _SURROGATE.search(node):
                raise ValueError("not readable JSON")
        elif isinstance(node, dict):
            stack.extend(node.values())
            stack.extend(node)
        elif isinstance(node, list):
            stack.extend(node)
    return value


def parse_verify_input(raw: bytes, unreadable_key: str, what: str) -> Any:
    """:func:`parse_untrusted_json` for one input verify reads, refused with that input's own key:
    ``verify.json_too_deep`` naming ``what`` past the depth limit, else ``unreadable_key``."""
    try:
        return parse_untrusted_json(raw)
    except JsonTooDeep as exc:
        raise VerificationError.refusal(
            "verify.json_too_deep", what=what, limit=MAX_JSON_DEPTH
        ) from exc
    except ValueError as exc:
        raise VerificationError.refusal(unreadable_key) from exc


def describe_untrusted(value: Any) -> str:
    """How a refusal names a value read from an untrusted document (a keyid, an issuer): ``None``,
    a quoted plain-ASCII string, or a fixed description -- never a language's own repr of an arbitrary
    value, which the three engines cannot reproduce alike."""
    if value is None:
        return "None"
    if isinstance(value, str):
        return (
            f"'{value}'"
            if _PLAIN_ASCII.fullmatch(value)
            else "<a string with special characters>"
        )
    return "<not a string>"


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


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
    paths = sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())
    # `rglob` skips a folder it cannot list without saying so, which would sign or check a digest
    # that silently leaves the folder out: list each real folder it descends, so one that cannot
    # be listed raises the OSError (naming it) before any digest is formed (18.68).
    for folder in (root, *paths):
        if folder.is_dir() and not folder.is_symlink():
            os.listdir(folder)
    for path in paths:
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
    cert: Any, authorities: dict[str, Ed25519PublicKey]
) -> tuple[Ed25519PublicKey, str]:
    """Verify a keyless certificate against the pinned authorities; return ``(leaf_key, identity)``.

    Every way a certificate can be malformed -- not an object, a missing/non-base64 ``signature``, a
    signature that does not verify, a missing ``public_key``/``identity`` -- collapses to the one
    ``verify.certificate_signature_invalid`` refusal (SPEC's keyless model treats a malformed
    certificate the same as one that fails to verify; TS/Java mirror this exact collapse)."""
    if not isinstance(cert, dict):
        raise VerificationError.refusal("verify.certificate_signature_invalid")
    issuer = cert.get("issuer")
    ca = authorities.get(issuer) if isinstance(issuer, str) else None
    if ca is None:
        raise VerificationError.refusal(
            "verify.certificate_issuer_unknown", issuer=describe_untrusted(issuer)
        )
    body = {k: v for k, v in cert.items() if k != "signature"}
    try:
        ca.verify(_b64d(cert["signature"]), canonicalize(body))
        leaf_key = load_public_ed25519(cert["public_key"])
        identity = cert["identity"]
        if not isinstance(identity, str):
            raise TypeError("certificate identity is not a string")
    except (
        InvalidSignature,
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        RecursionError,
    ) as exc:
        raise VerificationError.refusal("verify.certificate_signature_invalid") from exc
    _check_certificate_fields(cert)
    return leaf_key, identity


#: RFC 3339 `date-time` in UTC (§5.6): `T`/`Z` in either case, ASCII digits only, an optional fraction.
_RFC3339_UTC = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?[Zz]"
)
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _certificate_refusal(key: str) -> VerificationError:
    """``<key>: <cause>`` with the catalogue's cause text, minus its final period (the aggregate
    reasons that wrap it add their own)."""
    return VerificationError(
        f"{key}: {MESSAGE_KEYS[key].cause.removesuffix('.')}", (key,)
    )


def _utc_order_key(value: Any) -> str | None:
    """A text form of an RFC 3339 UTC timestamp that sorts in time order, or ``None`` if ``value``
    is not one. The 14 date-time digits, then the fraction without trailing zeros, so ``.5`` equals
    ``.50``. Second 60 is admitted (the ABNF's leap second); no leap-second table is consulted."""
    match = _RFC3339_UTC.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    if not 1 <= month <= 12:
        return None
    days = 29 if month == 2 and leap else _DAYS_IN_MONTH[month - 1]
    if not (1 <= day <= days and hour <= 23 and minute <= 59 and second <= 60):
        return None
    return "".join(match.groups()[:6]) + "." + (match.group(7) or "").rstrip("0")


def _check_certificate_fields(cert: dict[str, Any]) -> None:
    """The fields a verified certificate must also carry: ``algorithm`` exactly ``ed25519``, and a
    well-formed RFC 3339 UTC window with ``not_before <= not_after``. The window is never compared
    with the clock: keyless certificates are short-lived by design, and the bundle carries no trusted
    signing time to compare it with (docs/verification.md)."""
    if cert.get("algorithm") != "ed25519":
        raise _certificate_refusal("verify.certificate_algorithm")
    not_before = _utc_order_key(cert.get("not_before"))
    not_after = _utc_order_key(cert.get("not_after"))
    if not_before is None or not_after is None:
        raise _certificate_refusal("verify.certificate_validity_malformed")
    if not_before > not_after:
        raise _certificate_refusal("verify.certificate_validity_inverted")


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

    @property
    def public_key_b64(self) -> str:
        """The base64 raw public key a claimant publishes for ``--write-trust-root`` (SPEC §9.1)."""
        return public_ed25519_b64(self.private_key.public_key())


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


def _trust_entries(
    data: dict[str, Any], field_name: str, shape: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    """The ``(id, entry)`` pairs of a trust root's ``keys`` or ``certificate_authorities`` mapping, with
    one stable cause per malformed shape (18.80): the same words in all three engines, never the text of
    whatever exception a bad shape would otherwise raise. A falsy value is no entries, as before."""
    mapping = data.get(field_name) or {}
    if not isinstance(mapping, dict):
        raise ValueError(f"{field_name} is not a mapping of {shape}")
    for entry_id, entry in mapping.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{field_name} entry {entry_id!r} is not a mapping")
        if entry.get("public_key") is None:
            raise ValueError(f"{field_name} entry {entry_id!r} has no public_key")
        yield entry_id, entry


@dataclass
class TrustRoot:
    """The offline material a verifier trusts: pinned KMS keys and keyless certificate authorities."""

    keys: dict[str, Ed25519PublicKey] = field(default_factory=dict)
    key_identities: dict[str, str] = field(default_factory=dict)
    authorities: dict[str, Ed25519PublicKey] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrustRoot":
        """Load a trust root from its JSON shape (``--trust-root``, ``--signer-trust-root``, an
        embedded ``trust-root.json``, or the vendored root). Every ``keys`` entry's declared id must
        equal :func:`keyid_for` of the key it maps to -- a trust root is content-addressed by
        construction (every trust root this engine itself writes already satisfies this), so an entry
        whose id and key disagree is not a differently-labelled key but a forged or corrupted one: a
        file could otherwise map the real signer's own keyid to an attacker's public key, which
        `verify_envelope`/`--expect-keyid` would then accept as if it were the real signer (SPEC §9.1)."""
        keys: dict[str, Ed25519PublicKey] = {}
        identities: dict[str, str] = {}
        for keyid, entry in _trust_entries(data, "keys", "key id to key entry"):
            public_key = load_public_ed25519(entry["public_key"])
            if keyid_for(public_key) != keyid:
                raise VerificationError(
                    f"trust root entry {keyid!r} does not match its own key"
                )
            keys[keyid] = public_key
            identities[keyid] = entry.get("identity", keyid)
        authorities = {
            issuer: load_public_ed25519(entry["public_key"])
            for issuer, entry in _trust_entries(
                data, "certificate_authorities", "id to entry"
            )
        }
        return cls(
            keys=keys,
            key_identities=identities,
            authorities=authorities,
            description=str(data.get("description", "")),
        )

    @staticmethod
    def document(keyid: str, public_key_b64: str, identity: str) -> dict[str, Any]:
        """The JSON shape :meth:`from_dict` loads, for a single key (``sign --write-trust-root``'s
        embedded ``trust-root.json``, and ``verify --report``'s scratch trust root for its own
        offline re-run): the one place that shape is built, so both callers stay in sync with
        :meth:`from_dict`'s own load-time invariant."""
        return {"keys": {keyid: {"public_key": public_key_b64, "identity": identity}}}

    def resolve(self, signature: dict[str, Any]) -> tuple[Ed25519PublicKey, str]:
        """Resolve a DSSE signature entry to the ``(public_key, identity)`` that must verify it."""
        cert = signature.get("cert")
        if cert is not None:
            return verify_certificate(cert, self.authorities)
        keyid = signature.get("keyid")
        if not isinstance(keyid, str) or keyid not in self.keys:
            raise VerificationError.refusal(
                "verify.keyid_untrusted", keyid=describe_untrusted(keyid)
            )
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


def verify_envelope(envelope: Any, trust: TrustRoot) -> Verified:
    """Verify a DSSE envelope against ``trust``; raise :class:`VerificationError` on any failure.

    Shape is validated explicitly, one ordered check at a time, rather than relying on which
    exception a malformed field happens to raise: a wrong JSON type anywhere in the envelope (an int
    where a string is expected, a string where an array is expected, and so on) must refuse cleanly
    with the one message below, never surface as an unhandled crash (TS/Java port this exact sequence,
    since neither language throws on an ordinary out-of-shape property read the way Python does)."""
    if not isinstance(envelope, dict):
        raise VerificationError.refusal("verify.envelope_malformed")
    payload_type = envelope.get("payloadType")
    if not isinstance(payload_type, str):
        raise VerificationError.refusal("verify.envelope_malformed")
    raw_payload = envelope.get("payload")
    if not isinstance(raw_payload, str):
        raise VerificationError.refusal("verify.envelope_malformed")
    try:
        payload = _b64d(raw_payload)
    except ValueError as exc:
        raise VerificationError.refusal("verify.envelope_malformed") from exc
    signatures = envelope.get("signatures")
    if not isinstance(signatures, list):
        raise VerificationError.refusal("verify.envelope_malformed")
    if not signatures:
        raise VerificationError.refusal("verify.envelope_no_signatures")
    pae = _pae(payload_type, payload)
    last_error: Exception | None = None
    for signature in signatures:
        try:
            if not isinstance(signature, dict):
                # A non-object entry has no keyid to resolve; route it through the same
                # missing-keyid refusal `TrustRoot.resolve` gives for an absent `keyid` field.
                raise VerificationError.refusal(
                    "verify.keyid_untrusted", keyid=describe_untrusted(None)
                )
            public_key, identity = trust.resolve(signature)
            sig = signature.get("sig")
            if not isinstance(sig, str):
                raise VerificationError.refusal("verify.signature_sig_missing")
            try:
                sig_bytes = _b64d(sig)
            except ValueError as exc:
                raise VerificationError.refusal("verify.signature_not_base64") from exc
            try:
                public_key.verify(sig_bytes, pae)
            except InvalidSignature as exc:
                # InvalidSignature has no text of its own; give the reason a sentence (18.68).
                raise VerificationError.refusal("verify.signature_invalid") from exc
            keyid = signature.get("keyid")
            return Verified(
                payload=payload,
                identity=identity,
                keyid=keyid if isinstance(keyid, str) else None,
                # TrustRoot.resolve's own test: a "cert": null entry resolves as a key (18.68).
                keyless=signature.get("cert") is not None,
            )
        except (VerificationError, ValueError) as exc:
            last_error = exc
    raise VerificationError.refusal("verify.no_signature_verified", inner=last_error)


def statement_subject_digest(payload: bytes) -> str:
    """Return the ``sha256:`` digest of the first subject of the in-toto Statement in ``payload``
    (a verified envelope's payload bytes), or raise :class:`VerificationError` with one of two fixed
    texts: the payload is not readable JSON, or it has no ``subject[0].digest.sha256`` string."""
    statement = parse_verify_input(
        payload, "verify.statement_unreadable", "the signed statement"
    )
    subject = statement.get("subject") if isinstance(statement, dict) else None
    first = subject[0] if isinstance(subject, list) and subject else None
    digest = first.get("digest") if isinstance(first, dict) else None
    sha256 = digest.get("sha256") if isinstance(digest, dict) else None
    if not isinstance(sha256, str):
        raise VerificationError.refusal("verify.statement_no_digest")
    return "sha256:" + sha256


def verify_catalog_directory(directory: Path, trust: TrustRoot) -> Verified:
    """Verify a catalog directory's detached signature against ``trust`` (SPEC §8.7).

    One place decides whether a catalog on disk may be used: the signature file must be present, the
    DSSE envelope must verify against the trust root, and the digest it covers must equal a fresh
    recomputation of the directory's content. Any failure raises :class:`VerificationError` — an
    absent signature raises the :class:`UnsignedError` subclass, so a caller that reports "unsigned"
    differently from "did not verify" can tell them apart. Never contacts the network.
    """
    sig_path = directory / CATALOG_SIGNATURE_NAME
    if not sig_path.is_file():
        raise UnsignedError.refusal("verify.catalog_unsigned")
    try:
        raw = sig_path.read_bytes()
    except PermissionError:
        raise  # an unreadable signature is the caller's input.catalog_unreadable, not a bad one
    except OSError as exc:
        raise VerificationError.refusal("verify.catalog_signature_unreadable") from exc
    envelope = parse_verify_input(
        raw, "verify.catalog_signature_unreadable", CATALOG_SIGNATURE_NAME
    )
    verified = verify_envelope(envelope, trust)
    signed_digest = statement_subject_digest(verified.payload)
    recomputed = digest_tree(directory, exclude=frozenset({CATALOG_SIGNATURE_NAME}))
    if signed_digest != recomputed:
        raise VerificationError.refusal("verify.catalog_digest_mismatch")
    return verified


def vendored_trust_path() -> Path:
    """The path to the trust root vendored in the engine package (SPEC §8.7)."""
    return Path(__file__).resolve().parent / "data" / "trust" / "dev-root.json"


def vendored_trust() -> TrustRoot:
    """Load the trust root vendored in the engine package."""
    import json

    return TrustRoot.from_dict(json.loads(vendored_trust_path().read_text("utf-8")))


def load_trust_root(path: Path) -> TrustRoot:
    """Load a trust root from a JSON file an operator supplies (``--trust-root``, SPEC §8.7)."""
    import json

    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise VerificationError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VerificationError(f"{path} does not hold a trust-root object")
    try:
        return TrustRoot.from_dict(data)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        # `from_dict` assumes `keys`/`certificate_authorities` are objects and each entry is one too
        # (verifier round 2, 18.65): a tampered `keys: "x"` reaches `.items()` on a string, which is
        # `AttributeError`, not one of the JSON-decode-shaped errors already caught here.
        raise VerificationError(f"{path} is not a usable trust root: {exc}") from exc
