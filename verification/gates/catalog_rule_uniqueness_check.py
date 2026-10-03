"""Driver for VG-CATALOG-RULE-UNIQUE (18.37, SPEC §7.3). Not itself a test file: invoked by
catalog_rule_uniqueness.sh over every real shipped catalog. Discovers catalogs from the directory
tree (``spec/catalogs/base/*`` and ``spec/catalogs/overlays/*``) rather than from the baseline
file's own keys, so a new catalog is scanned even before anyone adds it to the baseline. Prints each
catalog's new (unbaselined) duplicate pairs, if any, and fails on a baseline entry that is no longer
a real duplicate (the baseline file is meant to shrink, never to grow by staying stale) or on a
baseline whose pair count exceeds its pinned ``ceilings`` entry (the shrink-only check: without it,
quietly adding a new duplicate to ``pairs`` without also raising the matching ceiling would pass; the
ceiling does not stop a change that deliberately edits both fields together in one diff -- that is a
reviewable, visible edit, not a silent one, and is the residual risk this mechanism accepts). Exits
non-zero on any of the three kinds of problem.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "engines" / "python"
sys.path.insert(0, str(_ENGINE_ROOT))

from agentce.catalog import load_catalog, rule_uniqueness_report  # noqa: E402


def _discover_catalogs(catalogs_root: Path) -> list[tuple[str, Path]]:
    """Every catalog directory under ``catalogs_root/{base,overlays}``: each has its own
    ``catalog.yaml``. Non-catalog entries (e.g. ``overlays/README.md``) are skipped."""
    found: list[tuple[str, Path]] = []
    for group in ("base", "overlays"):
        group_dir = catalogs_root / group
        if not group_dir.is_dir():
            continue
        for entry in sorted(group_dir.iterdir()):
            if entry.is_dir() and (entry / "catalog.yaml").is_file():
                found.append((entry.name, entry))
    return found


def main(argv: list[str]) -> int:
    catalogs_root, baseline_path = Path(argv[1]), Path(argv[2])
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline, ceilings = data["pairs"], data["ceilings"]
    status = 0
    for name, directory in _discover_catalogs(catalogs_root):
        catalog = load_catalog(directory)
        baseline_pairs = frozenset(frozenset(pair) for pair in baseline.get(name, []))
        problem_found = False
        ceiling = ceilings.get(name, 0)
        if len(baseline_pairs) > ceiling:
            status = 1
            problem_found = True
            print(
                f"catalog-rule-uniqueness: {name} has {len(baseline_pairs)} baselined pairs, above "
                f"its pinned ceiling of {ceiling} -- the baseline file is shrink-only; raising a "
                f"ceiling needs its own harness/decisions/phase-18.tsv row, not a silent bump"
            )
        stale, problems = rule_uniqueness_report(catalog, baseline_pairs)
        if stale:
            status = 1
            problem_found = True
            print(
                f"catalog-rule-uniqueness: {name} has a stale baseline entry that is no longer a "
                f"real duplicate -- fix the baseline file, don't let it grow by staying stale: "
                f"{sorted(sorted(pair) for pair in stale)}"
            )
        if problems:
            status = 1
            problem_found = True
            print(f"catalog-rule-uniqueness: {name} has new duplicate pairs beyond the committed baseline:")
            for problem in problems:
                print(f"  {problem}")
        if not problem_found:
            print(f"catalog-rule-uniqueness: {name} OK ({len(baseline_pairs)} baselined, 0 new)")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
