"""Multi-engine Engine Conformance Suite runner and comparator (SPEC §8.2 #9, §11.5, P3.4, P5.1).

Runs the same corpus through more than one engine and compares their outputs. The engines assess a
single materialised full corpus (so the inputs are identical to the byte), and the comparison is over
each project's whole canonical set: ``assertions.json`` and every ``packs/*/pack.json`` must be
byte-identical across conforming engines after RFC 8785 canonicalisation, and the ``oscal-ar.json`` and
``results.sarif`` translations must be identical once the one field each engine deliberately owns — its
own name and version — is neutralised. Run it as::

    cd conformance && uv run python ecs.py --engines python,typescript,java --corpus ../corpus --compare

``--compare`` exits 0 only when every engine claims ``full`` and every project's canonical set is
identical across engines; ``--json`` prints the per-engine claims and the identical count. For exactly
``python,typescript`` the JSON keeps the two-engine shape (``{"python": …, "ts": …, …}``) the phase-3
gate reads. The corpus source tree is materialised as the full set on demand, so the corpus output need
never be committed (SPEC §11.7).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# The corpus generator lives in the corpus package; make it importable from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ENGINE = REPO_ROOT / "engines" / "python"
TS_ENGINE = REPO_ROOT / "engines" / "typescript"
JAVA_ENGINE = REPO_ROOT / "engines" / "java"

SUPPORTED_ENGINES = ("python", "typescript", "java")


def _materialise_full(corpus_dir: Path) -> Path:
    """Return a directory holding the full-set ``corpus-manifest.json``, generating it if needed."""
    manifest = corpus_dir / "corpus-manifest.json"
    if (
        manifest.is_file()
        and json.loads(manifest.read_text(encoding="utf-8")).get("set") == "full"
    ):
        return corpus_dir
    if (corpus_dir / "generator" / "generate.py").is_file():
        from corpus.generator.generate import build_corpus

        out = Path(tempfile.mkdtemp(prefix="ecs-corpus-"))
        build_corpus(out, "full")
        return out
    raise SystemExit(
        f"{corpus_dir} has neither a full corpus-manifest.json nor a generator."
    )


def _run_python(corpus_root: Path, out_dir: Path) -> str:
    """Run the Python reference engine's ECS over ``corpus_root``; return its claim."""
    from agentce.conformance import run_ecs

    report = run_ecs(engine_path=PY_ENGINE, corpus_dir=corpus_root, out_dir=out_dir)
    return str(report["claim"])


def _run_typescript(corpus_root: Path, out_dir: Path) -> str:
    """Run the TypeScript engine's ECS over ``corpus_root`` (via pnpm); return its claim."""
    proc = subprocess.run(
        [
            "pnpm",
            "--silent",
            "agentce",
            "conformance",
            "run",
            "--engine",
            ".",
            "--corpus",
            str(corpus_root.resolve()),
            "--out",
            str(out_dir.resolve()),
            "--json",
        ],
        cwd=str(TS_ENGINE),
        capture_output=True,
        text=True,
        check=False,
    )
    return _claim_from_stdout("TypeScript", proc)


def java_launcher() -> Path:
    """Build the Java engine with the Gradle wrapper and return its launcher script.

    ``./gradlew installDist`` is idempotent (Gradle's up-to-date checks make it fast when the source is
    unchanged), so callers always run the freshly compiled engine, and the generated launcher gives a
    clean JSON envelope on stdout.
    """
    build = subprocess.run(
        ["./gradlew", "--no-daemon", "--quiet", "installDist"],
        cwd=str(JAVA_ENGINE),
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise SystemExit(
            f"building the Java engine failed:\n{(build.stderr or build.stdout)[-800:]}"
        )
    return JAVA_ENGINE / "build" / "install" / "agentce" / "bin" / "agentce"


def _run_java(corpus_root: Path, out_dir: Path) -> str:
    """Run the Java engine's ECS over ``corpus_root``; return its claim."""
    launcher = java_launcher()
    proc = subprocess.run(
        [
            str(launcher),
            "conformance",
            "run",
            "--engine",
            ".",
            "--corpus",
            str(corpus_root.resolve()),
            "--out",
            str(out_dir.resolve()),
            "--json",
        ],
        cwd=str(JAVA_ENGINE),
        capture_output=True,
        text=True,
        check=False,
    )
    return _claim_from_stdout("Java", proc)


def _claim_from_stdout(engine: str, proc: subprocess.CompletedProcess[str]) -> str:
    stdout = proc.stdout
    start = stdout.find("{")
    if start < 0:
        raise SystemExit(
            f"the {engine} engine produced no JSON envelope:\n{(proc.stderr or stdout)[-800:]}"
        )
    envelope = json.loads(stdout[start:])
    return str(envelope["claim"])


_RUNNERS = {"python": _run_python, "typescript": _run_typescript, "java": _run_java}


# The canonical set every conforming engine writes per project. Data artifacts are compared byte for
# byte; translations are compared after neutralising the engine identity they carry by design.
_DATA_ARTIFACTS = ("assertions.json",)
_TRANSLATION_ARTIFACTS = ("oscal-ar.json", "results.sarif")
_ENGINE_IDENTITY = "<engine>"


def _canonical_set(out_dir: Path, project_id: str) -> dict[str, bytes]:
    """Read one engine's canonical artifacts for a project, keyed by path relative to the project."""
    project = out_dir / "projects" / project_id
    found: dict[str, bytes] = {}
    for name in (*_DATA_ARTIFACTS, *_TRANSLATION_ARTIFACTS):
        path = project / name
        if path.is_file():
            found[name] = path.read_bytes()
    for pack in sorted(project.glob("packs/*/pack.json")):
        found[pack.relative_to(project).as_posix()] = pack.read_bytes()
    return found


def _identity_fields(name: str, data: bytes) -> list[tuple[str, str]]:
    """Return the (key, value) pairs a translation carries as its engine identity."""
    try:
        doc = json.loads(data)
    except ValueError:
        return []
    fields: list[tuple[str, Any]] = []
    if name == "results.sarif":
        for run in doc.get("runs", []):
            driver = run.get("tool", {}).get("driver", {})
            fields += [("name", driver.get("name")), ("version", driver.get("version"))]
    elif name == "oscal-ar.json":
        metadata = doc.get("assessment-results", {}).get("metadata", {})
        fields.append(("version", metadata.get("version")))
    return [(key, value) for key, value in fields if isinstance(value, str)]


def _without_engine_identity(name: str, data: bytes) -> bytes:
    """Blank the engine name and version a translation carries (each engine owns its own).

    Only those values are replaced in the raw text, so every other byte — key order, whitespace, escaping,
    a trailing newline — still has to match for the translations to compare equal.
    """
    text = data.decode("utf-8", errors="surrogateescape")
    for key, value in _identity_fields(name, data):
        pattern = re.compile(
            rf'("{re.escape(key)}"\s*:\s*){re.escape(json.dumps(value))}'
        )
        text = pattern.sub(rf'\1"{_ENGINE_IDENTITY}"', text)
    return text.encode("utf-8", errors="surrogateescape")


def _comparable(name: str, data: bytes) -> bytes:
    return (
        _without_engine_identity(name, data) if name in _TRANSLATION_ARTIFACTS else data
    )


def canonical_divergences(reference: Path, other: Path, project_id: str) -> list[str]:
    """Return the canonical artifacts of ``project_id`` where ``other`` differs from ``reference``.

    An artifact one engine wrote and the other did not counts as a divergence, and a reference that
    produced no ``assertions.json`` diverges outright, so an engine that wrote nothing never compares
    equal to another that wrote nothing.
    """
    expected = _canonical_set(reference, project_id)
    actual = _canonical_set(other, project_id)
    if "assertions.json" not in expected:
        return ["assertions.json"]
    return [
        name
        for name in sorted(expected.keys() | actual.keys())
        if name not in expected
        or name not in actual
        or _comparable(name, expected[name]) != _comparable(name, actual[name])
    ]


def run_multi_engine(corpus_dir: Path, engines: list[str]) -> dict[str, Any]:
    """Run every engine over one materialised full corpus and compare their canonical sets."""
    corpus_root = _materialise_full(corpus_dir)
    manifest = json.loads(
        (corpus_root / "corpus-manifest.json").read_text(encoding="utf-8")
    )
    project_ids = sorted(str(p["id"]) for p in manifest.get("projects", []))

    tmp = Path(tempfile.mkdtemp(prefix="ecs-out-"))
    claims = {name: _RUNNERS[name](corpus_root, tmp / name) for name in engines}

    reference = engines[0]
    identical = 0
    different: list[str] = []
    divergences: list[str] = []
    for pid in project_ids:
        found = [
            f"{pid}/{artifact} ({name})"
            for name in engines[1:]
            for artifact in canonical_divergences(tmp / reference, tmp / name, pid)
        ]
        if found:
            different.append(pid)
            divergences.extend(found)
        else:
            identical += 1

    return {
        "claims": claims,
        "engines": list(engines),
        "projects_total": len(project_ids),
        "projects_identical": identical,
        "different": different,
        "divergences": divergences,
    }


def run_two_engine(corpus_dir: Path) -> dict[str, Any]:
    """Run the Python and TypeScript engines and return the phase-3 two-engine shape (P3.4)."""
    result = run_multi_engine(corpus_dir, ["python", "typescript"])
    return {
        "python": result["claims"]["python"],
        "ts": result["claims"]["typescript"],
        "projects_total": result["projects_total"],
        "projects_identical": result["projects_identical"],
        "different": result["different"],
        "divergences": result["divergences"],
    }


def _all_identical(result: dict[str, Any]) -> bool:
    return (
        all(claim == "full" for claim in result["claims"].values())
        and result["projects_total"] > 0
        and result["projects_identical"] == result["projects_total"]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ecs",
        description="Multi-engine ECS runner and canonical-set comparator (SPEC §11.5, P3.4, P5.1).",
    )
    parser.add_argument(
        "--engines",
        default="python,typescript",
        help="comma-separated engines to run (any of python,typescript,java)",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPO_ROOT / "corpus",
        help="the corpus directory or source tree",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="require every engine to claim full and every project's canonical set to be identical",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    for engine in engines:
        if engine not in SUPPORTED_ENGINES:
            raise SystemExit(
                f"unsupported engine {engine!r}; expected any of {', '.join(SUPPORTED_ENGINES)}."
            )
    if len(engines) < 2:
        raise SystemExit("at least two engines are required to compare.")

    # Preserve the two-engine JSON shape for exactly python,typescript (phase-3 gate P3.4).
    if sorted(engines) == ["python", "typescript"]:
        two = run_two_engine(args.corpus)
        if args.json:
            print(json.dumps(two, sort_keys=True))
        else:
            print(
                f"python={two['python']} ts={two['ts']} "
                f"identical={two['projects_identical']}/{two['projects_total']}"
            )
            if two["divergences"]:
                print("different: " + ", ".join(two["divergences"][:10]))
        if args.compare:
            ok = (
                two["python"] == "full"
                and two["ts"] == "full"
                and two["projects_total"] > 0
                and two["projects_identical"] == two["projects_total"]
            )
            return _report_compare(ok, two["projects_identical"], two["projects_total"])
        return 0

    result = run_multi_engine(args.corpus, engines)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        claims = " ".join(f"{name}={claim}" for name, claim in result["claims"].items())
        print(
            f"{claims} identical={result['projects_identical']}/{result['projects_total']}"
        )
        if result["divergences"]:
            print("different: " + ", ".join(result["divergences"][:10]))
    if args.compare:
        return _report_compare(
            _all_identical(result),
            result["projects_identical"],
            result["projects_total"],
        )
    return 0


def _report_compare(ok: bool, identical: int, total: int) -> int:
    if ok:
        print(f"BYTE-IDENTICAL: {identical}/{total} projects, every engine claims full")
        return 0
    print(f"NOT IDENTICAL: {identical}/{total} identical")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
