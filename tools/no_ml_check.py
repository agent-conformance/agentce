#!/usr/bin/env python3
"""no_ml_check - enforce the no-learned-components rule (SPEC 8.7, HR-1/HR-2).

The engine and every evaluation-path script must resolve to a dependency tree with no machine-learning
framework and no LLM or embedding client. This tool scans every ecosystem lockfile in the repository --
the Python `uv.lock`, the TypeScript engine's `pnpm-lock.yaml`, and the Java engine's Gradle
`gradle.lockfile` -- for any package named in `spec/rules/no-ml-denylist.txt`, so the invariant is
enforced across all three engines' dependency trees rather than the Python one alone.

Distribution names are compared by exact PEP 503 normalisation (lowercase; runs of `-`, `_`, `.`
collapsed to one `-`), never as a substring, so a package whose name merely contains "ml" -- linkml,
pyyaml, html5lib -- is never mistaken for a machine-learning package. The single denylist governs all
three ecosystems; there is never a second list to drift out of sync.

    no_ml_check.py             scan every lockfile; exit 1 if any locked package is denylisted
    no_ml_check.py --self-test prove the matcher and every lockfile parser catch a denylisted name
                               and clear a clean set

Uses only the standard library (no PyYAML): the no-ml CI job runs it with a bare Python and no
install step. No network, no learned component.
"""

from __future__ import annotations

import re
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DENYLIST = ROOT / "spec" / "rules" / "no-ml-denylist.txt"

# Vendored, generated, and build-cache trees: any lockfile inside them is a copy of a third party's
# dependencies, not a dependency the project declares, so it is never scanned -- extending the original
# `.venv` exclusion (uv) to `node_modules` (pnpm) and `.gradle` (Gradle's own cache).
EXCLUDE_DIRS = frozenset({".venv", "node_modules", ".gradle"})


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def load_denylist(path: Path) -> set[str]:
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(normalize(line))
    return out


def packages_in_uv_lock(lock_path: Path) -> set[str]:
    """Normalised distribution names locked in a uv.lock (TOML)."""
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return {normalize(pkg["name"]) for pkg in data.get("package", []) if "name" in pkg}


# A key line in a pnpm-lock.yaml `packages:` section: two-space indent, an optionally single-quoted
# `name@version` (peer suffix allowed), ending in a colon. Deeper-indented value lines (`resolution:`)
# do not match, so only the package keys are read.
_PNPM_KEY = re.compile(r"^  ('?)(?P<key>[^\s].*?)\1:\s*$")


def packages_in_pnpm_lock(lock_path: Path) -> set[str]:
    """Normalised package names resolved in a pnpm-lock.yaml (v9). Parsed with the standard library
    only by reading the single `packages:` section, whose keys are the canonical `name@version` of every
    resolved package (a scoped package keeps its leading `@scope/`)."""
    names: set[str] = set()
    in_packages = False
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("packages:"):
            in_packages = True
            continue
        if not in_packages:
            continue
        if raw and not raw[0].isspace():  # a new top-level section (snapshots:, etc.) ends packages:
            break
        m = _PNPM_KEY.match(raw)
        if not m:
            continue
        key = m.group("key").split("(", 1)[0]  # drop any (peer@dep) suffix
        at = key.rfind("@")  # split off @version; rfind skips a leading scope '@' at index 0
        name = key[:at] if at > 0 else key
        names.add(normalize(name))
    return names


def packages_in_gradle_lock(lock_path: Path) -> set[str]:
    """Normalised artifact (module) names in a Gradle `gradle.lockfile`: one
    `group:artifact:version=configurations` per line, plus comment lines and a trailing `empty=...`
    line that carry no coordinate and are skipped."""
    names: set[str] = set()
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        coord = line.split("=", 1)[0]  # group:artifact:version
        parts = coord.split(":")
        if len(parts) >= 3:
            names.add(normalize(parts[1]))
    return names


# One parser per lockfile kind; the filename selects it, and all three feed the one denylist.
LOCK_PARSERS = {
    "uv.lock": packages_in_uv_lock,
    "pnpm-lock.yaml": packages_in_pnpm_lock,
    "gradle.lockfile": packages_in_gradle_lock,
}


def find_locks(root: Path) -> list[Path]:
    """Every lockfile of every ecosystem, anywhere in the tree, minus vendored/build-cache dirs."""
    out: list[Path] = []
    for name in LOCK_PARSERS:
        out.extend(p for p in root.rglob(name) if EXCLUDE_DIRS.isdisjoint(p.parts))
    return sorted(out)


def scan(root: Path, denylist: set[str]) -> tuple[list[str], int, int]:
    """Return (violation lines, number of locks, number of distinct packages)."""
    violations: list[str] = []
    packages: set[str] = set()
    locks = find_locks(root)
    for lock in locks:
        pkgs = LOCK_PARSERS[lock.name](lock)
        packages |= pkgs
        for hit in sorted(pkgs & denylist):
            violations.append(f"{lock.relative_to(root)}: {hit}")
    return violations, len(locks), len(packages)


def _selftest_lockfile(name: str, body: str, denylisted: str, clean: str) -> None:
    """Plant one denylisted and one clean package in a temporary lockfile (never a file committed into
    the scanned tree) and assert the parser and the whole-tree scan catch the denylisted one and clear
    the other, so detection is actively re-proved for `name` on every CI run."""
    deny = load_denylist(DENYLIST)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / name).write_text(body, encoding="utf-8")
        parsed = LOCK_PARSERS[name](root / name)
        assert normalize(denylisted) in parsed, (name, parsed)
        assert normalize(clean) in parsed, (name, parsed)
        assert parsed & deny == {normalize(denylisted)}, (name, parsed & deny)
        violations, n_locks, _ = scan(root, deny)
        assert n_locks == 1, (name, n_locks)
        assert violations == [f"{name}: {normalize(denylisted)}"], (name, violations)


def self_test() -> int:
    deny = {normalize(n) for n in ("torch", "openai", "scikit-learn")}
    # A denylisted package is caught.
    caught = {p for p in map(normalize, ["torch", "rdflib", "jsonschema"]) if p in deny}
    assert caught == {"torch"}, caught
    # A clean set, including names that merely contain "ml", is cleared.
    clean = {p for p in map(normalize, ["linkml", "pyyaml", "html5lib", "referencing"]) if p in deny}
    assert clean == set(), clean
    # Normalisation makes scikit_learn and scikit.learn match scikit-learn.
    assert normalize("scikit_learn") in deny and normalize("scikit.learn") in deny
    # Every ecosystem's lockfile parser catches a denylisted package and clears a clean one, including a
    # scoped/ml-flavoured clean name that a substring match would wrongly flag.
    _selftest_lockfile(
        "pnpm-lock.yaml",
        "lockfileVersion: '9.0'\n\npackages:\n\n"
        "  torch@2.4.1:\n    resolution: {integrity: sha512-x==}\n\n"
        "  '@acme/linkml-tools@1.0.0':\n    resolution: {integrity: sha512-x==}\n\n"
        "snapshots: {}\n",
        denylisted="torch",
        clean="@acme/linkml-tools",
    )
    _selftest_lockfile(
        "gradle.lockfile",
        "# Gradle dependency lock\n"
        "com.example.badvendor:tensorflow:9.9.9=compileClasspath,runtimeClasspath\n"
        "org.slf4j:slf4j-api:2.0.16=compileClasspath\n"
        "empty=annotationProcessor\n",
        denylisted="tensorflow",
        clean="slf4j-api",
    )
    print("NO-ML SELF-TEST PASSED (uv.lock, pnpm-lock.yaml, gradle.lockfile detection proven)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    denylist = load_denylist(DENYLIST)
    violations, n_locks, n_pkgs = scan(ROOT, denylist)
    if violations:
        print("NO-ML FAILED: learned-component dependency found:")
        for v in violations:
            print(f"  {v}")
        return 1
    print(f"NO-ML OK ({n_locks} lockfiles, {n_pkgs} packages, 0 denylisted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
