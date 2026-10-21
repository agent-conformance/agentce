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
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentce import cli, signing
from agentce.catalog import catalog_provenance_digest, load_catalog
from agentce.errors import InputError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
_EU_DIR = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
_EU_LABEL = "eu-ai-act@2026.09"
_CONDUCT_DIR = _REPO_ROOT / "spec" / "catalogs" / "overlays" / "conduct"


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


_MARKER = "CHANGED-ON-DISK-DURING-THE-RUN-18171"


def _race_after_read(
    monkeypatch: pytest.MonkeyPatch, control: Path, swap_in: bytes
) -> None:
    """A writer racing the run: the moment the engine has read the catalog, ``control`` on disk becomes
    ``swap_in``. A second read of the folder, by the signature check or the loader, would see it."""
    original = signing.read_catalog_files

    def racing(root: Path, **kwargs: Any) -> signing.CatalogFiles:
        files = original(root, **kwargs)
        if control.parent.parent == root:
            control.write_bytes(swap_in)
        return files

    monkeypatch.setattr(signing, "read_catalog_files", racing)


def _signed_with_edit(tmp_path: Path) -> tuple[Path, Path, bytes, bytes, Path]:
    key = Ed25519PrivateKey.generate()
    catalog = _copy_catalog(tmp_path / "testcat")
    _sign_catalog(catalog, key)
    control = catalog / "controls" / "DAT-01.yaml"
    signed = control.read_bytes()
    edited = signed.replace(b"Consequential decisions record", _MARKER.encode(), 1)
    assert edited != signed
    return catalog, control, signed, edited, _trust_root_file(tmp_path / "tr.json", key)


def _run_text(out: Path, envelope: dict[str, Any]) -> str:
    files = [f for f in out.rglob("*") if f.is_file()] if out.is_dir() else []
    return json.dumps(envelope) + "".join(f.read_text("utf-8", "ignore") for f in files)


def test_a_catalog_changed_after_the_read_is_assessed_from_the_bytes_that_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """18.171: the edit lands on disk after the engine read the catalog. The run verifies and
    assesses the bytes it read, so the edit reaches no output, and the packaged copy is those bytes."""
    catalog, control, signed, edited, root = _signed_with_edit(tmp_path)
    expected = catalog_provenance_digest(catalog)
    _race_after_read(monkeypatch, control, edited)
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
        "--package-for-sharing",
    )
    assert code in (0, 1), envelope
    assert control.read_bytes() == edited, "the race did not run"
    assert _MARKER not in _run_text(out, envelope)
    packaged = out / "bundle" / "catalog" / "0" / "controls" / "DAT-01.yaml"
    assert packaged.read_bytes() == signed
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert expected in json.dumps(manifest)


def test_a_catalog_put_back_after_the_read_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """18.171: the edit is on disk when the engine reads the catalog, and the signed bytes go back
    right after. The signature check reads the bytes the load read, so the run is refused."""
    catalog, control, signed, edited, root = _signed_with_edit(tmp_path)
    control.write_bytes(edited)
    _race_after_read(monkeypatch, control, signed)
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
    assert control.read_bytes() == signed, "the race did not run"
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unverified"
    assert not out.exists()


def test_verify_reads_the_files_it_is_given(tmp_path: Path) -> None:
    """The check over a load's files decides on those bytes, whatever the folder holds by then."""
    catalog, control, signed, edited, root = _signed_with_edit(tmp_path)
    trust = signing.load_trust_root(root)
    files = signing.read_catalog_files(catalog)
    control.write_bytes(edited)
    signing.verify_catalog_directory(files, trust)
    with pytest.raises(signing.VerificationError):
        signing.verify_catalog_directory(catalog, trust)
    edited_files = signing.read_catalog_files(catalog)
    control.write_bytes(signed)
    with pytest.raises(signing.VerificationError):
        signing.verify_catalog_directory(edited_files, trust)
    assert edited_files.digest() != files.digest()
    assert files.digest() == signing.digest_tree(
        catalog, exclude=frozenset({signing.CATALOG_SIGNATURE_NAME})
    )


def test_sign_signs_the_bytes_it_linted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """18.171: a control that would fail lint lands on disk after `catalog sign` read the catalog.
    The signature covers the bytes that were linted, so it never verifies the unlinted edit."""
    catalog = _copy_catalog(tmp_path / "testcat")
    control = catalog / "controls" / "DAT-01.yaml"
    linted = control.read_bytes()
    unlinted = linted + b"unknown_field_18171: 1\n"
    _race_after_read(monkeypatch, control, unlinted)
    code = cli.main(
        [
            "catalog",
            "sign",
            str(catalog),
            "--new-key",
            str(tmp_path / "k.pem"),
            "--write-trust-root",
            str(tmp_path / "tr.json"),
            "--json",
        ]
    )
    assert code == 0, capsys.readouterr().out
    assert control.read_bytes() == unlinted, "the race did not run"
    trust = signing.load_trust_root(tmp_path / "tr.json")
    with pytest.raises(signing.VerificationError):
        signing.verify_catalog_directory(catalog, trust)
    control.write_bytes(linted)
    signing.verify_catalog_directory(catalog, trust)


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


# --- 18.9 C3: the guided path (`catalog init` -> `lint --support-matrix` -> `sign --new-key
# --write-trust-root`) round-trips through `assess --catalog-dir` to a real outcome
# (contracts/P18-18.9.md). No new `assess` code -- this is the round-trip proof. ---


def _guided_signed_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[Path, Path, str]:
    """``catalog init`` -> ``catalog lint --support-matrix`` -> ``catalog sign --new-key
    --write-trust-root``, through the real C1/C2 CLI -- not a hand-built envelope."""
    directory = tmp_path / "own-rules-cat"
    capsys.readouterr()
    code = cli.main(["catalog", "init", str(directory), "--json"])
    env = json.loads(capsys.readouterr().out)
    assert code == 0, env
    family = str(env["family"])

    capsys.readouterr()
    code = cli.main(
        [
            "catalog",
            "lint",
            str(directory),
            "--support-matrix",
            str(tmp_path / "sm.json"),
            "--json",
        ]
    )
    lint_out = capsys.readouterr().out
    assert code == 0, json.loads(lint_out)

    key_path = tmp_path / "key.pem"
    trust_path = tmp_path / "trust.json"
    capsys.readouterr()
    code = cli.main(
        [
            "catalog",
            "sign",
            str(directory),
            "--new-key",
            str(key_path),
            "--write-trust-root",
            str(trust_path),
            "--json",
        ]
    )
    sign_out = capsys.readouterr().out
    assert code == 0, json.loads(sign_out)
    return directory, trust_path, family


def test_a_guided_catalog_round_trips_through_assess_to_a_real_outcome(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, trust_path, family = _guided_signed_catalog(tmp_path, capsys)
    out = tmp_path / "out"
    code, envelope = _assess(
        capsys, out, "--catalog-dir", str(directory), "--trust-root", str(trust_path)
    )
    assert code in (0, 1), envelope

    assertions = json.loads((out / "assertions.json").read_text("utf-8"))
    matches = [
        a
        for a in assertions
        if a["control"] == f"{family}-01"
        and a["subject"] == "spiffe://corp/agents/credit-langgraph"
    ]
    assert len(matches) == 1, assertions
    assert matches[0]["outcome"] == "conformant"

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert "limitations" not in manifest
    catalog_refs = manifest["inputs"]["catalogs"]
    assert any(ref["id"] == "own-rules-cat" for ref in catalog_refs), catalog_refs


def test_a_guided_catalog_without_a_trust_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator's own key is, correctly, not the vendored dev-trust root: omitting
    ``--trust-root`` (with ``AGENTCE_TRUST_ROOT`` explicitly unset, so this is not flaky under a
    developer's or CI's ambient env) falls back to the vendored root and refuses."""
    monkeypatch.delenv("AGENTCE_TRUST_ROOT", raising=False)
    directory, _trust_path, _family = _guided_signed_catalog(tmp_path, capsys)
    out = tmp_path / "out"
    code, envelope = _assess(capsys, out, "--catalog-dir", str(directory))
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unverified"
    assert not out.exists()


def _link_out(catalog: Path, rel: str, outside: Path) -> None:
    """Move ``rel`` out of ``catalog`` and link it back: the same bytes, now kept elsewhere."""
    (catalog / rel).rename(outside)
    (catalog / rel).symlink_to(outside)


@pytest.mark.parametrize("rel", ["controls/DOC-01.yaml", "shapes/DOC-01.ttl"])
def test_a_signed_catalog_file_linked_outside_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], rel: str
) -> None:
    """18.181 (R4, R5): a signed catalog is one folder of files. A covered control or shape moved out
    and linked back after signing has the same digest, and is still refused; the override assesses it
    and records why, as for any catalog that does not verify."""
    key = Ed25519PrivateKey.generate()
    catalog = _copy_catalog(tmp_path / "testcat")
    _sign_catalog(catalog, key)
    _link_out(catalog, rel, tmp_path / "moved")
    trust = _trust_root_file(tmp_path / "tr.json", key)
    with pytest.raises(signing.VerificationError) as raised:
        signing.verify_catalog_directory(catalog, signing.load_trust_root(trust))
    assert raised.value.keys == ("catalog.link_outside",)
    code, envelope = _assess(
        capsys,
        tmp_path / "out",
        "--catalog-dir",
        str(catalog),
        "--trust-root",
        str(trust),
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unverified"
    assert (
        f"the catalog file or folder {rel} is a link to a place outside"
        in envelope["error"]["cause"]
    )
    code, envelope = _assess(
        capsys,
        tmp_path / "out2",
        "--catalog-dir",
        str(catalog),
        "--trust-root",
        str(trust),
        "--allow-unverified-catalog",
    )
    assert code == 0
    assert any("link to a place outside" in item for item in envelope["limitations"])


def test_a_signed_catalog_file_linked_inside_is_followed(tmp_path: Path) -> None:
    """18.181: a link to another file in the same catalog leaves nothing outside it, so it verifies."""
    key = Ed25519PrivateKey.generate()
    catalog = _copy_catalog(tmp_path / "testcat")
    (catalog / "controls" / "DOC-01.yaml").rename(catalog / "test" / "DOC-01.yaml.txt")
    (catalog / "controls" / "DOC-01.yaml").symlink_to("../test/DOC-01.yaml.txt")
    _sign_catalog(catalog, key)
    trust = signing.load_trust_root(_trust_root_file(tmp_path / "tr.json", key))
    signing.verify_catalog_directory(catalog, trust)


def test_an_absolute_inside_link_through_another_spelling_is_followed(
    tmp_path: Path,
) -> None:
    """18.181 (verifier r1): containment is judged by folder identity, not spelling. On a
    case-insensitive filesystem, a control that is an absolute link to a file inside the catalog,
    reached through the catalog path in another case, still verifies, and its evidence shape too."""
    folded = Path(str(tmp_path).upper())
    if not folded.exists() or folded == tmp_path:
        pytest.skip("needs a case-insensitive filesystem")
    key = Ed25519PrivateKey.generate()
    catalog = _copy_catalog(tmp_path / "testcat")
    inside = catalog / "test" / "DOC-01.yaml.txt"
    (catalog / "controls" / "DOC-01.yaml").rename(inside)
    (catalog / "controls" / "DOC-01.yaml").symlink_to(Path(str(inside).upper()))
    _sign_catalog(catalog, key)
    trust = signing.load_trust_root(_trust_root_file(tmp_path / "tr.json", key))
    for spelled in (catalog, Path(str(catalog).upper())):
        files = signing.read_catalog_files(spelled)
        assert files.links_out == frozenset()
        signing.verify_catalog_directory(spelled, trust)
    assert signing.inside_folder(catalog, Path(str(inside).upper()))
    assert not signing.inside_folder(catalog / "controls", inside)


def test_catalog_sign_and_provenance_lint_refuse_a_control_linked_outside(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """18.181 (R6): `catalog sign` and `catalog lint --require-provenance` refuse a control that
    links outside the catalog; plain lint and an unsigned assess follow the link, as before."""
    catalog = _copy_catalog(tmp_path / "testcat")
    _link_out(catalog, "controls/DOC-01.yaml", tmp_path / "moved.yaml")
    code = cli.main(
        [
            "catalog",
            "sign",
            str(catalog),
            "--new-key",
            str(tmp_path / "k.pem"),
            "--json",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert (code, envelope["error"]["key"]) == (3, "catalog.link_outside")
    assert not (catalog / signing.CATALOG_SIGNATURE_NAME).exists()
    assert not (tmp_path / "k.pem").exists()
    code = cli.main(["catalog", "lint", str(catalog), "--require-provenance", "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 1
    assert envelope["problems"] == [
        "catalog.link_outside: the catalog file or folder controls/DOC-01.yaml is a link to a place outside"
        " the catalog folder. A signed catalog must hold its own files, because a file kept"
        " elsewhere is not handed over with the catalog."
    ]
    assert cli.main(["catalog", "lint", str(catalog), "--json"]) == 0
    capsys.readouterr()
    code, _ = _assess(
        capsys,
        tmp_path / "out",
        "--catalog-dir",
        str(catalog),
        "--allow-unverified-catalog",
    )
    assert code == 0


@pytest.mark.parametrize("command", ["assess", "lint", "sign"])
def test_a_catalog_whose_control_is_a_fifo_is_refused_unopened(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    """18.181: a control file that is a FIFO is refused naming it, never dropped (the run would
    assess, lint pass and sign sign the catalog without that control) and never opened."""
    catalog = _copy_catalog(tmp_path / "testcat")
    (catalog / "controls" / "DOC-01.yaml").unlink()
    os.mkfifo(catalog / "controls" / "DOC-01.yaml")
    if command == "assess":
        code, envelope = _assess(
            capsys,
            tmp_path / "out",
            "--catalog-dir",
            str(catalog),
            "--allow-unverified-catalog",
        )
        assert (code, envelope["error"]["key"]) == (3, "input.catalog_unreadable")
        assert envelope["error"]["cause"].endswith(
            "cannot be read: controls/DOC-01.yaml."
        )
    elif command == "lint":
        assert cli.main(["catalog", "lint", str(catalog), "--json"]) == 1
        problems = json.loads(capsys.readouterr().out)["problems"]
        assert problems[0].startswith("input.catalog_unreadable: ")
    else:
        code = cli.main(
            [
                "catalog",
                "sign",
                str(catalog),
                "--new-key",
                str(tmp_path / "k.pem"),
                "--json",
            ]
        )
        envelope = json.loads(capsys.readouterr().out)
        assert (code, envelope["error"]["key"]) == (3, "catalog.sign_lint_failed")
        assert not (catalog / signing.CATALOG_SIGNATURE_NAME).exists()


def test_a_catalog_yaml_that_is_a_fifo_is_refused_unopened(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """18.181: a FIFO named catalog.yaml is refused as unreadable instead of blocking the read."""
    catalog = _copy_catalog(tmp_path / "testcat")
    (catalog / "catalog.yaml").unlink()
    os.mkfifo(catalog / "catalog.yaml")
    code, envelope = _assess(capsys, tmp_path / "out", "--catalog-dir", str(catalog))
    assert (code, envelope["error"]["key"]) == (3, "input.catalog_unreadable")
    assert envelope["error"]["cause"].endswith("cannot be read: catalog.yaml.")


def test_a_signed_evidence_shape_linked_outside_is_refused(tmp_path: Path) -> None:
    """18.181 (R5): a control's evaluation.evidence_shape file, moved out of a signed catalog and
    linked back, is refused: the load by its own containment check (as at base), and the catalog
    verification with catalog.link_outside, as for control and shape files."""
    key = Ed25519PrivateKey.generate()
    catalog = tmp_path / "conduct"
    shutil.copytree(_CONDUCT_DIR, catalog)
    _sign_catalog(catalog, key)
    _link_out(catalog, "shapes/CND-02-evidence.ttl", tmp_path / "moved.ttl")
    trust = signing.load_trust_root(_trust_root_file(tmp_path / "tr.json", key))
    with pytest.raises(InputError) as refused:
        load_catalog(catalog)
    assert refused.value.key == "catalog.evidence_shape.unresolved"
    with pytest.raises(signing.VerificationError) as raised:
        signing.verify_catalog_directory(catalog, trust)
    assert raised.value.keys == ("catalog.link_outside",)


@pytest.mark.parametrize("command", ["assess", "lint", "sign"])
def test_a_folder_named_like_a_control_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    """18.181 (critic r1 B1): a folder where the loader reads a control (controls/<name>.yaml) is
    refused naming it; the walk would enter it and the control would vanish."""
    catalog = _copy_catalog(tmp_path / "testcat")
    (catalog / "controls" / "DOC-01.yaml").unlink()
    (catalog / "controls" / "DOC-01.yaml").mkdir()
    if command == "assess":
        code, envelope = _assess(
            capsys,
            tmp_path / "out",
            "--catalog-dir",
            str(catalog),
            "--allow-unverified-catalog",
        )
        assert (code, envelope["error"]["key"]) == (3, "input.catalog_unreadable")
        assert envelope["error"]["cause"].endswith(
            "cannot be read: controls/DOC-01.yaml."
        )
    elif command == "lint":
        assert cli.main(["catalog", "lint", str(catalog), "--json"]) == 1
        problems = json.loads(capsys.readouterr().out)["problems"]
        assert problems[0].startswith("input.catalog_unreadable: ")
    else:
        argv = ["catalog", "sign", str(catalog), "--new-key", str(tmp_path / "k.pem")]
        assert cli.main([*argv, "--json"]) == 3
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["error"]["key"] == "catalog.sign_lint_failed"
        assert not (catalog / signing.CATALOG_SIGNATURE_NAME).exists()


@pytest.mark.parametrize("kind", ["fifo", "folder"])
def test_catalog_lint_reads_a_non_file_catalog_yaml_as_unreadable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """18.181 (critic r1 D4): a catalog.yaml that is there but is not a file is unreadable, never
    reported as absent."""
    catalog = _copy_catalog(tmp_path / "testcat")
    (catalog / "catalog.yaml").unlink()
    if kind == "fifo":
        os.mkfifo(catalog / "catalog.yaml")
    else:
        (catalog / "catalog.yaml").mkdir()
    assert cli.main(["catalog", "lint", str(catalog), "--json"]) == 1
    problems = json.loads(capsys.readouterr().out)["problems"]
    assert problems == [
        f"input.catalog_unreadable: the catalog directory {catalog} holds a file or folder"
        " that cannot be read: catalog.yaml."
    ]


def _readiness(
    capsys: pytest.CaptureFixture[str], report: Path, *extra: str
) -> tuple[int, dict[str, Any]]:
    code = cli.main(["readiness", str(report), *extra, "--json"])
    stdout = capsys.readouterr().out
    return code, json.loads(stdout) if stdout.strip() else {}


def test_readiness_refuses_a_catalog_edited_after_signing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """readiness verifies --catalog-dir as assess does, so a severity lowered after signing never
    reaches the verdict (inbox row 354, 18.184h)."""
    key = Ed25519PrivateKey.generate()
    report = tmp_path / "report"
    assert _assess(capsys, report)[0] in (0, 1)
    catalog = _copy_catalog(tmp_path / "testcat")
    _sign_catalog(catalog, key)
    trust_root = str(_trust_root_file(tmp_path / "trust-root.json", key))
    code, _ = _readiness(
        capsys, report, "--catalog-dir", str(catalog), "--trust-root", trust_root
    )
    assert code in (0, 1)
    control = catalog / "controls" / "DOC-01.yaml"
    control.write_text(
        control.read_text("utf-8").replace("severity: high", "severity: low"),
        encoding="utf-8",
    )
    for written in report.glob("report-readiness-*.md"):
        written.unlink()
    code, envelope = _readiness(
        capsys, report, "--catalog-dir", str(catalog), "--trust-root", trust_root
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unverified"
    assert not list(report.glob("report-readiness-*.md"))


def test_readiness_override_records_the_unverified_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "report"
    assert _assess(capsys, report)[0] in (0, 1)
    catalog = _copy_catalog(tmp_path / "testcat")
    code, envelope = _readiness(capsys, report, "--catalog-dir", str(catalog))
    assert (code, envelope["error"]["key"]) == (3, "input.catalog_unverified")
    code, verdict = _readiness(
        capsys, report, "--catalog-dir", str(catalog), "--allow-unverified-catalog"
    )
    assert code in (0, 1)
    assert verdict["verdict"] != "READY"
    assert any("testcat" in line for line in verdict["limitations"])


@pytest.mark.parametrize(
    "extra",
    [
        ["--trust-root", ""],
        ["--trust-root", "/nonexistent/trust-root.json"],
        ["--allow-unverified-catalog"],
    ],
)
def test_readiness_trust_options_without_a_catalog_dir_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    report = tmp_path / "report"
    assert _assess(capsys, report)[0] in (0, 1)
    code, envelope = _readiness(capsys, report, *extra)
    assert code == 3
    assert envelope["error"]["key"] in {
        "input.trust_root_not_a_file",
        "input.allow_unverified_requires_catalog_dir",
    }
