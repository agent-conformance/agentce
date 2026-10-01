"""verify_parity_check - prove Python, TypeScript, and Java compute an identical `agentce verify
--catalog`/`--release`/`--bundle` (single-file, directory-bundle, and evidence bundle) across the
scenarios item 18.28's contract names (C3).

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
  `dist/cli.js`, and the built Java runnable jar over every scenario.

Usage:
    verify_parity_check.py             # needs `pnpm build` (TS) and `:assemble` (Java) run first
    verify_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import readiness_parity_check
import verify_flow_census

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
EU_AI_ACT = ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
EVIDENCE_BUNDLE = ROOT / "corpus" / "quickstart" / "evidence"

CATALOG_SIGNATURE_NAME = verify_flow_census.CATALOG_SIGNATURE_NAME

#: The exact literal Python gives for an absent catalog signature (`signing.UnsignedError`'s own
#: message, `signing.py:437-442`) -- asserted byte-identical across all three engines (scenario 3).
UNSIGNED_SENTENCE = f"unsigned: {CATALOG_SIGNATURE_NAME} is absent, so there is no signature to verify (SPEC §8.7)."

#: The fixed literal every engine gives for a directory-bundle release with zero signature
#: entries (verifier round-1 Finding 1: "no problems, no signers" used to mean `verified: true`,
#: an unsigned bundle reported as verified) -- asserted byte-identical across all three engines
#: (scenario 23) and reused by VG-VERIFY's own unsigned-bundle leg.
UNSIGNED_BUNDLE_SENTENCE = "release bundle carries no signatures"


def write_bundle_with_manifest(dest: Path, manifest: Any) -> None:
    """Writes an unsigned directory-bundle release (`release-manifest.json` + an empty
    `signatures.json`) at `dest`, which must not already exist -- the one fixture shape scenarios
    23-25 and VG-VERIFY's own unsigned-bundle leg all share, so the manifest shape (or the empty-
    signatures convention) only has one place to keep in sync."""
    dest.mkdir()
    (dest / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (dest / "signatures.json").write_text("[]", encoding="utf-8")


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


ENGINE_VERIFY = {
    "python": python_verify,
    "typescript": typescript_verify,
    "java": java_verify,
}


def _run_engines(args: list[str]) -> dict[str, tuple[str, int]]:
    return {engine: run(args) for engine, run in ENGINE_VERIFY.items()}


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

# Scenario s17 (directory-bundle signature entry whose signed statement has an empty `subject`
# list -- exercises `statement_subject_digest`'s `IndexError`, caught by the bundle's per-entry
# tuple; needs a real signature, since the per-entry loop only reaches this check once the DSSE
# signature itself has already verified).
empty_subject_statement = {{
    "_type": signing.INTOTO_STATEMENT_TYPE,
    "subject": [],
    "predicateType": RELEASE_PREDICATE_TYPE,
    "predicate": {{}},
}}
empty_subject_env = signing.sign_statement(
    empty_subject_statement, dev_trust.KmsSigner(private_key=dev_trust.kms_key())
)
(out / "release-subject-empty-entry.json").write_text(
    json.dumps(empty_subject_env), encoding="utf-8"
)

# The census's re-signed variants (statement and certificate fields), tools/verify_flow_census.py.
import sys
sys.path.insert(0, {tools!r})
import verify_flow_census
verify_flow_census.build_signed_variants(
    out, signing, dev_trust, canonicalize, manifest_digest, Path({catalog!r})
)

print(json.dumps({{"manifest_digest": manifest_digest, "profiles": ["kms", "sigstore-public"]}}))
"""


def build_canonical_fixtures(canonical: Path) -> dict[str, Any]:
    """The one shelled call into `engines/python`'s own `uv` environment that needs `cryptography`
    (not a `tools` dependency) -- builds every cryptographically-signed fixture this check reuses,
    copied crypto-free into each scenario's per-engine directories below."""
    script = _BUILD_SCRIPT.format(
        out=str(canonical), tools=str(ROOT / "tools"), catalog=str(EU_AI_ACT)
    )
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
        engine: ENGINE_VERIFY[engine](["--catalog", str(d / "catalog"), "--json"])
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
        runs[engine] = ENGINE_VERIFY[engine](["--release", str(release_path), "--json"])
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


def _run_bundle_scenario(
    name: str,
    args: list[str],
    failures: list[str],
    *,
    expect_exit: int,
) -> dict[str, tuple[str, int]]:
    """Like `_run_catalog_scenario`/`_run_release_scenario`, but for `--bundle`: there is no
    per-engine fixture to build, since the evidence bundle is read-only and shared (identical
    for all three engines), and its success shape (`streams`/`stream_count`/`broken_streams`) has
    no `verified` field to assert on."""
    runs = _run_engines(args)
    for engine, (out, code) in runs.items():
        _assert(
            code == expect_exit,
            failures,
            f"{name}:{engine}: exit {code}, expected {expect_exit} ({out.strip()[:200]!r})",
        )
    readiness_parity_check.compare_ports(
        f"{name}:error",
        [_normalized(runs[e][0]) for e in ("python", "typescript", "java")],
        failures,
    )
    return runs


def _run_mutation(
    mutation: verify_flow_census.Mutation, root: Path
) -> dict[str, tuple[str, int]]:
    """Each engine's output for one mutation, built once (`verify` only reads it), with the fixture
    directory written as `<dir>` so a refusal that names the path compares alike."""
    flag = "--catalog" if mutation.target == "catalog" else "--release"
    root.mkdir(parents=True)
    target = str(mutation.build(root))
    runs = {}
    for engine, run in ENGINE_VERIFY.items():
        out, code = run([flag, target, "--json"])
        runs[engine] = (out.replace(str(root), "<dir>"), code)
    return runs


def _census_view(out: str, code: int) -> str:
    """What the census compares: a refusal's key, text and fix (the engines name the fields
    differently), else the normalized result; then the exit code."""
    try:
        is_error = "error" in json.loads(out)
    except json.JSONDecodeError:
        is_error = False
    view = (
        readiness_parity_check._error_fields(out) + "\n"
        if is_error
        else _normalized(out)
    )
    return f"{view}exit {code}\n"


def run_census(canonical: Path, tmp: Path, failures: list[str]) -> None:
    """Every mutation `verify_flow_census.generate` derives from the census, through all three
    engines: byte-identical output and exit code, a JSON envelope, never `internal.unexpected`."""
    mutations = verify_flow_census.generate(canonical, EU_AI_ACT)
    failures.extend(verify_flow_census.coverage_problems(mutations))
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        results = list(
            pool.map(
                lambda im: _run_mutation(im[1], tmp / "census" / str(im[0])),
                enumerate(mutations),
            )
        )
    verified = []
    for mutation, runs in zip(mutations, results, strict=True):
        label = f"census {mutation.name}"
        for engine, (out, code) in runs.items():
            _assert(
                "internal.unexpected" not in out,
                failures,
                f"{label}:{engine}: crashed ({out.strip()[:300]!r})",
            )
        outputs = [_census_view(*runs[e]) for e in ENGINE_VERIFY]
        readiness_parity_check.compare_ports(label, outputs, failures)
        if runs["python"][1] == 0:
            verified.append(mutation.name)
    print(
        f"census: {len(mutations)} mutations over {len(verify_flow_census.FLOW_POINTS)} flow "
        f"points; still verified (unsigned fields only): {', '.join(verified) or 'none'}"
    )


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

        # Scenario 13: bundle release-manifest.json replaced by a JSON array (valid JSON, wrong
        # top-level type) -- folded into the same "not readable JSON" text as a file that does not
        # parse at all (Dispositions), byte-identical, never a crash.
        def _manifest_wrong_type(d: Path) -> Path:
            dest = _copy_bundle(d)
            (dest / "release-manifest.json").write_text(
                json.dumps([]), encoding="utf-8"
            )
            return dest

        runs = _run_release_scenario(
            "s13-release-bundle-manifest-wrong-type",
            _manifest_wrong_type,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out) == "release manifest is not readable JSON",
                failures,
                f"s13-release-bundle-manifest-wrong-type:{engine}: reason={_reason(out)!r}",
            )

        # Scenario 14: bundle signatures.json replaced by a bare integer (valid JSON, wrong
        # top-level type, not an array) -- same deliberate fold, byte-identical, never a crash.
        def _signatures_wrong_type(d: Path) -> Path:
            dest = _copy_bundle(d)
            (dest / "signatures.json").write_text(json.dumps(5), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s14-release-bundle-signatures-wrong-type",
            _signatures_wrong_type,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out) == "release signatures are not readable JSON",
                failures,
                f"s14-release-bundle-signatures-wrong-type:{engine}: reason={_reason(out)!r}",
            )

        # Scenario 15: a manifest.artifacts[] entry whose `name` is present but not a string --
        # folded into the same "no name" text as a missing `name` (Dispositions: "has a name that
        # is not a string" and "has no name" are the same defect from a caller's point of view).
        def _artifact_name_not_string(d: Path) -> Path:
            dest = _copy_bundle(d)
            manifest_path = dest / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][0]["name"] = 5
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s15-release-bundle-artifact-name-not-string",
            _artifact_name_not_string,
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
                f"s15-release-bundle-artifact-name-not-string:{engine}: reason={reason!r}",
            )

        # Scenario 16: one signatures.json entry's own envelope has a malformed field type
        # (`payloadType` wrong type) -- the per-entry loop's exception tuple catches it and wraps
        # it with the entry's profile, same as any other per-entry failure.
        def _entry_envelope_malformed(d: Path) -> Path:
            dest = _copy_bundle(d)
            signatures_path = dest / "signatures.json"
            entries = json.loads(signatures_path.read_text(encoding="utf-8"))
            entries[0]["envelope"]["payloadType"] = 5
            signatures_path.write_text(json.dumps(entries), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s16-release-bundle-entry-malformed-envelope",
            _entry_envelope_malformed,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            reason = _reason(out)
            _assert(
                reason is not None and "malformed DSSE envelope" in reason,
                failures,
                f"s16-release-bundle-entry-malformed-envelope:{engine}: reason={reason!r}",
            )

        # Scenario 17: one signatures.json entry's envelope verifies (a real signature), but the
        # statement it signs has an empty `subject` list -- `statement_subject_digest`'s
        # `IndexError`, caught by the per-entry tuple and wrapped the same as any other failure.
        def _entry_subject_empty(d: Path) -> Path:
            dest = _copy_bundle(d)
            signatures_path = dest / "signatures.json"
            entries = json.loads(signatures_path.read_text(encoding="utf-8"))
            empty_subject_env = json.loads(
                (canonical / "release-subject-empty-entry.json").read_text(
                    encoding="utf-8"
                )
            )
            entries[0]["envelope"] = empty_subject_env
            signatures_path.write_text(json.dumps(entries), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s17-release-bundle-entry-subject-empty",
            _entry_subject_empty,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            reason = _reason(out)
            _assert(
                reason is not None
                and "the signed statement carries no subject digest" in reason,
                failures,
                f"s17-release-bundle-entry-subject-empty:{engine}: reason={reason!r}",
            )

        # Scenario 18: a single-file release envelope that is not valid UTF-8 -- every engine's
        # strict decoder (Python's `read_text("utf-8")`, TS's `TextDecoder(..., {fatal: true})`,
        # Java's fatal-UTF-8 `readJsonFileStrict`) refuses it with the same fixed sentence a
        # non-parsing file gets, never a crash.
        def _non_utf8(d: Path) -> Path:
            dest = d / "release.json"
            dest.write_bytes(
                b'{"payloadType": "x", "payload": "\xff\xfe", "signatures": []}'
            )
            return dest

        runs = _run_release_scenario(
            "s18-release-non-utf8",
            _non_utf8,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out) == "release envelope is not readable JSON",
                failures,
                f"s18-release-non-utf8:{engine}: reason={_reason(out)!r}",
            )

        # Scenario 19: bundle with the manifest's one artifact file deleted -- a crypto-free
        # mutation of the artifact file only (manifest/signatures stay validly signed), byte-
        # identical fixed text.
        def _missing_artifact(d: Path) -> Path:
            dest = _copy_bundle(d)
            (dest / "artifact-a.txt").unlink()
            return dest

        runs = _run_release_scenario(
            "s19-release-bundle-missing-artifact",
            _missing_artifact,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            reason = _reason(out)
            _assert(
                reason is not None and "missing artifact artifact-a.txt" in reason,
                failures,
                f"s19-release-bundle-missing-artifact:{engine}: reason={reason!r}",
            )

        # Scenario 20: bundle with the artifact file's content changed after signing (manifest and
        # signatures untouched) -- the artifact's recomputed digest no longer matches the one the
        # manifest names, byte-identical fixed text.
        def _artifact_tampered(d: Path) -> Path:
            dest = _copy_bundle(d)
            (dest / "artifact-a.txt").write_bytes(b"tampered content\n")
            return dest

        runs = _run_release_scenario(
            "s20-release-bundle-artifact-digest-mismatch",
            _artifact_tampered,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            reason = _reason(out)
            _assert(
                reason is not None and "digest mismatch for artifact-a.txt" in reason,
                failures,
                f"s20-release-bundle-artifact-digest-mismatch:{engine}: reason={reason!r}",
            )

        # Scenario 21: bundle with an unrelated field added to release-manifest.json after
        # signing -- the artifacts[] array and the artifact file are both untouched (so neither
        # "missing artifact" nor "digest mismatch" fires), but the manifest's own recomputed digest
        # no longer matches what either signature covers: both entries report the same fixed text.
        def _manifest_digest_moved(d: Path) -> Path:
            dest = _copy_bundle(d)
            manifest_path = dest / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["note"] = "this field was added after signing"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            return dest

        runs = _run_release_scenario(
            "s21-release-bundle-manifest-digest-moved",
            _manifest_digest_moved,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            reason = _reason(out)
            _assert(
                reason is not None
                and reason.count("signature does not cover the release manifest") == 2,
                failures,
                f"s21-release-bundle-manifest-digest-moved:{engine}: reason={reason!r}",
            )

        # Scenario 22: the vendored catalog's content tampered (an extra file added) while
        # catalog.sig.json itself is untouched -- the signature still verifies, but the recomputed
        # directory digest no longer matches the one it was signed over.
        def _catalog_content_tampered(catalog_dir: Path) -> None:
            (catalog_dir / "parity-check-tamper.txt").write_text(
                "added after signing\n", encoding="utf-8"
            )

        runs = _run_catalog_scenario(
            "s22-catalog-digest-mismatch",
            EU_AI_ACT,
            tmp,
            failures,
            mutate=_catalog_content_tampered,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out)
                == "the signature covers a different catalog digest than the directory content",
                failures,
                f"s22-catalog-digest-mismatch:{engine}: reason={_reason(out)!r}",
            )

        # Scenario 23 (verifier round-1 Finding 1, HIGH/security): a bundle with no signature
        # entries at all must refuse, not report verified: true (an empty "no problems, no
        # signers" bundle used to pass in all three engines).
        def _unsigned_bundle(d: Path) -> Path:
            dest = d / "bundle"
            write_bundle_with_manifest(dest, {"artifacts": []})
            return dest

        runs = _run_release_scenario(
            "s23-release-bundle-unsigned",
            _unsigned_bundle,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out) == UNSIGNED_BUNDLE_SENTENCE,
                failures,
                f"s23-release-bundle-unsigned:{engine}: reason={_reason(out)!r}",
            )

        # Scenario 24 (verifier round-1 Finding 2): a manifest whose `artifacts` field is present
        # but not an array is treated as empty, not a crash.
        def _artifacts_not_a_list(d: Path) -> Path:
            dest = d / "bundle"
            write_bundle_with_manifest(dest, {"artifacts": None})
            return dest

        runs = _run_release_scenario(
            "s24-release-bundle-artifacts-not-a-list",
            _artifacts_not_a_list,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out) == UNSIGNED_BUNDLE_SENTENCE,
                failures,
                f"s24-release-bundle-artifacts-not-a-list:{engine}: reason={_reason(out)!r}",
            )

        # Scenario 25 (verifier round-1 Finding 3): a manifest holding a float crashes
        # `canonicalize()` in all three engines today; it must soft-fail instead.
        def _manifest_not_canonicalizable(d: Path) -> Path:
            dest = d / "bundle"
            write_bundle_with_manifest(dest, {"artifacts": [], "size": 1.5})
            return dest

        runs = _run_release_scenario(
            "s25-release-bundle-manifest-not-canonicalizable",
            _manifest_not_canonicalizable,
            tmp,
            failures,
            expect_exit=3,
            expect_verified=False,
        )
        for engine, (out, _) in runs.items():
            _assert(
                _reason(out) == "release manifest cannot be canonicalized",
                failures,
                f"s25-release-bundle-manifest-not-canonicalizable:{engine}: reason={_reason(out)!r}",
            )

        # Scenario B1 (point 4, critic round-2 finding #13): `verify --bundle` on a real,
        # already-signed evidence bundle (`corpus/quickstart/evidence`, not a synthetic fixture) --
        # this is the item's full evidence-integrity path, not only the catalog/release DSSE
        # primitives.
        _run_bundle_scenario(
            "sB1-verify-bundle-evidence",
            ["--bundle", str(EVIDENCE_BUNDLE), "--json"],
            failures,
            expect_exit=0,
        )

        run_census(canonical, tmp, failures)

    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "MATCH: verify --catalog/--release/--bundle scenarios (happy path, signature/certificate "
        "tamper, unsigned, directory-bundle, malformed-type, digest-mismatch, and evidence-bundle "
        "vectors) byte-identical across python, typescript, java"
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
