"""claim_parity_check - prove Python, TypeScript, and Java `assess` write the same `claim.json`.

`claim.json` is the unsigned conformance claim (SPEC §9.1) that `agentce sign` signs. Python's
`report.py` writes it whenever a run has at least one assertion; 18.53 ports it to the TypeScript and
Java engines, so a report assessed with any engine can be signed by that engine. This check runs all
three engines' `assess` over the 30 generated corpus projects, `corpus/quickstart`, the auditor-view
fixture (an unverified catalog, a deviations register and a non-ASCII operator) and a profile with no
subjects (no assertions, so no claim), and for each run requires:

* each engine writes `claim.json` exactly when Python does;
* the file's bytes are `json.dumps(parsed, sort_keys=True, indent=2)` plus a newline (ASCII escapes);
* every key but `engine` and `claim_id` equals Python's;
* `engine` is that engine's own manifest engine block minus `package_digest`;
* `claim_id` is `sha256:` over the canonical body, recomputed by the Python engine's `agentce.canonical`.

Modes:
    claim_parity_check.py                  # three engines (needs `pnpm build` and `:assemble` first)
    claim_parity_check.py --fail-fast      # the same, stopping at the first mismatching run
    claim_parity_check.py --engine python  # Python alone, no build needed
    claim_parity_check.py --end-to-end     # each engine assesses, signs, and its signature verifies
    claim_parity_check.py --sign-refusals  # sign_parity_check's claim.json refusal vectors
    claim_parity_check.py --self-test      # every tamper class is caught
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import sign_parity_check as spc

ROOT = spc.ROOT
QUICKSTART = ROOT / "corpus" / "quickstart"
AUDITOR = ROOT / "verification" / "gates" / "fixtures" / "auditor_view"
NO_ASSERTIONS = (
    ROOT / "tools" / "fixtures" / "claim" / "no-assertions" / "applicability.yaml"
)
ENGINES = ("python", "typescript", "java")


def _engine_cmd(engine: str) -> list[str]:
    if engine == "python":
        return ["uv", "run", "--frozen", "--project", str(spc.PY_ENGINE), "agentce"]
    if engine == "typescript":
        entry = spc.TS_ENGINE / "dist" / "cli.js"
        if not entry.is_file():
            raise SystemExit(f"typescript dist is not built: {entry} is missing")
        return ["node", str(entry)]
    return ["java", "-jar", str(spc.readiness_parity_check.java_jar())]


def run_engine(
    engine: str, args: list[str], env: dict[str, str] | None = None
) -> tuple[str, int]:
    """One engine command, with `LC_ALL=C.UTF-8` and no inherited `AGENTCE_OPERATOR`."""
    full_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AGENTCE_OPERATOR", "VIRTUAL_ENV")
    }
    full_env.update({"LC_ALL": "C.UTF-8", **(env or {})})
    proc = subprocess.run(
        [*_engine_cmd(engine), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=full_env,
    )
    return proc.stdout, proc.returncode


def _project_args(project: Path, profile: Path | None = None) -> list[str]:
    return [
        "--bundle",
        str(project / "evidence"),
        "--profile",
        str(profile or project / "applicability.yaml"),
        "--domain",
        str(project / "domain.linkml.yaml"),
    ]


#: (label, assess argv, extra env, expect a claim). Python decides whether a claim exists; the
#: no-assertions run also checks that no engine writes one.
Run = tuple[str, list[str], dict[str, str], bool]


def runs(corpus: Path) -> list[Run]:
    projects = sorted(
        p.parent for p in (corpus / "projects").rglob("applicability.yaml")
    )
    found: list[Run] = [
        (str(p.relative_to(corpus / "projects")), _project_args(p), {}, True)
        for p in projects
    ]
    auditor = [
        *_project_args(AUDITOR),
        "--catalog-dir",
        str(AUDITOR / "catalog"),
        "--allow-unverified-catalog",
        "--deviations",
        str(AUDITOR / "deviations.yaml"),
    ]
    # The fixtures come first so a fault in a rarely used field fails a --fail-fast run early.
    return [
        ("quickstart", _project_args(QUICKSTART), {}, True),
        ("auditor_view", auditor, {"AGENTCE_OPERATOR": "Zo\u00eb Corp"}, True),
        ("no-assertions", _project_args(QUICKSTART, NO_ASSERTIONS), {}, False),
        *found,
    ]


def generate_corpus(out: Path) -> Path:
    subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(ROOT / "corpus" / "generator"),
            "python",
            str(ROOT / "corpus" / "generator" / "generate.py"),
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
        cwd=ROOT,
        env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"},
    )
    return out


def canonical_digests(bodies: list[Any]) -> list[str]:
    """sha256 hex of each value's canonical form, computed by the Python engine's own canonicalizer."""
    script = (
        "import hashlib, json, sys\n"
        "from agentce.canonical import canonicalize\n"
        "print(json.dumps([hashlib.sha256(canonicalize(b)).hexdigest() for b in json.load(sys.stdin)]))\n"
    )
    out, code = spc._py_engine_script(script, input_text=json.dumps(bodies))
    if code != 0:
        raise SystemExit(
            f"could not canonicalize claim bodies with the Python engine: {out}"
        )
    return list(json.loads(out))


def claim_problems(
    engine: str,
    raw: bytes | None,
    manifest: dict[str, Any] | None,
    reference: dict[str, Any] | None,
    digest: Callable[[Any], str],
) -> list[str]:
    """Everything wrong with one engine's claim.json against Python's (`reference`, None when
    Python wrote no claim). `digest` is the canonical sha256 of a body."""
    if reference is None or raw is None:
        if (reference is None) != (raw is None):
            return [
                f"{engine} wrote claim.json: {raw is not None}, python: {reference is not None}"
            ]
        return []
    try:
        claim = json.loads(raw)
    except ValueError:
        return [f"{engine}'s claim.json is not JSON"]
    problems = []
    if raw != (json.dumps(claim, sort_keys=True, indent=2) + "\n").encode("ascii"):
        problems.append(
            f"{engine}'s claim.json bytes are not json.dumps(sort_keys, indent=2) + newline"
        )
    others = {k: v for k, v in claim.items() if k not in ("engine", "claim_id")}
    expected = {k: v for k, v in reference.items() if k not in ("engine", "claim_id")}
    if others != expected:
        keys = sorted(
            k
            for k in others.keys() | expected.keys()
            if others.get(k) != expected.get(k)
        )
        problems.append(f"{engine}'s claim differs from python's at {keys}")
    own = {
        k: v
        for k, v in ((manifest or {}).get("engine") or {}).items()
        if k != "package_digest"
    }
    if claim.get("engine") != own:
        problems.append(
            f"{engine}'s claim engine {claim.get('engine')!r} is not its manifest's {own!r}"
        )
    body = {k: v for k, v in claim.items() if k != "claim_id"}
    if claim.get("claim_id") != f"sha256:{digest(body)}":
        problems.append(f"{engine}'s claim_id is not sha256 over its canonical body")
    return problems


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def check_run(run: Run, tmp: Path, engines: tuple[str, ...]) -> tuple[list[str], bool]:
    """Assess one run with each engine and compare its claim.json; returns (problems, python wrote one)."""
    label, args, env, expect_claim = run
    raws: dict[str, bytes | None] = {}
    manifests: dict[str, dict[str, Any] | None] = {}
    problems: list[str] = []
    for engine in engines:
        out_dir = tmp / label.replace("/", "_") / engine
        out, code = run_engine(
            engine, ["assess", *args, "--out", str(out_dir), "--json"], env
        )
        if not expect_claim:
            error = json.loads(out).get("error", {}) if out.strip() else {}
            key = error.get("key", error.get("message_key"))
            if code != 3 or key != "input.nothing_evaluated":
                problems.append(
                    f"{engine}: exit {code} key {key!r}, expected 3 input.nothing_evaluated"
                )
        raws[engine] = _read(out_dir / "claim.json")
        manifest_raw = _read(out_dir / "manifest.json")
        manifests[engine] = json.loads(manifest_raw) if manifest_raw else None
    py_raw = raws["python"]
    if expect_claim != (py_raw is not None):
        problems.append(
            f"python wrote claim.json: {py_raw is not None}, expected {expect_claim}"
        )
    reference = json.loads(py_raw) if py_raw is not None else None
    present = [e for e in engines if raws[e] is not None]
    bodies: list[Any] = []
    for engine in present:
        try:
            bodies.append(
                {
                    k: v
                    for k, v in json.loads(raws[engine] or b"").items()
                    if k != "claim_id"
                }
            )
        except (ValueError, AttributeError):
            bodies.append(None)
    digests = dict(
        zip((json.dumps(b, sort_keys=True) for b in bodies), canonical_digests(bodies))
    )
    for engine in engines:
        problems += claim_problems(
            engine,
            raws[engine],
            manifests[engine],
            reference,
            lambda body: digests.get(json.dumps(body, sort_keys=True), ""),
        )
    return problems, py_raw is not None


def run_check(engines: tuple[str, ...], fail_fast: bool) -> int:
    failures = 0
    written = 0
    with tempfile.TemporaryDirectory(prefix="claim-parity-") as raw:
        tmp = Path(raw)
        for run in runs(generate_corpus(tmp / "corpus")):
            problems, wrote = check_run(run, tmp, engines)
            written += wrote
            if problems:
                failures += 1
                for problem in problems:
                    print(f"MISMATCH: {run[0]}: {problem}", file=sys.stderr)
                if fail_fast:
                    break
            else:
                print(f"MATCH: {run[0]}")
    if failures:
        return 1
    if engines == ("python",):
        print(f"{written} claims written")
    else:
        print("CLAIM PARITY OK")
    return 0


def _verify_signed(report: Path, problems: list[str], engine: str) -> None:
    """The one claimant signature verifies against the written trust-root.json with Python's own
    `signing.verify_envelope`, and its subjects are the claim body and the manifest bytes."""
    claim = json.loads((report / "claim.json").read_bytes())
    signatures = claim.get("signatures") or []
    if len(signatures) != 1 or signatures[0].get("role") != "claimant":
        problems.append(
            f"{engine}: expected one claimant signature, got {len(signatures)}"
        )
        return
    trust = json.loads((report / "trust-root.json").read_bytes())
    [(keyid, entry)] = trust["keys"].items()
    ok, out = spc.verify_offline(signatures[0], entry["public_key"], keyid)
    if out.strip() != "VERIFIED":
        problems.append(
            f"{engine}: envelope did not verify against trust-root.json ({out.strip()})"
        )
    else:
        print(f"{engine}: envelope OK")
    statement = json.loads(base64.b64decode(signatures[0]["payload"]))
    got = {s["name"]: s["digest"]["sha256"] for s in statement.get("subject", [])}
    body = {k: v for k, v in claim.items() if k != "signatures"}
    want = {
        "claim.json": canonical_digests([body])[0],
        "manifest.json": hashlib.sha256(
            (report / "manifest.json").read_bytes()
        ).hexdigest(),
    }
    if got != want:
        problems.append(
            f"{engine}: signed subjects {got} are not the claim body and manifest {want}"
        )
    else:
        print(f"{engine}: subjects OK")


def run_end_to_end() -> int:
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="claim-e2e-") as raw:
        for engine in ENGINES:
            report = Path(raw) / engine
            _, code = run_engine(
                engine, ["assess", *_project_args(QUICKSTART), "--out", str(report)]
            )
            print(f"{engine}: assess {code}")
            sign = [
                "sign",
                str(report),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(spc.TEST_KEY),
                "--write-trust-root",
                "--json",
            ]
            out, sign_code = run_engine(engine, sign)
            print(f"{engine}: sign {sign_code}")
            if code != 0 or sign_code != 0:
                problems.append(
                    f"{engine}: assess {code}, sign {sign_code} ({out.strip()[:200]})"
                )
                continue
            _verify_signed(report, problems, engine)
            if engine == "python":
                _, verify_code = run_engine(
                    engine, ["verify", "--report", str(report), "--json"]
                )
                print(f"{engine}: verify --report {verify_code}")
                if verify_code != 0:
                    problems.append(f"python: verify --report exit {verify_code}")
    for problem in problems:
        print(f"MISMATCH: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("END-TO-END OK")
    return 0


def run_sign_refusals() -> int:
    """sign_parity_check's claim.json refusal vectors (sign.claim_malformed and the dry run), run on
    their own for VG-CLAIM-PARITY."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="claim-refusals-") as raw:
        spc.run_claim_malformed_scenarios(Path(raw), failures)
    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("SIGN REFUSALS OK")
    return 0


def self_test() -> int:
    """Each tamper class on a correct claim is caught, and the correct claim passes."""
    manifest = {
        "engine": {
            "impl": "agentce-ts",
            "version": "0.1.0",
            "spec_version": "0.6",
            "package_digest": "x",
        }
    }
    body: dict[str, Any] = {
        "catalogs": [{"digest": "sha256:00", "id": "c", "version": "1"}],
        "claimant": {"org": "Zoë Corp"},
        "engine": {
            k: v for k, v in manifest["engine"].items() if k != "package_digest"
        },
        "statement": "s",
        "subjects": [{"id": "a", "role": "both"}],
    }
    reference = {
        **body,
        "engine": {"impl": "agentce-py", "version": "0.1.0", "spec_version": "0.6"},
    }

    def digest(value: Any) -> str:
        return canonical_digests([value])[0]

    def claim(b: dict[str, Any]) -> dict[str, Any]:
        return {"claim_id": f"sha256:{digest(b)}", **b}

    def dumps(value: Any) -> bytes:
        return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("ascii")

    good = claim(body)
    reference = claim(reference)
    other = copy.deepcopy(body)
    other["claimant"]["org"] = "other"
    wrong_engine = {**body, "engine": reference["engine"]}
    tampers: list[tuple[str, bytes | None, dict[str, Any] | None]] = [
        ("format", (json.dumps(good, indent=4) + "\n").encode(), reference),
        (
            "non-ASCII unescaped",
            (
                json.dumps(good, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
            ).encode(),
            reference,
        ),
        ("a body value", dumps(claim(other)), reference),
        ("engine block not the manifest's", dumps(claim(wrong_engine)), reference),
        (
            "claim_id over the wrong body",
            dumps({**body, "claim_id": reference["claim_id"]}),
            reference,
        ),
        ("claim missing", None, reference),
        ("claim written when python wrote none", dumps(good), None),
    ]
    failures = []
    if claim_problems("typescript", dumps(good), manifest, reference, digest):
        failures.append("a correct claim was flagged")
    for name, raw, ref in tampers:
        if claim_problems("typescript", raw, manifest, ref, digest):
            print(f"caught: {name}")
        else:
            failures.append(f"tamper not caught: {name}")
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("SELF-TEST OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claim_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--end-to-end", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--sign-refusals", action="store_true")
    parser.add_argument("--engine", choices=["python"], default=None)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.end_to_end:
        return run_end_to_end()
    if args.sign_refusals:
        return run_sign_refusals()
    return run_check(("python",) if args.engine else ENGINES, args.fail_fast)


if __name__ == "__main__":
    raise SystemExit(main())
