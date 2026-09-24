#!/usr/bin/env python3
"""nist_catalog_substantiation_check - the two assertions `agentce catalog lint` does not make.

``lint_catalog`` (engines/python/agentce/catalog.py) validates a control's schema, its shape, and its
test-case outcomes, and (with ``--require-verification-flags``) that every crosswalk entry merely
*carries* a ``verified_against_text`` key. It never checks the flag's *value*, and it never checks which
subcategory of a crosswalked framework a control maps to. Those two gaps matter specifically for a base
catalog built from an existing crosswalk file (SPEC §7.3, §14.5 CP-3, finding on catalog neutrality):

1. Every crosswalk entry in the catalog's own ``controls/*.yaml`` files must carry
   ``verified_against_text: false`` -- literally, never ``true``. Verifying a mapping against the
   licensed standard text is a human action (SPEC §7.3, "verified_against_text"); an implementer
   setting it true themselves would let an unverified claim render as verified.
2. Every control id under the catalog's ``controls/`` directory must be one of the ids a companion
   crosswalk file already substantiates with a non-empty ``controls:`` list on at least one obligation.
   A catalog control with no substantiating obligation is an unfounded framework mapping.

    nist_catalog_substantiation_check.py <catalog_dir> <crosswalk_file>   check a catalog against a
                                                                           crosswalk file
    nist_catalog_substantiation_check.py --self-test                     prove it discriminates: a
                                                                           crosswalk entry claiming
                                                                           verified_against_text: true
                                                                           is rejected, a control id
                                                                           outside the substantiated set
                                                                           is rejected, and the real
                                                                           catalog passes

Uses only the standard library plus PyYAML. No network, no learned component.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def substantiated_control_ids(crosswalk_path: Path) -> set[str]:
    """Every control id that at least one obligation in ``crosswalk_path`` substantiates (a non-empty
    ``controls:`` list), read from the file rather than hard-coded, so the checker stays correct if the
    crosswalk is amended."""
    data = yaml.safe_load(crosswalk_path.read_text(encoding="utf-8")) or {}
    ids: set[str] = set()
    for obligation in data.get("obligations", []):
        for control_id in obligation.get("controls", []) or []:
            ids.add(str(control_id))
    return ids


def check(catalog_dir: Path, crosswalk_path: Path) -> list[str]:
    """Return a list of problems; an empty list means the catalog's crosswalk claims are substantiated."""
    problems: list[str] = []
    if not crosswalk_path.is_file():
        return [f"{crosswalk_path}: crosswalk file not found"]
    substantiated = substantiated_control_ids(crosswalk_path)
    controls_dir = catalog_dir / "controls"
    if not controls_dir.is_dir():
        return [f"{controls_dir}: no controls directory"]
    for control_file in sorted(controls_dir.glob("*.yaml")):
        data: dict[str, Any] = (
            yaml.safe_load(control_file.read_text(encoding="utf-8")) or {}
        )
        control_id = str(data.get("id", control_file.stem))
        for entry in data.get("crosswalk", []) or []:
            if entry.get("verified_against_text") is not False:
                problems.append(
                    f"{control_file.name}: crosswalk entry {entry.get('framework')}/"
                    f"{entry.get('clause')} has verified_against_text="
                    f"{entry.get('verified_against_text')!r}, not literally false "
                    "(verifying against the licensed standard text is a human action)"
                )
        if control_id not in substantiated:
            problems.append(
                f"{control_file.name}: control id {control_id!r} is not in {crosswalk_path.name}'s "
                "substantiated set (no obligation lists it in a non-empty controls:)"
            )
    return problems


_GOOD_CATALOG = ROOT / "spec" / "catalogs" / "base" / "nist-ai-rmf"
_GOOD_CROSSWALK = (
    ROOT / "spec" / "catalogs" / "base" / "eu-ai-act" / "crosswalk" / "nist-ai-rmf.yaml"
)


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def self_test() -> int:
    ok = True

    def report(name: str, got: list[str], want_fail: bool) -> None:
        nonlocal ok
        failed = bool(got)
        good = failed == want_fail
        print(f"self-test {name}: {'ok' if good else 'FAIL'}")
        if not good:
            ok = False
            print(f"  problems: {got}", file=sys.stderr)

    # Good fixture: the real, committed catalog against the real, committed crosswalk must pass clean.
    report("good-catalog", check(_GOOD_CATALOG, _GOOD_CROSSWALK), want_fail=False)

    with tempfile.TemporaryDirectory(
        prefix="agentce-nist-substantiation-selftest-"
    ) as tmp:
        tmp_path = Path(tmp)

        # Bad fixture 1: a crosswalk entry the implementer marked verified_against_text: true.
        bad_true = tmp_path / "bad-verified-true"
        shutil.copytree(_GOOD_CATALOG, bad_true)
        rec04 = bad_true / "controls" / "REC-04.yaml"
        data = yaml.safe_load(rec04.read_text(encoding="utf-8"))
        data["crosswalk"][0]["verified_against_text"] = True
        _write_yaml(rec04, data)
        report("bad-verified-true", check(bad_true, _GOOD_CROSSWALK), want_fail=True)

        # Bad fixture 2: a control id outside the crosswalk's substantiated set (a GOVERN/MAP obligation
        # the crosswalk marks organisational, controls: []).
        bad_unsubstantiated = tmp_path / "bad-unsubstantiated"
        shutil.copytree(_GOOD_CATALOG, bad_unsubstantiated)
        rec04_2 = bad_unsubstantiated / "controls" / "REC-04.yaml"
        data2 = yaml.safe_load(rec04_2.read_text(encoding="utf-8"))
        data2["id"] = "GOV-99"
        _write_yaml(rec04_2, data2)
        report(
            "bad-unsubstantiated-id",
            check(bad_unsubstantiated, _GOOD_CROSSWALK),
            want_fail=True,
        )

    print("SELF-TEST PASSED" if ok else "SELF-TEST FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2:
        problems = check(Path(argv[0]).resolve(), Path(argv[1]).resolve())
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print(f"{len(problems)} problem(s)", file=sys.stderr)
            return 1
        print("SUBSTANTIATION OK")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
