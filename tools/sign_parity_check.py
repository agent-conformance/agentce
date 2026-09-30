"""sign_parity_check - prove Python, TypeScript, and Java compute an identical `agentce sign` for
the `kms` profile.

`agentce sign`'s job (18.26) is a DSSE/in-toto signature over a report's `claim.json`, refusing
unless the report is ready to publish (SPEC §8.5, §9.1). Three independent per-engine unit-test
suites (18.26's C1/C2) can pass with three independently-wrong-but-matching-in-no-way
implementations, since none of them ever compares against another engine's real output. This check
does, mirroring `readiness_parity_check.py`'s shape (18.25's C3) -- reusing its own READY/NOT_READY
report-directory fixtures directly, proving `sign` and `readiness` see the same input the same way.

Ed25519 signing is deterministic (RFC 8032) for a given key and message, so this is the one place in
this repository's parity checks that can assert **signature bytes themselves** match across all
three engines, not just the verdict/JSON shape -- using a fixed, checked-in test key
(`tools/fixtures/sign/test-key.pem`).

* `--self-test` needs no build: the comparator (a tamper-detection check), the reused readiness
  fixtures' own shape, and the checked-in key fixtures' PEM labels (proving the fixture, not any
  engine).
* The real invocation runs Python's own `agentce sign` (the reference), the built TypeScript
  `dist/cli.js`, and the built Java runnable jar, over nine scenarios -- comparing the `--json`
  envelope, the written `claim.json`/detached-signature/`trust-root.json` files (byte-identical
  across engines), and independently verifying every produced signature against Python's own
  `signing.verify_envelope` (the checker cannot import `agentce.signing` directly: `tools/pyproject.toml`
  depends only on `pyyaml`, so every cryptographic step shells into `engines/python`'s own
  environment, the same boundary `readiness_parity_check.py`'s own Python-engine invocation crosses).

Usage:
    sign_parity_check.py             # needs `pnpm build` (TS) and `:assemble` (Java) run first
    sign_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import functools
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import readiness_parity_check

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"
FIXTURES = ROOT / "tools" / "fixtures" / "sign"

TEST_KEY = FIXTURES / "test-key.pem"
RSA_KEY = FIXTURES / "rsa-key.pem"
EC_KEY = FIXTURES / "ec-key.pem"
ENCRYPTED_KEY = FIXTURES / "encrypted-key.pem"


def _run(
    cmd: list[str], *, cwd: Path | None = None, input_text: str | None = None
) -> tuple[str, int]:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, input=input_text
    )
    return proc.stdout, proc.returncode


def _py_engine_script(script: str, *, input_text: str | None = None) -> tuple[str, int]:
    """Runs a short Python script inside `engines/python`'s own `uv` environment -- the only place
    `cryptography` is a dependency in this repository; `tools/pyproject.toml` never gains it."""
    return _run(
        ["uv", "run", "--frozen", "--project", str(PY_ENGINE), "python", "-c", script],
        cwd=ROOT,
        input_text=input_text,
    )


@functools.cache
def sign_profiles() -> tuple[str, ...]:
    """`SIGN_PROFILES` read out of the engine's own environment (the `known_test_key()` pattern)
    rather than sliced out of the source text, so a reordering or rename is picked up automatically."""
    script = (
        "import json\n"
        "from agentce.commands import SIGN_PROFILES\n"
        "print(json.dumps(list(SIGN_PROFILES)))\n"
    )
    out, code = _py_engine_script(script)
    if code != 0:
        raise SystemExit(f"could not read SIGN_PROFILES from the engine: {out}")
    return tuple(json.loads(out))


@functools.cache
def known_test_key() -> tuple[str, str]:
    """`(keyid, public_key_b64)` for the fixed, checked-in Ed25519 test key -- derived once, never
    hardcoded, so a regenerated fixture is picked up automatically."""
    script = (
        "import json\n"
        "from cryptography.hazmat.primitives.serialization import load_pem_private_key\n"
        "from agentce import signing\n"
        f"key = load_pem_private_key(open({str(TEST_KEY)!r}, 'rb').read(), password=None)\n"
        "print(json.dumps([signing.keyid_for(key.public_key()), "
        "signing.public_ed25519_b64(key.public_key())]))\n"
    )
    out, code = _py_engine_script(script)
    if code != 0:
        raise SystemExit(f"could not derive the known test key's public half: {out}")
    keyid, pub_b64 = json.loads(out)
    return keyid, pub_b64


def verify_offline(
    detached: dict[str, Any], public_key_b64: str, keyid: str
) -> tuple[bool, str]:
    """Independently verifies a DSSE envelope against the known test key's public half, using
    Python's own reference `signing.verify_envelope` -- proving an engine's output is a signature
    Python's own verifier accepts, not only that the three engines agree with each other."""
    script = (
        "import json, sys\n"
        "from agentce import signing\n"
        "envelope = json.load(sys.stdin)\n"
        f"trust = signing.TrustRoot.document({keyid!r}, {public_key_b64!r}, 'checker')\n"
        "try:\n"
        "    signing.verify_envelope(envelope, signing.TrustRoot.from_dict(trust))\n"
        "    print('VERIFIED')\n"
        "except signing.VerificationError as exc:\n"
        "    print(f'FAILED: {exc}')\n"
    )
    out, code = _py_engine_script(script, input_text=json.dumps(detached))
    out = out.strip()
    return code == 0 and out == "VERIFIED", out


def python_sign(args: list[str]) -> tuple[str, int]:
    return _run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(PY_ENGINE),
            "agentce",
            "sign",
            *args,
        ],
        cwd=ROOT,
    )


def typescript_sign(args: list[str]) -> tuple[str, int]:
    entry = TS_ENGINE / "dist" / "cli.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing "
            "(run `pnpm build` in engines/typescript first)"
        )
    return _run(["node", str(entry), "sign", *args], cwd=ROOT)


def java_sign(args: list[str]) -> tuple[str, int]:
    return _run(
        ["java", "-jar", str(readiness_parity_check.java_jar()), "sign", *args],
        cwd=ROOT,
    )


def _run_engines(argv: list[str]) -> list[tuple[str, tuple[str, int]]]:
    return [
        ("python", python_sign(["--json", *argv])),
        ("typescript", typescript_sign(["--json", *argv])),
        ("java", java_sign(["--json", *argv])),
    ]


def _named_readiness_scenario(name: str) -> Any:
    return next(s for s in readiness_parity_check.SCENARIOS if s.name == name)


def ready_fixture(directory: Path, claim: dict[str, Any] | None = None) -> Path:
    """The READY report-directory shape (18.25's scenario 1, reused directly) plus a `claim.json` to
    sign -- one independent copy per engine, so each engine's own `signatures[]` append is compared
    without chaining through a shared mutable file."""
    directory.mkdir(parents=True, exist_ok=True)
    report, _ = _named_readiness_scenario("1-clean").build(directory)
    (report / "claim.json").write_text(
        json.dumps(claim if claim is not None else {"claimant": {"org": "acme"}}),
        encoding="utf-8",
    )
    return report


def not_ready_fixture(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    report, _ = _named_readiness_scenario("3-insufficient-evidence-no-gaps").build(
        directory
    )
    (report / "claim.json").write_text(json.dumps({}), encoding="utf-8")
    return report


def _per_engine_dirs(tmp: Path, name: str) -> tuple[Path, Path, Path]:
    dirs = [tmp / name / engine for engine in ("python", "typescript", "java")]
    for d in dirs:
        d.mkdir(parents=True)
    return dirs[0], dirs[1], dirs[2]


def _build_reports(
    dirs: tuple[Path, Path, Path], fixture_fn: Any, *args: Any
) -> dict[str, Path]:
    """One independent `fixture_fn(dir, *args)` call per engine's own directory -- the
    per-scenario `{"python": ..., "typescript": ..., "java": ...}` shape every scenario below needs."""
    py_dir, ts_dir, java_dir = dirs
    return {
        "python": fixture_fn(py_dir, *args),
        "typescript": fixture_fn(ts_dir, *args),
        "java": fixture_fn(java_dir, *args),
    }


#: (name, key, argv extra, expect_key) for every refusal scenario (contract C3's numbered list).
#: `argv extra` is appended after the READY report-dir positional; every engine must refuse with
#: `expect_key` and the identical cause/fix text.
def _error_scenarios(directory: Path) -> list[tuple[str, list[str], str]]:
    ready = ready_fixture(directory / "errors")
    return [
        (
            "5-rsa-key-algorithm",
            [str(ready), "--as", "claimant", "--profile", "kms", "--key", str(RSA_KEY)],
            "sign.key_algorithm",
        ),
        (
            "5b-ec-sec1-key-algorithm",
            [str(ready), "--as", "claimant", "--profile", "kms", "--key", str(EC_KEY)],
            "sign.key_algorithm",
        ),
        (
            "6-encrypted-key-unreadable",
            [
                str(ready),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(ENCRYPTED_KEY),
            ],
            "sign.key_unreadable",
        ),
        (
            "7-default-profile-keyless-offline",
            [str(ready), "--as", "claimant"],
            "sign.keyless_offline",
        ),
        ("8-as-bogus", [str(ready), "--as", "bogus"], "input.sign_role"),
        (
            "9-profile-quote-apostrophe",
            [str(ready), "--as", "claimant", "--profile", "it's"],
            "input.sign_profile",
        ),
    ]


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

    for name, expected_label in [
        ("test-key.pem", "-----BEGIN PRIVATE KEY-----"),
        ("rsa-key.pem", "-----BEGIN PRIVATE KEY-----"),
        ("ec-key.pem", "-----BEGIN EC PRIVATE KEY-----"),
        ("encrypted-key.pem", "-----BEGIN ENCRYPTED PRIVATE KEY-----"),
    ]:
        path = FIXTURES / name
        if not path.is_file():
            failures.append(f"fixture {name} is missing")
            continue
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        if first_line != expected_label:
            failures.append(
                f"fixture {name}: first PEM line is {first_line!r}, expected {expected_label!r} "
                "(the label this check's own scenario selection depends on)"
            )
    if EC_KEY.is_file() and TEST_KEY.is_file():
        if EC_KEY.read_text(encoding="utf-8") == TEST_KEY.read_text(encoding="utf-8"):
            failures.append(
                "ec-key.pem and test-key.pem are byte-identical (fixture generation bug)"
            )

    profiles = sign_profiles()
    if len(profiles) != 3 or "kms" not in profiles:
        failures.append(
            f"sign_profiles() returned {profiles!r}, expected 3 names incl. 'kms'"
        )

    with tempfile.TemporaryDirectory(prefix="sign-parity-selftest-") as raw:
        tmp = Path(raw)
        ready = ready_fixture(tmp / "ready")
        if not (ready / "claim.json").is_file():
            failures.append("ready_fixture did not write claim.json")
        not_ready = not_ready_fixture(tmp / "not-ready")
        integrity = [
            json.loads(line)
            for line in (not_ready / "integrity.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        if not integrity and not json.loads(
            (not_ready / "assertions.json").read_text(encoding="utf-8")
        ):
            failures.append(
                "not_ready_fixture's reused readiness scenario carries no blocking finding"
            )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(
            "sign_parity_check self-test: comparator + fixture-shape cases discriminate"
        )
    return 1 if failures else 0


def _assert_valid_detached(
    path_str: str | None, failures: list[str], label: str
) -> dict[str, Any] | None:
    if not path_str or not Path(path_str).is_file():
        failures.append(
            f"{label}: detached signature file {path_str!r} does not exist on disk"
        )
        return None
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def run_real_check() -> int:
    failures: list[str] = []
    keyid, pub_b64 = known_test_key()

    with tempfile.TemporaryDirectory(prefix="sign-parity-") as raw:
        tmp = Path(raw)

        # Scenario 1: READY + kms happy path -- byte-identical claim.json signatures[-1] entry and
        # detached signature file, each independently verifying against the known test key.
        dirs = _per_engine_dirs(tmp, "s1")
        reports = _build_reports(dirs, ready_fixture)
        runners = {
            "python": python_sign,
            "typescript": typescript_sign,
            "java": java_sign,
        }
        envelopes: dict[str, dict[str, Any]] = {}
        detached: dict[str, dict[str, Any]] = {}
        for engine, report in reports.items():
            out, code = runners[engine](
                [
                    "--json",
                    str(report),
                    "--as",
                    "claimant",
                    "--profile",
                    "kms",
                    "--key",
                    str(TEST_KEY),
                ]
            )
            if code != 0:
                failures.append(
                    f"s1-ready:{engine}: exit {code}, expected 0 ({out.strip()[:200]!r})"
                )
                continue
            envelopes[engine] = json.loads(out)
            record = _assert_valid_detached(
                envelopes[engine].get("signature"), failures, f"s1-ready:{engine}"
            )
            if record is not None:
                detached[engine] = record
                claim = json.loads((report / "claim.json").read_text(encoding="utf-8"))
                if claim.get("signatures") != [record]:
                    failures.append(
                        f"s1-ready:{engine}: claim.json's signatures does not equal the written detached record"
                    )
                ok, verify_out = verify_offline(record, pub_b64, keyid)
                if not ok:
                    failures.append(
                        f"s1-ready:{engine}: signature did not verify offline against the known test key ({verify_out})"
                    )

        if len(detached) == 3:
            readiness_parity_check.compare_ports(
                "s1-ready:detached-signature",
                [
                    json.dumps(detached[e], sort_keys=True)
                    for e in ("python", "typescript", "java")
                ],
                failures,
            )
        if len(envelopes) == 3:
            readiness_parity_check.compare_ports(
                "s1-ready:keyid",
                [
                    str(envelopes[e].get("keyid"))
                    for e in ("python", "typescript", "java")
                ],
                failures,
            )

        # Scenario 2: --write-trust-root -- byte-identical trust-root.json, keys[keyid].public_key
        # matches the known test key exactly.
        dirs = _per_engine_dirs(tmp, "s2")
        reports = _build_reports(
            dirs, ready_fixture, {"claimant": {"org": "acme corp"}}
        )
        trust_roots: dict[str, dict[str, Any]] = {}
        for engine, report in reports.items():
            out, code = runners[engine](
                [
                    "--json",
                    str(report),
                    "--as",
                    "assessor",
                    "--profile",
                    "kms",
                    "--key",
                    str(TEST_KEY),
                    "--write-trust-root",
                ]
            )
            if code != 0:
                failures.append(
                    f"s2-trust-root:{engine}: exit {code}, expected 0 ({out.strip()[:200]!r})"
                )
                continue
            env = json.loads(out)
            path = env.get("trust_root")
            if not path or not Path(path).is_file():
                failures.append(
                    f"s2-trust-root:{engine}: trust_root file {path!r} does not exist on disk"
                )
                continue
            trust_roots[engine] = json.loads(Path(path).read_text(encoding="utf-8"))
            entry = trust_roots[engine].get("keys", {}).get(env.get("keyid"), {})
            if entry.get("public_key") != pub_b64:
                failures.append(
                    f"s2-trust-root:{engine}: keys[keyid].public_key does not match the known test key"
                )
            if entry.get("identity") != "acme corp":
                failures.append(
                    f"s2-trust-root:{engine}: keys[keyid].identity={entry.get('identity')!r}, expected 'acme corp'"
                )
        if len(trust_roots) == 3:
            readiness_parity_check.compare_ports(
                "s2-trust-root:document",
                [
                    json.dumps(trust_roots[e], sort_keys=True)
                    for e in ("python", "typescript", "java")
                ],
                failures,
            )

        # Scenario 3: NOT_READY -- refuses with sign.not_ready, identical verdict/reasons text.
        dirs = _per_engine_dirs(tmp, "s3")
        reports = _build_reports(dirs, not_ready_fixture)
        outs: dict[str, str] = {}
        for engine, report in reports.items():
            out, code = runners[engine](
                [
                    "--json",
                    str(report),
                    "--as",
                    "claimant",
                    "--profile",
                    "kms",
                    "--key",
                    str(TEST_KEY),
                ]
            )
            if code != 3:
                failures.append(
                    f"s3-not-ready:{engine}: exit {code}, expected 3 ({out.strip()[:200]!r})"
                )
            outs[engine] = out
            claim = json.loads((report / "claim.json").read_text(encoding="utf-8"))
            if "signatures" in claim:
                failures.append(
                    f"s3-not-ready:{engine}: claim.json was touched despite the NOT_READY refusal"
                )
        readiness_parity_check.compare_ports(
            "s3-not-ready:error",
            [
                readiness_parity_check._error_fields(outs[e])
                for e in ("python", "typescript", "java")
            ],
            failures,
        )
        for engine in ("python", "typescript", "java"):
            key = json.loads(outs[engine]).get("error", {})
            got = key.get("key", key.get("message_key"))
            if got != "sign.not_ready":
                failures.append(
                    f"s3-not-ready:{engine}: error key={got!r}, expected 'sign.not_ready'"
                )

        # Scenario 4: --dry-run on the READY fixture -- claim.json byte-for-byte unchanged, no
        # signatures/ directory, even with a bad --key value (no key ever touched).
        dirs = _per_engine_dirs(tmp, "s4")
        reports = _build_reports(dirs, ready_fixture)
        for engine, report in reports.items():
            before = (report / "claim.json").read_bytes()
            out, code = runners[engine](
                [
                    "--json",
                    str(report),
                    "--as",
                    "claimant",
                    "--profile",
                    "kms",
                    "--key",
                    str(report / "does-not-exist.pem"),
                    "--dry-run",
                ]
            )
            if code != 0:
                failures.append(
                    f"s4-dry-run:{engine}: exit {code}, expected 0 ({out.strip()[:200]!r})"
                )
            after = (report / "claim.json").read_bytes()
            if before != after:
                failures.append(
                    f"s4-dry-run:{engine}: claim.json changed despite --dry-run"
                )
            if (report / "signatures").exists():
                failures.append(
                    f"s4-dry-run:{engine}: a signatures/ directory was created despite --dry-run"
                )
            env = json.loads(out)
            if env.get("dry_run") is not True or env.get("readiness") != "READY":
                failures.append(
                    f"s4-dry-run:{engine}: envelope is {env!r}, expected dry_run=true, readiness='READY'"
                )

        # Scenarios 5/5b/6/7/8/9: every refusal carries the identical message key, cause, and fix.
        for name, argv, expect_key in _error_scenarios(tmp):
            runs = _run_engines(argv)
            codes = {engine: code for engine, (_, code) in runs}
            if set(codes.values()) != {3}:
                failures.append(
                    f"{name}: expected exit 3 in all three engines, got {codes}"
                )
            readiness_parity_check.compare_ports(
                f"{name}:error",
                [readiness_parity_check._error_fields(out) for _, (out, _) in runs],
                failures,
            )
            for engine, (out, _) in runs:
                got = json.loads(out).get("error", {})
                key = got.get("key", got.get("message_key"))
                if key != expect_key:
                    failures.append(
                        f"{name}:{engine}: error key={key!r}, expected {expect_key!r}"
                    )

    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "MATCH: sign kms-profile scenarios (happy path, trust-root, NOT_READY, dry-run, and 6 "
        "refusal shapes) byte-identical and offline-verifying across python, typescript, java"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sign_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_real_check()


if __name__ == "__main__":
    raise SystemExit(main())
