#!/usr/bin/env python3
"""no_ml_check - enforce the no-learned-components rule (SPEC 8.7, HR-1/HR-2).

The engine and every evaluation-path script must resolve to a dependency tree with no machine-learning
framework and no LLM or embedding client. This tool scans the repository's `uv.lock` files for any
package named in `spec/rules/no-ml-denylist.txt`.

Distribution names are compared by exact PEP 503 normalisation (lowercase; runs of `-`, `_`, `.`
collapsed to one `-`), never as a substring, so a package whose name merely contains "ml" -- linkml,
pyyaml, html5lib -- is never mistaken for a machine-learning package.

    no_ml_check.py             scan every uv.lock; exit 1 if any locked package is denylisted
    no_ml_check.py --self-test prove the matcher catches a denylisted name and clears a clean set

Uses only the standard library. No network, no learned component.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DENYLIST = ROOT / "spec" / "rules" / "no-ml-denylist.txt"


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def load_denylist(path: Path) -> set[str]:
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(normalize(line))
    return out


def packages_in_lock(lock_path: Path) -> set[str]:
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return {normalize(pkg["name"]) for pkg in data.get("package", []) if "name" in pkg}


def find_locks(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("uv.lock") if ".venv" not in p.parts)


def scan(root: Path, denylist: set[str]) -> tuple[list[str], int, int]:
    """Return (violation lines, number of locks, number of distinct packages)."""
    violations: list[str] = []
    packages: set[str] = set()
    locks = find_locks(root)
    for lock in locks:
        pkgs = packages_in_lock(lock)
        packages |= pkgs
        for hit in sorted(pkgs & denylist):
            violations.append(f"{lock.relative_to(root)}: {hit}")
    return violations, len(locks), len(packages)


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
    print("NO-ML SELF-TEST PASSED")
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
