"""The three ``verify`` targets: --bundle, --catalog, and --release (SPEC §8.5, §8.7).

Catalog and release verification run offline against a trust root. These tests build their own
ephemeral trust root and signed material and monkeypatch :func:`agentce.signing.vendored_trust`, so
they exercise the real verification path without depending on the repository's committed keys.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_commands import _AUD_FIXTURE, _packaged_and_signed

from agentce import cli, signing


def run(
    argv: Sequence[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _trust_for(key: Ed25519PrivateKey) -> signing.TrustRoot:
    keyid = signing.keyid_for(key.public_key())
    return signing.TrustRoot(
        keys={keyid: key.public_key()},
        key_identities={keyid: "test-signer"},
    )


def _sign_catalog(catalog_dir: Path, key: Ed25519PrivateKey) -> None:
    digest = signing.digest_tree(
        catalog_dir, exclude=frozenset({signing.CATALOG_SIGNATURE_NAME})
    )
    statement = signing.intoto_statement(
        catalog_dir.name,
        digest,
        "https://agent-conformance.org/attestation/catalog/v1",
        {},
    )
    envelope = signing.sign_statement(statement, signing.KmsSigner(private_key=key))
    (catalog_dir / signing.CATALOG_SIGNATURE_NAME).write_text(
        json.dumps(envelope), encoding="utf-8"
    )


def test_verify_catalog_signed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signing, "vendored_trust", lambda: _trust_for(key))
    (tmp_path / "catalog.yaml").write_text("id: demo\n", encoding="utf-8")
    _sign_catalog(tmp_path, key)
    code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    assert code == 0
    assert env["verified"] is True
    assert env["signer"] == "test-signer"
    assert env["digest"].startswith("sha256:")


def test_verify_catalog_unsigned_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "catalog.yaml").write_text("id: demo\n", encoding="utf-8")
    code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False


def test_verify_catalog_tampered_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signing, "vendored_trust", lambda: _trust_for(key))
    (tmp_path / "catalog.yaml").write_text("id: demo\n", encoding="utf-8")
    _sign_catalog(tmp_path, key)
    (tmp_path / "catalog.yaml").write_text("id: tampered\n", encoding="utf-8")
    code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False


def test_verify_release_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signing, "vendored_trust", lambda: _trust_for(key))
    statement = signing.intoto_statement(
        "release",
        "sha256:" + "0" * 64,
        "https://agent-conformance.org/attestation/release/v1",
        {},
    )
    envelope = signing.sign_statement(statement, signing.KmsSigner(private_key=key))
    artifact = tmp_path / "release.dsse.json"
    artifact.write_text(json.dumps(envelope), encoding="utf-8")
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 0
    assert env["verified"] is True
    assert env["signer"] == "test-signer"


def test_verify_release_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(
        ["verify", "--release", str(tmp_path / "nope.tgz"), "--json"], capsys
    )
    assert code == 3
    assert env["error"]["key"] == "input.release_missing"


def test_verify_release_tampered_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An envelope whose keyid is not in the trust root is refused, not a crash (the item's named
    defect: this used to surface as `internal.unexpected`)."""
    trusted_key = Ed25519PrivateKey.generate()
    other_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signing, "vendored_trust", lambda: _trust_for(trusted_key))
    statement = signing.intoto_statement(
        "release",
        "sha256:" + "0" * 64,
        "https://agent-conformance.org/attestation/release/v1",
        {},
    )
    envelope = signing.sign_statement(
        statement, signing.KmsSigner(private_key=other_key)
    )
    artifact = tmp_path / "release.dsse.json"
    artifact.write_text(json.dumps(envelope), encoding="utf-8")
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "reason" in env
    assert "error" not in env


@pytest.mark.parametrize(
    "envelope",
    [
        {
            "payloadType": "application/vnd.in-toto+json",
            "payload": "e30=",
            "signatures": ["x"],
        },
        {"payloadType": "application/vnd.in-toto+json", "payload": 5, "signatures": []},
        {"payloadType": 5, "payload": "e30=", "signatures": []},
    ],
    ids=["signatures-not-objects", "payload-wrong-type", "payloadType-wrong-type"],
)
def test_verify_release_malformed_types_refused(
    envelope: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release envelope with a wrong JSON type for a required field is refused cleanly -- these
    crash with `internal.unexpected` today even under a narrower, single-exception-tuple fix."""
    monkeypatch.setattr(
        signing, "vendored_trust", lambda: _trust_for(Ed25519PrivateKey.generate())
    )
    artifact = tmp_path / "release.dsse.json"
    artifact.write_text(json.dumps(envelope), encoding="utf-8")
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "reason" in env
    assert "error" not in env


def test_verify_release_bundle_manifest_not_json_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "release-manifest.json").write_text("not json", encoding="utf-8")
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert env["reason"] == "release manifest is not readable JSON"


def test_verify_release_bundle_signatures_not_json_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": []}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("not json", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert env["reason"] == "release signatures are not readable JSON"


def test_verify_release_bundle_signatures_entry_not_object_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": []}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text(json.dumps(["x"]), encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "signature (None): signature entry is not an object" in env["reason"]


def test_verify_release_bundle_artifact_missing_name_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": [{"digest": "sha256:" + "0" * 64}]}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "release manifest has an artifact entry with no name" in env["reason"]


def test_verify_release_bundle_unsigned_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bundle with no signature entries at all must not be `verified: true` -- the verifier's
    round-1 Finding 1: no problems and no signers used to mean `verified: true`, an unsigned bundle
    reported as verified."""
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": []}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert env["reason"] == "release bundle carries no signatures"
    assert env["signers"] == []


@pytest.mark.parametrize(
    "artifacts",
    [None, 5, "ab"],
    ids=["artifacts-null", "artifacts-int", "artifacts-str"],
)
def test_verify_release_bundle_artifacts_not_a_list_is_empty(
    artifacts: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-list `artifacts` field is treated as `[]`, not a crash (`TypeError` on `None`/`int`)
    and not an iterable of characters (`str`) -- verifier round-1 Finding 2."""
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": artifacts}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "error" not in env
    assert env["reason"] == "release bundle carries no signatures"


def test_verify_release_bundle_manifest_not_canonicalizable_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A manifest holding a float crashes `canonicalize()` with `CanonicalizationError` today --
    verifier round-1 Finding 3: wrap it in the same soft-fail shape, never `internal.unexpected`."""
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": [], "size": 1.5}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "error" not in env
    assert env["reason"] == "release manifest cannot be canonicalized"


def test_verify_release_envelope_with_bom_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A byte-order mark makes the file unreadable JSON in every engine -- verifier round-1 Finding 4
    (TypeScript's decoder silently stripped it; this pins the cross-engine refusal)."""
    artifact = tmp_path / "release.dsse.json"
    artifact.write_text(
        "﻿" + json.dumps({"payloadType": "x", "payload": "", "signatures": []}),
        encoding="utf-8",
    )
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert env["reason"] == "release envelope is not readable JSON"


@pytest.mark.parametrize("size", [1.0, 100.0, -0.0], ids=["1.0", "1e2", "-0.0"])
def test_verify_release_bundle_manifest_integral_float_refused(
    size: float, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An integral-valued float (`1.0`, `1e2`, `-0.0`) is still a non-integer JSON number token and
    must refuse exactly like `1.5` -- verifier round-2: TypeScript's plain `JSON.parse` folded these
    to ordinary integers before `canonicalize` ever saw them, so only Python/Java refused."""
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": [], "size": size}), encoding="utf-8"
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "error" not in env
    assert env["reason"] == "release manifest cannot be canonicalized"


@pytest.mark.parametrize(
    "name",
    ["/etc/hosts", "../../../../../../etc/hosts", "a\x00b"],
    ids=["absolute", "dotdot-escape", "embedded-nul"],
)
def test_verify_release_bundle_artifact_name_unsafe_is_missing(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An artifact name that is absolute, escapes the release directory via `..`, or carries an
    embedded NUL byte must soft-fail as a missing artifact, never read outside the release directory
    and never crash -- verifier round-2 (adjacent probes)."""
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"artifacts": [{"name": name, "digest": "sha256:0" * 8}]}),
        encoding="utf-8",
    )
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "error" not in env
    assert f"missing artifact {name}" in env["reason"]


def test_verify_release_bundle_manifest_deeply_nested_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A manifest nested past `MAX_JSON_DEPTH` is refused as unreadable, the same depth Java's Jackson
    refuses natively, never a crash -- verifier round-2 (adjacent probes). Built as raw text (not
    `json.dumps` on a Python object), since building the fixture that way would itself recurse."""
    nested_text = '{"artifacts": [], "nested": ' + "[" * 5000 + "]" * 5000 + "}"
    (tmp_path / "release-manifest.json").write_text(nested_text, encoding="utf-8")
    (tmp_path / "signatures.json").write_text("[]", encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "error" not in env
    assert env["reason"] == "release manifest is not readable JSON"


@pytest.mark.parametrize(
    ("depth", "reason"),
    [
        (1000, "signature (None): signature entry is not an object"),
        (1001, "release signatures are not readable JSON"),
    ],
)
def test_verify_release_signatures_depth_boundary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], depth: int, reason: str
) -> None:
    """`MAX_JSON_DEPTH` containers parse (and the manifest under them canonicalizes); one more is
    refused -- the boundary all three engines share."""
    (tmp_path / "release-manifest.json").write_text(
        '{"artifacts": []}', encoding="utf-8"
    )
    nested = "[" * depth + "]" * depth
    (tmp_path / "signatures.json").write_text(nested, encoding="utf-8")
    code, env = run(["verify", "--release", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert "error" not in env
    assert env["reason"] == reason


@pytest.mark.parametrize(
    "text",
    [
        '{"x": NaN}',
        '{"x": Infinity}',
        '{"x": "\\ud800"}',
        '{"\\udc00": 1}',
        "\ufeff{}",
        "{} x",
    ],
)
def test_parse_untrusted_json_refuses(text: str) -> None:
    with pytest.raises(ValueError):
        signing.parse_untrusted_json(text.encode("utf-8"))


def test_parse_untrusted_json_accepts_a_surrogate_pair_and_max_depth() -> None:
    assert signing.parse_untrusted_json(b'"\\ud83d\\ude00"') == "\U0001f600"
    depth = signing.MAX_JSON_DEPTH
    assert signing.parse_untrusted_json(b"[" * depth + b"]" * depth) is not None


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, "None"),
        ("sha256:ab", "'sha256:ab'"),
        ("it's", "<a string with special characters>"),
        ("caf\u00e9", "<a string with special characters>"),
        (True, "<not a string>"),
        ([], "<not a string>"),
    ],
)
def test_describe_untrusted(value: object, text: str) -> None:
    assert signing.describe_untrusted(value) == text


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"{", signing.STATEMENT_UNREADABLE),
        (b'{"subject": NaN}', signing.STATEMENT_UNREADABLE),
        (b"[]", signing.STATEMENT_NO_DIGEST),
        (b'{"subject": {}}', signing.STATEMENT_NO_DIGEST),
        (b'{"subject": [{"digest": {"sha256": 7}}]}', signing.STATEMENT_NO_DIGEST),
    ],
)
def test_statement_subject_digest_fixed_texts(payload: bytes, reason: str) -> None:
    with pytest.raises(signing.VerificationError, match=f"^{reason}$"):
        signing.statement_subject_digest(payload)


def test_verify_release_single_file_deeply_nested_envelope_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A single-file release envelope nested far beyond the interpreter's recursion limit must
    soft-fail as unreadable JSON, not crash -- verifier round-2 (adjacent probes). Built as raw text,
    since `json.dumps` on an equivalently deep Python object would itself recurse."""
    nested_text = "[" * 100000 + "]" * 100000
    artifact = tmp_path / "release.dsse.json"
    artifact.write_text(nested_text, encoding="utf-8")
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 3
    assert env["verified"] is False
    assert "error" not in env
    assert env["reason"] == "release envelope is not readable JSON"


def test_verify_catalog_unreadable_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A catalog file the process cannot read is a stable input error, never internal.unexpected."""
    (tmp_path / "catalog.yaml").write_text("id: demo\n", encoding="utf-8")
    (tmp_path / "catalog.yaml").chmod(0)
    try:
        code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    finally:
        (tmp_path / "catalog.yaml").chmod(0o644)
    assert code == 3
    assert env["error"]["key"] == "input.catalog_unreadable"


def test_verify_catalog_symlinked_directory_is_not_descended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """digest_tree lists a symlinked directory but neither descends it nor hashes it, so a link to
    the catalog's own parent neither loops nor changes the digest (the TS and Java walks match)."""
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signing, "vendored_trust", lambda: _trust_for(key))
    (tmp_path / "catalog.yaml").write_text("id: demo\n", encoding="utf-8")
    _sign_catalog(tmp_path, key)
    (tmp_path / "link-out").symlink_to(tmp_path.parent)
    (tmp_path / "broken").symlink_to(tmp_path / "no-such-file")
    code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    assert code == 0
    assert env["verified"] is True


@pytest.mark.parametrize("sig", ["!" * 88, "A" * 86, "AAAA AAAA"])
def test_verify_envelope_sig_not_base64_fixed_text(sig: str) -> None:
    """A `sig` that is not padded, alphabet-only base64 gets one fixed text, never the decoder's."""
    key = Ed25519PrivateKey.generate()
    envelope = signing.sign_statement(
        signing.intoto_statement("x", "sha256:" + "0" * 64, "t", {}),
        signing.KmsSigner(private_key=key),
    )
    envelope["signatures"][0]["sig"] = sig
    with pytest.raises(signing.VerificationError) as exc:
        signing.verify_envelope(envelope, _trust_for(key))
    assert str(exc.value) == (
        "no signature verified against the trust root: 'sig' is not valid base64"
    )


def test_verify_two_targets_is_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(
        ["verify", "--bundle", str(tmp_path), "--catalog", str(tmp_path), "--json"],
        capsys,
    )
    assert code == 3
    assert env["error"]["key"] == "input.verify_target"


def test_report_default_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "assertions.json"
    src.write_text("{}", encoding="utf-8")
    code, env = run(["report", "--from", str(src), "--json"], capsys)
    assert code == 0
    assert env["format"] == "md"


# --- 18.68: an input that cannot be read is refused with one key per target, naming the path. ---

_REPO = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO / "corpus" / "quickstart"
_STREAM = "events/urn-agentce-source-langgraph-gateway-eu-1.jsonl"

not_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a mode-000 file anyway"
)


@contextmanager
def _unreadable(path: Path) -> Iterator[None]:
    mode = path.stat().st_mode
    path.chmod(0)
    try:
        yield
    finally:
        path.chmod(mode)


def _refusal(env: dict[str, Any]) -> tuple[str, str]:
    return env["error"]["key"], env["error"]["cause"]


def _evidence(tmp_path: Path) -> Path:
    bundle = tmp_path / "evidence"
    shutil.copytree(_QUICKSTART / "evidence", bundle)
    return bundle


@not_root
def test_verify_unreadable_input_bundle_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _evidence(tmp_path)
    with _unreadable(bundle):
        code, env = run(["verify", "--bundle", str(bundle), "--json"], capsys)
    assert code == 3
    assert _refusal(env) == (
        "input.bundle_unreadable",
        f"the evidence bundle {bundle} holds a file or folder that cannot be read: "
        "manifest.json.",
    )


@not_root
def test_verify_unreadable_input_bundle_events_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _evidence(tmp_path)
    with _unreadable(bundle / "events"):
        code, env = run(["verify", "--bundle", str(bundle), "--json"], capsys)
    assert code == 3
    key, cause = _refusal(env)
    assert key == "input.bundle_unreadable"
    assert "cannot be read: events/" in cause


@not_root
def test_verify_unreadable_input_bundle_stream_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _evidence(tmp_path)
    with _unreadable(bundle / _STREAM):
        code, env = run(["verify", "--bundle", str(bundle), "--json"], capsys)
    assert code == 3
    assert _refusal(env) == (
        "input.bundle_unreadable",
        f"the evidence bundle {bundle} holds a file or folder that cannot be read: {_STREAM}.",
    )


@not_root
def test_verify_unreadable_input_release_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "release-manifest.json").write_text("{}", encoding="utf-8")
    with _unreadable(release):
        code, env = run(["verify", "--release", str(release), "--json"], capsys)
    assert code == 3
    key, cause = _refusal(env)
    assert key == "input.release_unreadable"
    assert cause.startswith(f"the release artifact {release} ")


@not_root
def test_verify_unreadable_input_catalog_subdirectory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "catalog.yaml").write_text("id: demo\n", encoding="utf-8")
    (tmp_path / "controls").mkdir()
    (tmp_path / "controls" / "c.yaml").write_text("id: c\n", encoding="utf-8")
    with _unreadable(tmp_path / "controls"):
        code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert _refusal(env) == (
        "input.catalog_unreadable",
        f"the catalog directory {tmp_path} holds a file or folder that cannot be read: "
        "controls.",
    )


@not_root
def test_verify_unreadable_input_report_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "report"
    report.mkdir()
    with _unreadable(report):
        code, env = run(["verify", "--report", str(report), "--json"], capsys)
    assert code == 3
    assert _refusal(env) == (
        "verify.report_unreadable",
        f"the report directory {report} cannot be read.",
    )


def _report_refusal(
    capsys: pytest.CaptureFixture[str], out: Path, rel: str
) -> tuple[int, tuple[str, str]]:
    capsys.readouterr()  # discard the setup commands' output
    with _unreadable(out / rel):
        code, env = run(["verify", "--report", str(out), "--json"], capsys)
    return code, _refusal(env)


@not_root
def test_verify_unreadable_input_report_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _key = _packaged_and_signed(tmp_path)
    assert _report_refusal(capsys, out, "manifest.json") == (
        3,
        (
            "verify.report_unreadable",
            f"the report directory {out} holds a file or folder that cannot be read: "
            "manifest.json.",
        ),
    )


@not_root
def test_verify_unreadable_input_report_packaged_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _key = _packaged_and_signed(
        tmp_path,
        "--catalog-dir",
        str(_AUD_FIXTURE / "catalog"),
        "--allow-unverified-catalog",
    )
    code, (key, cause) = _report_refusal(capsys, out, "bundle/catalog/0")
    assert (code, key) == (3, "verify.report_unreadable")
    assert "cannot be read: bundle/catalog/0" in cause


@not_root
def test_verify_unreadable_input_report_packaged_deviations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deviations = tmp_path / "deviations.yaml"
    deviations.write_text("deviations: []\n", encoding="utf-8")
    out, _key = _packaged_and_signed(tmp_path, "--deviations", str(deviations))
    assert _report_refusal(capsys, out, "bundle/deviations.yaml") == (
        3,
        (
            "verify.report_unreadable",
            f"the report directory {out} holds a file or folder that cannot be read: "
            "bundle/deviations.yaml.",
        ),
    )


@not_root
@pytest.mark.parametrize(
    "extra", [[], ["--allow-unverified-catalog"]], ids=["strict", "override"]
)
def test_verify_unreadable_input_assess_catalog_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    """Unreadable is not unverified: --allow-unverified-catalog does not cover it."""
    catalog = tmp_path / "catalog"
    shutil.copytree(_REPO / "spec" / "catalogs" / "base" / "eu-ai-act", catalog)
    with _unreadable(catalog / "controls"):
        code = cli.main(
            [
                "assess",
                "--bundle",
                str(_QUICKSTART / "evidence"),
                "--profile",
                str(_QUICKSTART / "applicability.yaml"),
                "--catalog-dir",
                str(catalog),
                *extra,
                "--out",
                str(tmp_path / "out"),
                "--json",
            ]
        )
    env = json.loads(capsys.readouterr().out)
    assert code == 3
    assert _refusal(env) == (
        "input.catalog_unreadable",
        f"the catalog directory {catalog} holds a file or folder that cannot be read: "
        "controls.",
    )
