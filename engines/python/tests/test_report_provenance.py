"""The report's provenance is real, never a fixed or all-zero constant (SPEC §9.1): a real catalog
digest, the engine version and a reproduce command embedded in report.md/report.html, an end-to-end
signable bundle, and a real, deterministic, order-independent ``agentce diff``."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentce import __version__, bundled, signing
from agentce.canonical import canonicalize
from agentce.catalog import catalog_provenance_digest
from agentce.commands import cmd_diff, cmd_quickstart, cmd_sign

_ZERO_DIGEST_RE = re.compile(r"^sha256:0{64}$")


def _quickstart(out: Path) -> dict:
    cmd_quickstart(argparse.Namespace(out=str(out)))
    return json.loads((out / "manifest.json").read_text("utf-8"))


def test_manifest_catalog_digest_is_real_and_content_derived(tmp_path: Path) -> None:
    manifest = _quickstart(tmp_path / "out")
    catalogs = manifest["inputs"]["catalogs"]
    assert catalogs
    catalog_dir = bundled.catalogs_dir() / "base" / "eu-ai-act"
    expected = catalog_provenance_digest(catalog_dir)
    for entry in catalogs:
        assert not _ZERO_DIGEST_RE.match(entry["digest"])
        assert entry["digest"] == expected


def test_report_bodies_embed_engine_version_catalog_and_reproduce_command(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    manifest = _quickstart(out)
    cat = manifest["inputs"]["catalogs"][0]
    label = f"{cat['id']}@{cat['version']}"
    for name in ("report.md", "report.html"):
        text = (out / name).read_text("utf-8")
        assert __version__ in text
        assert label in text
        assert "agentce" in text and "quickstart" in text


def test_bundle_signs_verifies_detects_tamper_and_refuses_untrusted_root(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    _quickstart(out)

    signer_key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "signer.pem"
    key_path.write_bytes(
        signer_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cmd_sign(
        argparse.Namespace(
            report_dir=str(out),
            as_role="claimant",
            profile="kms",
            key=str(key_path),
            dry_run=False,
        )
    )

    sig_path = out / "signatures" / "claimant-kms.dsse.json"
    assert sig_path.is_file()
    envelope = json.loads(sig_path.read_text("utf-8"))
    claim = json.loads((out / "claim.json").read_text("utf-8"))
    manifest_bytes = (out / "manifest.json").read_bytes()

    pub = signer_key.public_key()
    keyid = signing.keyid_for(pub)
    good_root = signing.TrustRoot.from_dict(
        {
            "keys": {
                keyid: {
                    "public_key": signing.public_ed25519_b64(pub),
                    "identity": "test",
                }
            }
        }
    )
    verified = signing.verify_envelope(envelope, good_root)
    statement = json.loads(verified.payload)
    subjects = {s["name"]: s["digest"]["sha256"] for s in statement["subject"]}
    body = {k: v for k, v in claim.items() if k != "signatures"}
    assert subjects["claim.json"] == hashlib.sha256(canonicalize(body)).hexdigest()
    assert subjects["manifest.json"] == hashlib.sha256(manifest_bytes).hexdigest()

    # Tamper detection: a post-signature edit no longer matches the signed digest.
    tampered = manifest_bytes.replace(b'"operator"', b'"operatorX"', 1)
    assert tampered != manifest_bytes
    assert hashlib.sha256(tampered).hexdigest() != subjects["manifest.json"]

    # An untrusted root (does not hold the signing key) refuses the same envelope.
    stranger = Ed25519PrivateKey.generate()
    stranger_keyid = signing.keyid_for(stranger.public_key())
    bad_root = signing.TrustRoot.from_dict(
        {
            "keys": {
                stranger_keyid: {
                    "public_key": signing.public_ed25519_b64(stranger.public_key()),
                    "identity": "test-stranger",
                }
            }
        }
    )
    try:
        signing.verify_envelope(envelope, bad_root)
    except signing.VerificationError:
        pass
    else:
        raise AssertionError(
            "envelope verified against a trust root without the signing key"
        )


def test_diff_is_real_deterministic_and_order_independent(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _quickstart(out)
    a_path = out / "assertions.json"
    a = json.loads(a_path.read_text("utf-8"))

    b = json.loads(json.dumps(a))
    flipped = False
    for entry in b:
        if entry["control"] == "DAT-01":
            assert entry["outcome"] == "insufficient_evidence"
            entry["outcome"] = "non-conformant"
            entry["population"]["failed"] = 1
            flipped = True
            break
    assert flipped
    b_path = tmp_path / "b.json"
    b_path.write_text(json.dumps(b), encoding="utf-8")
    c_path = tmp_path / "c.json"
    c_path.write_text(json.dumps(list(reversed(b))), encoding="utf-8")

    def run(left: Path, right: Path) -> tuple[dict, int]:
        result = cmd_diff(argparse.Namespace(report_a=str(left), report_b=str(right)))
        return result.data, result.exit_code

    aa_data, aa_code = run(a_path, a_path)
    assert aa_code == 0
    assert aa_data["changed"] == 0

    ab1_data, ab1_code = run(a_path, b_path)
    assert ab1_code != 0
    changed = {
        (c["control"], c["subject"]): (c["from"], c["to"]) for c in ab1_data["diff"]
    }
    assert changed[("DAT-01", changed_subject(ab1_data))] == (
        "insufficient_evidence",
        "non-conformant",
    )

    ab2_data, _ = run(a_path, b_path)
    assert ab1_data["diff"] == ab2_data["diff"]

    ac_data, _ = run(a_path, c_path)
    assert ab1_data["diff"] == ac_data["diff"]


def changed_subject(data: dict) -> str:
    return next(c["subject"] for c in data["diff"] if c["control"] == "DAT-01")
