"""catalog_digest_check - prove Python, TypeScript, and Java compute the identical catalog digest.

``manifest.json``'s ``inputs.catalogs[].digest`` field exists so a reader can independently verify the
catalog an assessment was run against (SPEC §14.5 CP-3); a value all three engines write only when it
happens to be right, or a value copied from a catalog's own (possibly stale) ``provenance.digest``
field, verifies nothing. This check proves the three engines' catalog-content digest is the same
algorithm, not merely independently non-zero.

* ``--self-test`` needs no build: it re-implements the digest algorithm once, directly in this script
  (never importing ``agentce`` -- this script runs under ``tools``' own environment, not the Python
  engine's), and proves that reference implementation against the shared fixture
  ``spec/model/test-vectors/digest-tree/`` and its pinned hash: (1) the untampered fixture matches the
  pinned digest exactly; (2) tampering one byte of a non-excluded file changes the digest; (3) a stale,
  wrong ``provenance.digest`` value written into the fixture's own (always-excluded) ``catalog.yaml`` is
  ignored -- the digest is recomputed from the other files, never read off that field.
* The real invocation (a real catalog directory, e.g. ``spec/catalogs/base/baseline``) shells to each
  engine's real implementation -- the Python engine's own ``catalog_provenance_digest`` (via
  ``uv run --project engines/python``, so this script's own environment never needs ``agentce``
  installed), the built TypeScript ``dist/seams.js``'s ``digest-tree`` seam, and the built Java runnable
  jar's ``digest-tree`` verb -- and asserts all three sha256 strings are byte-identical.

Usage:
    catalog_digest_check.py <catalog-dir>   # needs `pnpm build` (TS) and `:assemble` (Java) run first
    catalog_digest_check.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"
FIXTURE = ROOT / "spec" / "model" / "test-vectors" / "digest-tree"
EXPECTED_FILE = ROOT / "spec" / "model" / "test-vectors" / "digest-tree.expected"
#: The same exclusion set `agentce.catalog.catalog_provenance_digest` uses: the detached signature and
#: `catalog.yaml` itself (which carries the provenance block), so the digest is non-circular.
PROVENANCE_EXCLUDE = frozenset({"catalog.sig.json", "catalog.yaml"})


def digest_tree(root: Path, exclude: frozenset[str] = PROVENANCE_EXCLUDE) -> str:
    """A from-scratch reimplementation of `engines/python/agentce/signing.py`'s `digest_tree`.

    Kept independent of the `agentce` package (this script runs under `tools`' own `uv` environment,
    which does not install the engine) so the self-test proves this reference implementation, and the
    real invocation below proves the engines against it and each other.
    """

    def rel_posix(path: Path) -> str:
        return path.relative_to(root).as_posix()

    lines: list[bytes] = []
    for path in sorted(root.rglob("*"), key=rel_posix):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root)
        rel = rel_path.as_posix()
        if rel in exclude or any(
            part.startswith(".") or part == "__pycache__" for part in rel_path.parts
        ):
            continue
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(rel.encode("utf-8") + b"\x00" + file_hash.encode("ascii"))
    return "sha256:" + hashlib.sha256(b"\n".join(lines)).hexdigest()


def _run_or_die(cmd: list[str], label: str, *, cwd: Path | None = None) -> str:
    """Run `cmd`, returning its stripped stdout, or raising with `label` and stderr on failure --
    shared by the three engines' digest invocations below, which differ only in the command."""
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        raise SystemExit(f"{label} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def python_engine_digest(directory: Path) -> str:
    """The real Python engine's own digest, run in its own project environment."""
    return _run_or_die(
        [
            "uv",
            "run",
            "--project",
            str(PY_ENGINE),
            "--frozen",
            "python3",
            "-c",
            "import sys\n"
            "from pathlib import Path\n"
            "from agentce.catalog import catalog_provenance_digest\n"
            "print(catalog_provenance_digest(Path(sys.argv[1])))\n",
            str(directory),
        ],
        "python engine digest",
        cwd=ROOT,
    )


def typescript_digest(directory: Path) -> str:
    """The built TypeScript engine's `digest-tree` verb (`pnpm build` must have run first)."""
    entry = TS_ENGINE / "dist" / "seams.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing "
            "(run `pnpm build` in engines/typescript first)"
        )
    return _run_or_die(
        ["node", str(entry), "digest-tree", str(directory)], "typescript digest-tree"
    )


def java_digest(directory: Path) -> str:
    """The built Java engine's `digest-tree` verb (`./gradlew :assemble` must have run first)."""
    jars = sorted(
        (JAVA_ENGINE / "build" / "libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jars:
        raise SystemExit(
            "java runnable jar is not built "
            "(run `./gradlew :assemble --offline -q` in engines/java first)"
        )
    return _run_or_die(
        ["java", "-cp", str(jars[-1]), "org.agentce.Seams", "digest-tree", str(directory)],
        "java digest-tree",
    )


def self_test() -> int:
    failures: list[str] = []
    expected = EXPECTED_FILE.read_text(encoding="utf-8").strip()

    # 1. the untampered fixture matches the pinned expectation (catches an ordering or
    #    exclude-matching bug in this reference implementation before any cross-engine run).
    got = digest_tree(FIXTURE)
    if got != expected:
        failures.append(f"fixture digest {got!r} != pinned expectation {expected!r}")

    with tempfile.TemporaryDirectory(prefix="catalog-digest-selftest-") as raw:
        tmp = Path(raw)

        # 2. tampering one byte of a non-excluded file changes the digest (catches a check that
        #    always returns the same value regardless of content).
        tampered = tmp / "tampered"
        shutil.copytree(FIXTURE, tampered)
        readme = tampered / "readme.txt"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8"
        )
        tampered_digest = digest_tree(tampered)
        if tampered_digest == expected:
            failures.append("tampering a non-excluded file did not change the digest")

        # 3. a deliberately wrong `provenance.digest` written into the fixture's own (always
        #    excluded) catalog.yaml is ignored: the digest is recomputed from the other files, never
        #    read off that field (catches "read the stored value" masquerading as "recompute it").
        stale = tmp / "stale"
        shutil.copytree(FIXTURE, stale)
        catalog_yaml = stale / "catalog.yaml"
        catalog_yaml.write_text(
            catalog_yaml.read_text(encoding="utf-8")
            + 'provenance:\n  digest: "sha256:'
            + "0" * 64
            + '"\n',
            encoding="utf-8",
        )
        stale_digest = digest_tree(stale)
        if stale_digest != expected:
            failures.append(
                "a stale provenance.digest written into catalog.yaml changed the computed digest "
                "(catalog.yaml is excluded and must be ignored, not read)"
            )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("catalog_digest_check self-test: 3 cases discriminate")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="catalog_digest_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "directory", nargs="?", help="a real catalog directory to check"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.directory is None:
        parser.error("a catalog directory is required (or pass --self-test)")
    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        parser.error(f"not a directory: {directory}")
    digests = {
        "python": python_engine_digest(directory),
        "typescript": typescript_digest(directory),
        "java": java_digest(directory),
    }
    for engine, digest in digests.items():
        print(f"{engine}: {digest}")
    if len(set(digests.values())) != 1:
        print(
            "MISMATCH: the three engines do not agree on this catalog's digest",
            file=sys.stderr,
        )
        return 1
    print("MATCH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
