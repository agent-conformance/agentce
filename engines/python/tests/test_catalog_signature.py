"""``assess`` verifies every ``--catalog-dir`` catalog before it evaluates anything (SPEC §8.7).

A catalog directory an operator points the engine at is untrusted input. Before a single control
runs, its detached ``catalog.sig.json`` must verify against the effective trust root — ``--trust-root``
if given, else ``AGENTCE_TRUST_ROOT``, else the trust root vendored in the engine — and the id and
version it carries must be one the run asked for. Unsigned, signed by a key the root does not know,
or validly signed but rebranded: each is refused at exit 3 with a stable ``input.catalog*`` key and
nothing is written.

Every test here builds its own throwaway Ed25519 key and trust root inline and signs a copy of the
committed base catalog with it, so no test depends on the repository's own signing material, on the
order the tests run in, on the clock, or on a network.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentce import cli, signing
from agentce.catalog import catalog_provenance_digest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
_EU_DIR = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
_EU_LABEL = "eu-ai-act@2026.09"


def _copy_catalog(dest: Path) -> Path:
    """A copy of the committed base catalog with its own signature removed."""
    shutil.copytree(_EU_DIR, dest)
    (dest / signing.CATALOG_SIGNATURE_NAME).unlink()
    return dest


def _sign_catalog(directory: Path, key: Ed25519PrivateKey) -> None:
    """Sign ``directory``'s content digest with ``key``, as a catalog publisher would."""
    statement = signing.intoto_statement(
        subject_name=directory.name,
        digest=signing.digest_tree(
            directory, exclude=frozenset({signing.CATALOG_SIGNATURE_NAME})
        ),
        predicate_type="https://agent-conformance.org/attestation/catalog/v1",
        predicate={"kind": "catalog", "id": directory.name},
    )
    envelope = signing.sign_statement(statement, signing.KmsSigner(private_key=key))
    (directory / signing.CATALOG_SIGNATURE_NAME).write_text(
        json.dumps(envelope, indent=2), encoding="utf-8"
    )


def _rebrand(directory: Path, new_id: str = "eu-ai-act-rebrand") -> Path:
    """Give the catalog a different id and recompute its provenance digest to match, exactly as a
    rebrander keeping ``catalog lint --require-provenance`` green would."""
    meta = directory / "catalog.yaml"
    renamed = re.sub(
        r"(?m)^id:\s*eu-ai-act\s*$", f"id: {new_id}", meta.read_text("utf-8"), count=1
    )
    assert "id: " + new_id in renamed, "the id: line was not replaced"
    meta.write_text(renamed, encoding="utf-8")
    meta.write_text(
        re.sub(
            r"(?m)^(\s*digest:\s*)sha256:[0-9a-f]+\s*$",
            rf"\g<1>{catalog_provenance_digest(directory)}",
            meta.read_text("utf-8"),
            count=1,
        ),
        encoding="utf-8",
    )
    return directory


def _trust_root_file(path: Path, key: Ed25519PrivateKey) -> Path:
    """Write a trust root, in the vendored root's own form, that pins ``key`` and nothing else."""
    public = key.public_key()
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "description": "TEST trust root; not production.",
                "keys": {
                    signing.keyid_for(public): {
                        "algorithm": "ed25519",
                        "public_key": signing.public_ed25519_b64(public),
                        "identity": "test://trusted-signer",
                        "purpose": "test-fixture-catalog-signing",
                    }
                },
                "certificate_authorities": {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _assess(
    capsys: pytest.CaptureFixture[str], out: Path, *extra: str
) -> tuple[int, dict[str, Any]]:
    code = cli.main(
        [
            "assess",
            "--bundle",
            str(_QUICKSTART / "evidence"),
            "--profile",
            str(_QUICKSTART / "applicability.yaml"),
            "--domain",
            str(_QUICKSTART / "domain.linkml.yaml"),
            *extra,
            "--out",
            str(out),
            "--json",
        ]
    )
    stdout = capsys.readouterr().out
    return code, json.loads(stdout) if stdout.strip() else {}


def test_a_catalog_signed_under_the_supplied_trust_root_is_assessed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C1 arm 1: the flag is consulted, not merely parsed — a catalog the *supplied* root trusts runs."""
    key = Ed25519PrivateKey.generate()
    catalog = _copy_catalog(tmp_path / "testcat")
    _sign_catalog(catalog, key)
    root = _trust_root_file(tmp_path / "trust-root.json", key)
    out = tmp_path / "out"
    code, envelope = _assess(
        capsys,
        out,
        "--catalog",
        _EU_LABEL,
        "--catalog-dir",
        str(catalog),
        "--trust-root",
        str(root),
    )
    assert code == 0, envelope
    assert envelope["catalogs"] == [_EU_LABEL]
    assert len(json.loads((out / "assertions.json").read_text("utf-8"))) > 0


def test_the_trust_root_environment_variable_is_honored_like_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same catalog, the same root, named through AGENTCE_TRUST_ROOT instead of --trust-root."""
    key = Ed25519PrivateKey.generate()
    catalog = _copy_catalog(tmp_path / "testcat")
    _sign_catalog(catalog, key)
    root = _trust_root_file(tmp_path / "trust-root.json", key)
    monkeypatch.setenv("AGENTCE_TRUST_ROOT", str(root))
    code, envelope = _assess(
        capsys, tmp_path / "out", "--catalog", _EU_LABEL, "--catalog-dir", str(catalog)
    )
    assert code == 0, envelope
    assert envelope["catalogs"] == [_EU_LABEL]


def test_the_flag_wins_over_the_environment_variable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--trust-root`` is what the operator typed for this run; the environment does not override it."""
    trusted = Ed25519PrivateKey.generate()
    catalog = _copy_catalog(tmp_path / "testcat")
    _sign_catalog(catalog, trusted)
    monkeypatch.setenv(
        "AGENTCE_TRUST_ROOT",
        str(
            _trust_root_file(tmp_path / "other-root.json", Ed25519PrivateKey.generate())
        ),
    )
    code, envelope = _assess(
        capsys,
        tmp_path / "out",
        "--catalog",
        _EU_LABEL,
        "--catalog-dir",
        str(catalog),
        "--trust-root",
        str(_trust_root_file(tmp_path / "trust-root.json", trusted)),
    )
    assert code == 0, envelope


def test_a_catalog_untrusted_under_the_effective_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C1 arm 2: the same catalog, no --trust-root — the default root does not know the test key."""
    catalog = _copy_catalog(tmp_path / "testcat")
    _sign_catalog(catalog, Ed25519PrivateKey.generate())
    out = tmp_path / "out"
    code, envelope = _assess(
        capsys, out, "--catalog", _EU_LABEL, "--catalog-dir", str(catalog)
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unverified"
    assert (
        not out.exists()
    )  # refused before anything that looks like a result is written


def test_an_unsigned_catalog_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C4: the signature file is simply absent — there is nothing to verify, so nothing is assessed."""
    catalog = _copy_catalog(tmp_path / "unsignedcat")
    out = tmp_path / "out"
    code, envelope = _assess(
        capsys, out, "--catalog", _EU_LABEL, "--catalog-dir", str(catalog)
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unverified"
    assert not out.exists()


def test_the_override_assesses_an_unsigned_catalog_and_records_the_limitation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--allow-unverified-catalog`` (SPEC §8.7) is the operator's explicit override: the run
    proceeds, and both the manifest and the claim carry the override as a limitation so no reader
    mistakes the report for one produced from verified inputs."""
    catalog = _copy_catalog(tmp_path / "unsignedcat")
    out = tmp_path / "out"
    code, envelope = _assess(
        capsys,
        out,
        "--catalog",
        _EU_LABEL,
        "--catalog-dir",
        str(catalog),
        "--allow-unverified-catalog",
    )
    assert code in (0, 1), (
        envelope
    )  # assessed; 1 would be a real finding, never a refusal
    assert len(json.loads((out / "assertions.json").read_text("utf-8"))) > 0

    limitations = envelope["limitations"]
    assert len(limitations) == 1
    assert "unverified" in limitations[0]
    assert _EU_LABEL in limitations[0]
    assert signing.CATALOG_SIGNATURE_NAME in limitations[0]

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["limitations"] == limitations
    claim = json.loads((out / "claim.json").read_text("utf-8"))
    assert claim["limitations"] == limitations

    # The report still validates against the vendored schemas with the new field present.
    from agentce.report import validate_report

    assert validate_report(out) == []


def test_an_ordinary_run_records_no_limitation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The field is absent — not an empty array — when nothing was waived."""
    out = tmp_path / "out"
    code, envelope = _assess(
        capsys, out, "--catalog", _EU_LABEL, "--catalog-dir", str(_EU_DIR)
    )
    assert code == 0, envelope
    assert "limitations" not in envelope
    assert "limitations" not in json.loads((out / "manifest.json").read_text("utf-8"))
    assert "limitations" not in json.loads((out / "claim.json").read_text("utf-8"))


def test_the_override_does_not_excuse_a_catalog_that_is_not_the_one_requested(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The override covers SPEC §8.7's "unsigned or unverifiable" and nothing more: a rebranded
    catalog is refused with ``input.catalog_mismatch`` even with the flag set, whether its signature
    verifies or is absent entirely."""
    key = Ed25519PrivateKey.generate()
    for name, sign in (("rebranded-signed", True), ("rebranded-unsigned", False)):
        catalog = _rebrand(_copy_catalog(tmp_path / name))
        if sign:
            _sign_catalog(catalog, key)
        out = tmp_path / f"out-{name}"
        code, envelope = _assess(
            capsys,
            out,
            "--catalog",
            _EU_LABEL,
            "--catalog-dir",
            str(catalog),
            "--trust-root",
            str(_trust_root_file(tmp_path / "trust-root.json", key)),
            "--allow-unverified-catalog",
        )
        assert code == 3, (name, envelope)
        assert envelope["error"]["key"] == "input.catalog_mismatch", name
        assert not out.exists(), name


def test_a_catalog_whose_content_changed_after_signing_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A signature over other bytes is not a signature over these: the digest must match the content."""
    key = Ed25519PrivateKey.generate()
    catalog = _copy_catalog(tmp_path / "testcat")
    _sign_catalog(catalog, key)
    control = sorted((catalog / "controls").glob("*.yaml"))[0]
    control.write_text(
        control.read_text("utf-8") + "\n# swapped after signing\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    code, envelope = _assess(
        capsys,
        out,
        "--catalog",
        _EU_LABEL,
        "--catalog-dir",
        str(catalog),
        "--trust-root",
        str(_trust_root_file(tmp_path / "trust-root.json", key)),
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unverified"
    assert not out.exists()


def test_a_validly_signed_but_rebranded_catalog_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C2: the signature verifies and the provenance digest is self-consistent — only the identity
    differs from what was requested, and that alone must stop the run."""
    key = Ed25519PrivateKey.generate()
    catalog = _rebrand(_copy_catalog(tmp_path / "rebranded"))
    _sign_catalog(catalog, key)
    root = _trust_root_file(tmp_path / "trust-root.json", key)
    out = tmp_path / "out"
    code, envelope = _assess(
        capsys,
        out,
        "--catalog",
        _EU_LABEL,
        "--catalog-dir",
        str(catalog),
        "--trust-root",
        str(root),
    )
    assert code == 3
    assert envelope["error"]["key"].startswith("input.catalog")
    assert envelope["error"]["key"] == "input.catalog_mismatch"
    assert "eu-ai-act-rebrand@2026.09" in envelope["error"]["cause"]
    assert not out.exists()

    # The signature itself is sound: only the identity was wrong.
    assert (
        signing.verify_catalog_directory(
            catalog, signing.load_trust_root(root)
        ).identity
        == "test://trusted-signer"
    )


def test_a_trust_root_that_does_not_exist_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, envelope = _assess(
        capsys,
        tmp_path / "out",
        "--catalog",
        _EU_LABEL,
        "--catalog-dir",
        str(_EU_DIR),
        "--trust-root",
        str(tmp_path / "absent.json"),
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.trust_root_not_a_file"


def test_a_malformed_trust_root_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "trust-root.json"
    root.write_text("{not json", encoding="utf-8")
    code, envelope = _assess(
        capsys,
        tmp_path / "out",
        "--catalog",
        _EU_LABEL,
        "--catalog-dir",
        str(_EU_DIR),
        "--trust-root",
        str(root),
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.trust_root_invalid"


def test_the_committed_base_catalogs_verify_under_the_vendored_root() -> None:
    """The base catalogs the engine ships are signed, so ``--catalog-dir`` needs no flag for them —
    the quickstart, the corpus runner, and the documented examples all rely on that."""
    trust = signing.vendored_trust()
    for root in (
        _REPO_ROOT / "spec" / "catalogs" / "base",
        Path(signing.__file__).parent / "data" / "catalogs" / "base",
    ):
        catalogs = sorted(root.glob("*/catalog.yaml"))
        assert catalogs, f"no committed catalogs under {root}"
        for meta in catalogs:
            signing.verify_catalog_directory(meta.parent, trust)
