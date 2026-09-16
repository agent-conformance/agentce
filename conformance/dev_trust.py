"""The repository's development trust material (SPEC §8.7, §9.1) — the signing side.

Production releases and catalogs are signed with real Sigstore/KMS identities held by the maintainer.
For the repository's *own* bundled artifacts — the base catalogs, the corpus, and the dry-run release
— this module holds a **published, non-secret development seed** and derives deterministic Ed25519
keys from it. This mirrors the specification's own pattern of a *published test key* for the corpus
(SPEC §6.7): the derived private keys are reproducible by anyone and are therefore emphatically not a
production credential; they sign only fixtures whose trust root is the vendored development root.

The engine never holds this seed. It vendors only the *public* root (:func:`public_root`, written to
``engines/python/agentce/data/trust/dev-root.json``) so that ``agentce verify`` runs fully offline; a
test asserts the two never drift. The dry-run release tooling (:mod:`release`) imports the derived
signers to exercise the three signing profiles.

Run the maintenance commands after changing the seed or a signed catalog::

    uv run python dev_trust.py emit-root
    uv run python dev_trust.py sign-catalog ../spec/catalogs/base/eu-ai-act
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentce import signing
from agentce.signing import (
    KeylessSigner,
    KmsSigner,
    issue_certificate,
    keyid_for,
    public_ed25519_b64,
    sign_statement,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDORED_ROOT = (
    REPO_ROOT / "engines" / "python" / "agentce" / "data" / "trust" / "dev-root.json"
)

#: Published, non-production seed. Documented here and in docs/verification.md; deriving a key from it
#: is deterministic and open to anyone, which is the point: these keys sign only repository fixtures.
_SEED = b"agentce.dev-trust.v1 PUBLISHED NON-PRODUCTION SIGNING SEED"

#: Fixed validity window for the development keyless certificates (deterministic, not wall-clock).
_NOT_BEFORE = "2026-01-01T00:00:00.000Z"
_NOT_AFTER = "2027-01-01T00:00:00.000Z"

# Signer identities the development root vouches for.
CATALOG_IDENTITY = "agentce-release (development catalog-signing key)"
KMS_IDENTITY = "kms://agent-conformance/dev/release"
CI_PRIVATE_IDENTITY = "https://agent-conformance.org/ci/release (development)"
CI_PUBLIC_IDENTITY = "https://github.com/agent-conformance/agentce/.github/workflows/release.yml@refs/tags/dev"

# Certificate-authority issuer ids for the two keyless profiles.
CA_PRIVATE = "agentce-dev-fulcio-private"
CA_PUBLIC = "agentce-dev-fulcio-public"


def derive(label: str) -> Ed25519PrivateKey:
    """Deterministically derive an Ed25519 private key from the published seed and a label."""
    seed = hashlib.sha256(_SEED + b"|" + label.encode("utf-8")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def catalog_key() -> Ed25519PrivateKey:
    return derive("catalog-signing")


def kms_key() -> Ed25519PrivateKey:
    return derive("release-kms")


def ca_key(issuer: str) -> Ed25519PrivateKey:
    return derive(f"fulcio-ca:{issuer}")


def public_root() -> dict[str, Any]:
    """Build the public development trust root (the exact bytes vendored in the engine)."""
    catalog_pub = catalog_key().public_key()
    kms_pub = kms_key().public_key()
    return {
        "description": (
            "PUBLISHED development trust root. Verifies the repository's own bundled catalogs, "
            "corpora, and dry-run release artifacts, which are signed with keys derived from a "
            "published non-production seed (see conformance/dev_trust.py and docs/verification.md). "
            "It is NOT the public Sigstore TUF root; a production deployment configures its own root."
        ),
        "version": 1,
        "keys": {
            keyid_for(catalog_pub): {
                "algorithm": "ed25519",
                "public_key": public_ed25519_b64(catalog_pub),
                "identity": CATALOG_IDENTITY,
                "purpose": "catalog-and-corpus-signing",
            },
            keyid_for(kms_pub): {
                "algorithm": "ed25519",
                "public_key": public_ed25519_b64(kms_pub),
                "identity": KMS_IDENTITY,
                "purpose": "kms-release-signing",
            },
        },
        "certificate_authorities": {
            CA_PRIVATE: {
                "algorithm": "ed25519",
                "public_key": public_ed25519_b64(ca_key(CA_PRIVATE).public_key()),
                "purpose": "sigstore-private keyless certificate authority",
            },
            CA_PUBLIC: {
                "algorithm": "ed25519",
                "public_key": public_ed25519_b64(ca_key(CA_PUBLIC).public_key()),
                "purpose": "sigstore-public keyless certificate authority (development)",
            },
        },
    }


def release_signer(profile: str) -> signing.Signer:
    """A development signer for a release signing profile (SPEC §9.1)."""
    if profile == "kms":
        return KmsSigner(private_key=kms_key())
    if profile in ("sigstore-private", "sigstore-public"):
        issuer = CA_PRIVATE if profile == "sigstore-private" else CA_PUBLIC
        identity = (
            CI_PRIVATE_IDENTITY if profile == "sigstore-private" else CI_PUBLIC_IDENTITY
        )
        # Keyless: a fresh ephemeral key, certified by the development authority.
        ephemeral = Ed25519PrivateKey.generate()
        cert = issue_certificate(
            ca_key(issuer),
            issuer=issuer,
            identity=identity,
            leaf_public=ephemeral.public_key(),
            not_before=_NOT_BEFORE,
            not_after=_NOT_AFTER,
        )
        return KeylessSigner(private_key=ephemeral, cert=cert)
    raise ValueError(f"unknown signing profile {profile!r}")


#: The signature file dropped into a signed catalog directory (defined by the engine).
SIGNATURE_NAME = signing.CATALOG_SIGNATURE_NAME


def catalog_signature(catalog_dir: Path) -> dict[str, Any]:
    """Sign a catalog directory's content digest; return the DSSE envelope (deterministic)."""
    digest = signing.digest_tree(catalog_dir, exclude=frozenset({SIGNATURE_NAME}))
    statement = signing.intoto_statement(
        subject_name=catalog_dir.name,
        digest=digest,
        predicate_type="https://agent-conformance.org/attestation/catalog/v1",
        predicate={"kind": "catalog", "id": catalog_dir.name},
    )
    return sign_statement(statement, KmsSigner(private_key=catalog_key()))


def _emit_root() -> int:
    VENDORED_ROOT.parent.mkdir(parents=True, exist_ok=True)
    VENDORED_ROOT.write_text(
        json.dumps(public_root(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {VENDORED_ROOT.relative_to(REPO_ROOT)}")
    return 0


def _sign_catalog(catalog_dir: Path) -> int:
    catalog_dir = catalog_dir.resolve()
    if not (catalog_dir / "catalog.yaml").is_file():
        print(f"not a catalog directory: {catalog_dir}", file=sys.stderr)
        return 1
    envelope = catalog_signature(catalog_dir)
    out = catalog_dir / SIGNATURE_NAME
    out.write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_trust", description="Development trust material (SPEC §8.7, §9.1)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("emit-root", help="write the vendored public development trust root")
    sign = sub.add_parser("sign-catalog", help="sign a catalog directory")
    sign.add_argument("catalog", type=Path, help="the catalog directory")
    args = parser.parse_args(argv)
    if args.cmd == "emit-root":
        return _emit_root()
    if args.cmd == "sign-catalog":
        return _sign_catalog(args.catalog)
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
