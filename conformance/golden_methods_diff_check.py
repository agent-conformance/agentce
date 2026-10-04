#!/usr/bin/env python3
"""Confirm a golden regeneration touched only each OSCAL observation's `methods` field (18.17b).

Run with (from the repository root):

    cd conformance && uv run --frozen python golden_methods_diff_check.py <base_sha>

Compares each of ``engines/typescript/testdata/report-golden.json`` and
``engines/java/src/test/resources/testdata/report-golden.json`` at ``<base_sha>`` (via ``git show``)
against the current working tree: every JSON path other than an observation's own ``methods`` array
must be byte-identical, at least one ``methods`` array must actually have changed (otherwise the
regeneration did nothing and this check would pass vacuously), and at least one observation must now
read ``["EXAMINE"]`` (the manual-mode case this item exists to fix).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDENS = (
    "engines/typescript/testdata/report-golden.json",
    "engines/java/src/test/resources/testdata/report-golden.json",
)


def _strip_methods(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: (None if k == "methods" else _strip_methods(v)) for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_strip_methods(x) for x in obj]
    return obj


def _methods_arrays(obj: Any, acc: list[list[str]]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "methods":
                acc.append(v)
            else:
                _methods_arrays(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _methods_arrays(item, acc)


def _at_base(base: str, rel_path: str) -> Any:
    raw = subprocess.run(
        ["git", "show", f"{base}:{rel_path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(raw)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: golden_methods_diff_check.py <base_sha>", file=sys.stderr)
        return 2
    base = argv[0]

    for rel_path in GOLDENS:
        old = _at_base(base, rel_path)
        new = json.loads((REPO_ROOT / rel_path).read_text(encoding="utf-8"))

        if _strip_methods(old) != _strip_methods(new):
            print(
                f"FAIL {rel_path}: a field other than `methods` changed since {base}",
                file=sys.stderr,
            )
            return 1

        old_methods: list[list[str]] = []
        new_methods: list[list[str]] = []
        _methods_arrays(old, old_methods)
        _methods_arrays(new, new_methods)

        if len(old_methods) != len(new_methods):
            print(
                f"FAIL {rel_path}: observation count changed ({len(old_methods)} -> {len(new_methods)})",
                file=sys.stderr,
            )
            return 1
        if old_methods == new_methods:
            print(
                f"FAIL {rel_path}: `methods` arrays are identical to {base}; golden was not regenerated",
                file=sys.stderr,
            )
            return 1
        if ["EXAMINE"] not in new_methods:
            print(
                f'FAIL {rel_path}: no observation has methods == ["EXAMINE"]; manual mapping missing',
                file=sys.stderr,
            )
            return 1

    print(
        f"OK: only `methods` fields changed since {base}, in both goldens, and a manual EXAMINE is present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
