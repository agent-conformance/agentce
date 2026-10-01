"""verify_parity_check - prove Python, TypeScript, and Java compute an identical `agentce verify
--catalog`/`--release` (single-file and directory-bundle) across the twelve scenarios item 18.28's
contract names (C3).

`agentce verify`'s job (SPEC §8.7, §9.1) is offline signature verification against the vendored
development trust root. Three independent per-engine unit-test suites (18.28's C1/C2) can pass with
three independently-wrong-but-matching-in-no-way implementations, since none of them ever compares
against another engine's real output. This check does, mirroring `sign_parity_check.py`'s shape
(18.26's C3).

**The dev-root trust root has no CLI override for `--catalog`/`--release`** (unlike `--report`'s
`--signer-trust-root`), so every fixture here is signed with material already pinned in the vendored
`dev-root.json`: `conformance/dev_trust.py`'s deterministic `kms_key()`/`catalog_key()` derivation for
key-based signatures, and its new, test-only `deterministic_keyless_signer` for certificate-based
(keyless) ones -- never a fresh ad-hoc key the way other parity checks' own trust roots could.

* `--self-test` needs no build and shells into no engine: the comparator, the base64-byte-flip helper
  (proving it actually changes content while staying valid base64), and the vendored real catalog
  fixture's own shape.
* The real invocation builds every cryptographic fixture once (a single shelled call into
  `engines/python`'s own `uv` environment -- the only place `cryptography` is a dependency -- using
  `conformance.dev_trust`'s key-derivation functions and `signing.sign_statement` directly), then
  copies each into three independent per-engine directories and applies any scenario-specific,
  crypto-free tampering (a base64 byte flip, a deleted file, a mutated JSON field) separately per
  engine copy, then runs Python's own `agentce verify` (the reference), the built TypeScript
  `dist/cli.js`, and the built Java runnable jar over all twelve scenarios.

Usage:
    verify_parity_check.py             # needs `pnpm build` (TS) and `:assemble` (Java) run first
    verify_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import readiness_parity_check

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
EU_AI_ACT = ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"

CATALOG_SIGNATURE_NAME = "catalog.sig.json"

#: The exact literal Python gives for an absent catalog signature (`signing.UnsignedError`'s own
#: message, `signing.py:437-442`) -- asserted byte-identical across all three engines (scenario 3).
UNSIGNED_SENTENCE = f"unsigned: {CATALOG_SIGNATURE_NAME} is absent, so there is no signature to verify (SPEC §8.7)."


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[str, int]:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return proc.stdout, proc.returncode


def python_verify(args: list[str]) -> tuple[str, int]:
    return _run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(PY_ENGINE),
            "agentce",
            "verify",
            *args,
        ],
        cwd=ROOT,
    )


def typescript_verify(args: list[str]) -> tuple[str, int]:
    entry = TS_ENGINE / "dist" / "cli.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing "
            "(run `pnpm build` in engines/typescript first)"
        )
    return _run(["node", str(entry), "verify", *args], cwd=ROOT)


def java_verify(args: list[str]) -> tuple[str, int]:
    return _run(
        ["java", "-jar", str(readiness_parity_check.java_jar()), "verify", *args],
        cwd=ROOT,
    )


def _run_engines(args: list[str]) -> dict[str, tuple[str, int]]:
    return {
        "python": python_verify(args),
        "typescript": typescript_verify(args),
        "java": java_verify(args),
    }


def _per_engine_dirs(tmp: Path, name: str) -> dict[str, Path]:
    dirs = {}
    for engine in ("python", "typescript", "java"):
        d = tmp / name / engine
        d.mkdir(parents=True)
        dirs[engine] = d
    return dirs


def _flip_b64_byte(b64_text: str) -> str:
    """Flip one bit of the first decoded byte and re-encode -- always valid base64 (the encoder only
    ever emits the standard alphabet), but never decodes to the original content, so this cleanly
    exercises the signature-mismatch path (round-2 critic finding #11) rather than a malformed-base64
    crash."""
    raw = bytearray(base64.b64decode(b64_text))
    raw[0] ^= 0x01
    return base64.b64encode(bytes(raw)).decode("ascii")


# --- Fixture construction (the one shelled call into engines/python's own environment). -----------

_BUILD_SCRIPT = """
import json
from pathlib import Path

from agentce import signing
from agentce.canonical import canonicalize
from conformance import dev_trust

out = Path({out!r})
out.mkdir(parents=True, exist_ok=True)

RELEASE_PREDICATE_TYPE = "https://agent-conformance.org/attestation/release/v1"


def _envelope(signer, *, subject_name, digest, predicate):
    statement = signing.intoto_statement(
        subject_name=subject_name,
        digest=digest,
        predicate_type=RELEASE_PREDICATE_TYPE,
        predicate=predicate,
    )
    return signing.sign_statement(statement, signer)


# Scenarios 4/5 (single-file, kms-signed).
kms_env = _envelope(
    dev_trust.KmsSigner(private_key=dev_trust.kms_key()),
    subject_name="release",
    digest="sha256:" + "0" * 64,
    predicate={{}},
)
(out / "release-kms.json").write_text(json.dumps(kms_env), encoding="utf-8")

# Scenarios 6/7 (single-file, certificate/keyless-signed).
cert_env = _envelope(
    dev_trust.deterministic_keyless_signer("c3-s6", "sigstore-public"),
    subject_name="release",
    digest="sha256:" + "0" * 64,
    predicate={{}},
)
(out / "release-cert.json").write_text(json.dumps(cert_env), encoding="utf-8")

# Scenarios 8-11 (directory-bundle: one kms entry, one keyless entry, over a real manifest digest).
bundle = out / "bundle"
bundle.mkdir(exist_ok=True)
artifact_bytes = b"agentce verify-parity fixture artifact\\n"
(bundle / "artifact-a.txt").write_bytes(artifact_bytes)
manifest = {{
    "artifacts": [
        {{"name": "artifact-a.txt", "digest": signing.sha256_prefixed(artifact_bytes)}}
    ]
}}
manifest_digest = signing.sha256_prefixed(canonicalize(manifest))
(bundle / "release-manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\\n", encoding="utf-8"
)
signatures = []
for profile, signer in (
    ("kms", dev_trust.KmsSigner(private_key=dev_trust.kms_key())),
    ("sigstore-public", dev_trust.deterministic_keyless_signer("c3-s8", "sigstore-public")),
):
    envelope = _envelope(
        signer,
        subject_name="release-manifest.json",
        digest=manifest_digest,
        predicate={{"profile": profile, "release": "agentce@parity-fixture"}},
    )
    signatures.append({{"profile": profile, "target": "release-manifest.json", "envelope": envelope}})
(bundle / "signatures.json").write_text(
    json.dumps(signatures, indent=2, sort_keys=True) + "\\n", encoding="utf-8"
)

print(json.dumps({{"manifest_digest": manifest_digest, "profiles": ["kms", "sigstore-public"]}}))
"""


def build_canonical_fixtures(canonical: Path) -> dict[str, Any]:
    """The one shelled call into `engines/python`'s own `uv` environment that needs `cryptography`
    (not a `tools` dependency) -- builds every cryptographically-signed fixture this check reuses,
    copied crypto-free into each scenario's per-engine directories below."""
    script = _BUILD_SCRIPT.format(out=str(canonical))
    out, code = _run(
        ["uv", "run", "--frozen", "--project", str(PY_ENGINE), "python", "-c", script],
        cwd=ROOT,
    )
    if code != 0:
        raise SystemExit(f"could not build verify-parity fixtures: {out}")
    return json.loads(out)


# --- Self-test: the comparator and the crypto-free helpers, no build and no engine shell. ----------


def self_test() -> int:
    failures: list[str] = []

    unmoved: list[str] = []
    readiness_parity_check.compare(
        "comparator-self-test", "identical\n", "identical\n", "a", "b", unmoved
    )
    if unmoved:
        failures.append(
            "comparator wrongly flagged two identical strings as a mismatch"
        )
    caught: list[str] = []
    readiness_parity_check.compare(
        "comparator-self-test", "identical\n", "identicalX\n", "a", "b", caught
    )
    if not caught:
        failures.append(
            "comparator failed to catch a one-byte tamper between two 'engine output' strings"
        )

    original = base64.b64encode(b"agentce verify parity self-test payload").decode(
        "ascii"
    )
    flipped = _flip_b64_byte(original)
    if flipped == original:
        failures.append("_flip_b64_byte did not change the payload")
    try:
        base64.b64decode(flipped, validate=True)
    except ValueError:
        failures.append(
            "_flip_b64_byte produced a string that is not valid strict base64"
        )
    if base64.b64decode(flipped) == base64.b64decode(original):
        failures.append(
            "_flip_b64_byte's re-encoded bytes decode back to the original content"
        )

    sig_path = EU_AI_ACT / CATALOG_SIGNATURE_NAME
    if not sig_path.is_file():
        failures.append(
            f"{sig_path} is missing -- this check reuses the real vendored eu-ai-act catalog "
            "fixture's own signature directly (never a synthetic catalog)"
        )
    else:
        envelope = json.loads(sig_path.read_text(encoding="utf-8"))
        for field in ("payloadType", "payload", "signatures"):
            if field not in envelope:
                failures.append(f"{sig_path}: envelope is missing {field!r}")

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(
            "verify_parity_check self-test: comparator + crypto-free helpers discriminate"
        )
    return 1 if failures else 0


# --- The real check. ---------------------------------------------------------------------------


def _assert(condition: bool, failures: list[str], message: str) -> None:
    if not condition:
        failures.append(message)


def _reason(out: str) -> str | None:
    try:
        return json.loads(out).get("reason")
    except json.JSONDecodeError:
        return None


def _normalized(out: str) -> str:
    """`out` with the `catalog`/`release` path field blanked -- each engine runs against its own
    per-engine copy of the fixture (never a shared mutable file, so cross-engine runs cannot
    interfere with each other), so that field is expected to differ by construction and must not
    fail the byte-identical comparison the way every other field does."""
    try:
        env = json.loads(out)
    except json.JSONDecodeError:
        return out
    for key in ("catalog", "release"):
        if key in env:
            env[key] = "<path>"
    return json.dumps(env, indent=2, sort_keys=True) + "\n"


def _run_catalog_scenario(
    name: str,
    canonical_catalog: Path,
    tmp: Path,
    failures: list[str],
    *,
    mutate: Any = None,
    expect_exit: int,
    expect_verified: bool,
) -> dict[str, tuple[str, int]]:
    dirs = _per_engine_dirs(tmp, name)
    for engine, d in dirs.items():
        catalog_dir = d / "catalog"
        shutil.copytree(canonical_catalog, catalog_dir)
        if mutate is not None:
            mutate(catalog_dir)
    runs = {
        engine: _run_engines(["--catalog", str(d / "catalog"), "--json"])[engine]
        for engine, d in dirs.items()
    }
    for engine, (out, code) in runs.items():
        _assert(
            code == expect_exit,
            failures,
            f"{name}:{engine}: exit {code}, expected {expect_exit} ({out.strip()[:200]!r})",
        )
        try:
            verified = json.loads(out).get("verified")
        except json.JSONDecodeError:
            verified = None
        _assert(
            verified == expect_verified,
            failures,
            f"{name}:{engine}: verified={verified!r}, expected {expect_verified!r}",
        )
    readiness_parity_check.compare_ports(
        f"{name}:error",
        [_normalized(runs[e][0]) for e in ("python", "typescript", "java")],
        failures,
    )
    return runs


def _run_release_scenario(
    name: str,
    build: Any,
    tmp: Path,
    failures: list[str],
    *,
    expect_exit: int,
    expect_verified: bool,
) -> dict[str, tuple[str, int]]:
    """`build(engine_dir) -> release_path` writes this scenario's fixture into `engine_dir`
    independently per engine (copied from the canonical crypto fixtures, never shared mutable
    state), and returns the path to pass as `--release`."""
    dirs = _per_engine_dirs(tmp, name)
    runs: dict[str, tuple[str, int]] = {}
    for engine, d in dirs.items():
        release_path = build(d)
        runs[engine] = _run_engines(["--release", str(release_path), "--json"])[engine]
    for engine, (out, code) in runs.items():
        _assert(
            code == expect_exit,
            failures,
            f"{name}:{engine}: exit {code}, expected {expect_exit} ({out.strip()[:200]!r})",
        )
        try:
            verified = json.loads(out).get("verified")
        except json.JSONDecodeError:
            verified = None
        _assert(
            verified == expect_verified,
            failures,
            f"{name}:{engine}: verified={verified!r}, expected {expect_verified!r}",
        )
    readiness_parity_check.compare_ports(
        f"{name}:error",
        [_normalized(runs[e][0]) for e in ("python", "typescript", "java")],
        failures,
    )
    return runs


def run_real_check() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="verify-parity-") as raw:
        tmp = Path(raw)
        canonical = tmp / "canonical"
        build_canonical_fixtures(canonical)

        # Scenario 1: the real vendored eu-ai-act catalog, already signed -- verified: true,
        # identical digest/signer/keyid across all three engines.
        runs = _run_catalog_scenario(
            "s1-catalog-happy",
            EU_AI_ACT,
            tmp,
            failures,
            expect_exit=0,
            expect_verified=True,
        )
        for engine, (out, _) in runs.items():
            env = json.loads(out)
            _assert(
                env.get("keyless") is False,
                failures,
                f"s1-catalog-happy:{engine}: keyless={env.get('keyless')!r}, expected false",
            )

        # Scenario 2: catalog.sig.json's payload flipped -- the signature itself fails to verify.
        def _flip_payload(catalog_dir: Path) -> None:
            sig_path = catalog_dir / CATALOG_SIGNATURE_NAME
            envelope = json.loads(sig_path.read_text(encoding="utf-8"))
            envelope["payload"] = _flip_b64_byte(envelope["payload"])
            sig_path.write_text(json.dumps(envelope), encoding="utf-8")

        runs = _run_catalog_scenario(
            "s2-catalog-payload-flipped",
            EU_AI_ACT,
            tmp,
            failures,
            mutate=_flip_payload,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out) == "no signature verified against the trust root: ",
                failures,
                f"s2-catalog-payload-flipped:{engine}: reason={_reason(out)!r}, expected the "
                "empty-InvalidSignature sentence",
            )

        # Scenario 3: catalog.sig.json deleted -- the exact unsigned sentence.
        def _delete_sig(catalog_dir: Path) -> None:
            (catalog_dir / CATALOG_SIGNATURE_NAME).unlink()

        runs = _run_catalog_scenario(
            "s3-catalog-unsigned",
            EU_AI_ACT,
            tmp,
            failures,
            mutate=_delete_sig,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out) == UNSIGNED_SENTENCE,
                failures,
                f"s3-catalog-unsigned:{engine}: reason={_reason(out)!r}, expected {UNSIGNED_SENTENCE!r}",
            )

        # Scenario 4: single-file kms-signed release envelope -- verified: true, keyless: false.
        def _copy_kms(d: Path) -> Path:
            dest = d / "release.json"
            shutil.copyfile(canonical / "release-kms.json", dest)
            return dest

        runs = _run_release_scenario(
            "s4-release-kms",
            _copy_kms,
            tmp,
            failures,
            expect_exit=0,
            expect_verified=True,
        )
        for engine, (out, _) in runs.items():
            env = json.loads(out)
            _assert(
                env.get("keyless") is False,
                failures,
                f"s4-release-kms:{engine}: keyless={env.get('keyless')!r}, expected false",
            )

        # Scenario 5: the kms envelope's signature byte flipped -- the item's own named defect:
        # verified: false with the identical reason text, never a crash.
        def _flip_kms_sig(d: Path) -> Path:
            envelope = json.loads(
                (canonical / "release-kms.json").read_text(encoding="utf-8")
            )
            envelope["signatures"][0]["sig"] = _flip_b64_byte(
                envelope["signatures"][0]["sig"]
            )
            dest = d / "release.json"
            dest.write_text(json.dumps(envelope), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s5-release-kms-tampered",
            _flip_kms_sig,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                "error" not in json.loads(out),
                failures,
                f"s5-release-kms-tampered:{engine}: output carries an 'error' field (a crash, "
                "not a clean refusal)",
            )
            _assert(
                _reason(out) == "no signature verified against the trust root: ",
                failures,
                f"s5-release-kms-tampered:{engine}: reason={_reason(out)!r}",
            )

        # Scenario 6: single-file certificate/keyless-signed release envelope -- verified: true,
        # keyless: true, identical signer identity (the documented default release path).
        def _copy_cert(d: Path) -> Path:
            dest = d / "release.json"
            shutil.copyfile(canonical / "release-cert.json", dest)
            return dest

        runs = _run_release_scenario(
            "s6-release-cert",
            _copy_cert,
            tmp,
            failures,
            expect_exit=0,
            expect_verified=True,
        )
        for engine, (out, _) in runs.items():
            env = json.loads(out)
            _assert(
                env.get("keyless") is True,
                failures,
                f"s6-release-cert:{engine}: keyless={env.get('keyless')!r}, expected true",
            )

        # Scenario 7: the certificate's signature field corrupted -- the full aggregate message
        # (the inner certificate failure wrapped by the per-entry "no signature verified" template).
        def _flip_cert_sig(d: Path) -> Path:
            envelope = json.loads(
                (canonical / "release-cert.json").read_text(encoding="utf-8")
            )
            cert = envelope["signatures"][0]["cert"]
            cert["signature"] = _flip_b64_byte(cert["signature"])
            dest = d / "release.json"
            dest.write_text(json.dumps(envelope), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s7-release-cert-tampered",
            _flip_cert_sig,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out)
                == "no signature verified against the trust root: certificate signature does not verify",
                failures,
                f"s7-release-cert-tampered:{engine}: reason={_reason(out)!r}",
            )

        # Scenario 8: directory-bundle release -- one kms entry, one keyless entry, both profiles
        # represented, verified: true.
        def _copy_bundle(d: Path) -> Path:
            dest = d / "bundle"
            shutil.copytree(canonical / "bundle", dest)
            return dest

        runs = _run_release_scenario(
            "s8-release-bundle",
            _copy_bundle,
            tmp,
            failures,
            expect_exit=0,
            expect_verified=True,
        )
        for engine, (out, _) in runs.items():
            env = json.loads(out)
            profiles = {s.get("profile") for s in env.get("signers", [])}
            _assert(
                profiles == {"kms", "sigstore-public"},
                failures,
                f"s8-release-bundle:{engine}: signers' profiles={profiles!r}, expected both "
                "'kms' and 'sigstore-public'",
            )

        # Scenario 9: one signatures.json entry replaced with an empty object (missing both
        # `profile` and `envelope`) -- Python's exact `signature (None): 'envelope'`; TS/Java
        # asserted only on shape (disclosed, narrower -- round-2 critic finding #9/round-1
        # correction #9).
        def _strip_envelope(d: Path) -> Path:
            dest = d / "bundle"
            shutil.copytree(canonical / "bundle", dest)
            signatures_path = dest / "signatures.json"
            entries = json.loads(signatures_path.read_text(encoding="utf-8"))
            entries[0] = {}
            signatures_path.write_text(json.dumps(entries), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s9-release-bundle-missing-envelope",
            _strip_envelope,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        py_reason = _reason(runs["python"][0])
        _assert(
            py_reason is not None and "signature (None): 'envelope'" in py_reason,
            failures,
            f"s9-release-bundle-missing-envelope:python: reason={py_reason!r}, expected it to "
            "contain \"signature (None): 'envelope'\"",
        )

        # Scenario 10: signatures.json replaced by a bare-string array -- byte-identical fixed text.
        def _bare_strings(d: Path) -> Path:
            dest = d / "bundle"
            shutil.copytree(canonical / "bundle", dest)
            (dest / "signatures.json").write_text(json.dumps(["x"]), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s10-release-bundle-bare-signature",
            _bare_strings,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            reason = _reason(out)
            _assert(
                reason is not None and "signature entry is not an object" in reason,
                failures,
                f"s10-release-bundle-bare-signature:{engine}: reason={reason!r}",
            )

        # Scenario 11: one manifest.artifacts[] entry missing `name` -- byte-identical fixed text.
        def _missing_name(d: Path) -> Path:
            dest = d / "bundle"
            shutil.copytree(canonical / "bundle", dest)
            manifest_path = dest / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["artifacts"][0]["name"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s11-release-bundle-artifact-no-name",
            _missing_name,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            reason = _reason(out)
            _assert(
                reason is not None
                and "release manifest has an artifact entry with no name" in reason,
                failures,
                f"s11-release-bundle-artifact-no-name:{engine}: reason={reason!r}",
            )

        # Scenario 12: three malformed-JSON-type vectors -- never a crash in any of the three.
        malformed: list[tuple[str, dict[str, Any], str]] = [
            (
                "s12a-payload-wrong-type",
                {
                    "payloadType": "application/vnd.in-toto+json",
                    "payload": 5,
                    "signatures": [],
                },
                "malformed DSSE envelope",
            ),
            (
                "s12b-payloadType-wrong-type",
                {"payloadType": 5, "payload": "e30=", "signatures": []},
                "malformed DSSE envelope",
            ),
            (
                "s12c-signatures-not-objects",
                {
                    "payloadType": "application/vnd.in-toto+json",
                    "payload": "e30=",
                    "signatures": ["x"],
                },
                "no signature verified against the trust root: no trusted key for keyid None",
            ),
        ]
        for name, envelope, expect_reason in malformed:

            def _write_malformed(d: Path, envelope: dict[str, Any] = envelope) -> Path:
                dest = d / "release.json"
                dest.write_text(json.dumps(envelope), encoding="utf-8")
                return dest

            runs = _run_release_scenario(
                name,
                _write_malformed,
                tmp,
                failures,
                expect_exit=3,
                expect_verified=False,
            )
            for engine, (out, _) in runs.items():
                _assert(
                    "error" not in json.loads(out),
                    failures,
                    f"{name}:{engine}: output carries an 'error' field (a crash)",
                )
                _assert(
                    _reason(out) == expect_reason,
                    failures,
                    f"{name}:{engine}: reason={_reason(out)!r}, expected {expect_reason!r}",
                )

    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "MATCH: verify --catalog/--release scenarios (happy path, signature/certificate tamper, "
        "unsigned, directory-bundle, and malformed-type vectors) byte-identical across "
        "python, typescript, java"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_real_check()


if __name__ == "__main__":
    raise SystemExit(main())
