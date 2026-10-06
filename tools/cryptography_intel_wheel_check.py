"""cryptography_intel_wheel_check -- prove every tracked ``uv.lock`` resolving ``cryptography`` still
resolves a wheel CPython on an Intel Mac can install (items 18.58 and 18.62, ADR-0024).

ADR-0024 split ``engines/python/pyproject.toml`` and ``adapters/supply-chain/pyproject.toml``'s
``cryptography`` requirement by platform marker so Intel macOS resolves the last release with a real
prebuilt wheel (48.x) while every other platform keeps the advisory-clean 50.x line. Every other tracked
``uv.lock`` in the workspace depends on ``agent-conformance`` (editable from ``engines/python``), so its
own re-lock inherits that split -- but only once someone actually runs ``uv lock`` there. 18.55's verifier
found 20 such locks still pinned to plain ``cryptography==50.0.1`` with no Intel wheel (O1): AGENTS.md's
own documented docs and conformance commands would still fail a fresh Intel-macOS install. This script is
the standing check a later dependency bump could otherwise silently regress: it parses every tracked lock
that names ``cryptography`` and checks that every ``cryptography`` entry whose ``resolution-markers``
admit CPython on Intel macOS carries a wheel that interpreter installs.

Usage: ``uv run --project tools python tools/cryptography_intel_wheel_check.py``. Exit 0 when every
tracked lock naming ``cryptography`` passes: at least one entry admits Intel macOS, and each such entry
has a ``macosx_*_universal2`` or ``macosx_*_x86_64`` wheel tagged ``cp*`` or ``abi3`` (a ``pp*`` wheel is
PyPy-only). Exit 1 and print each offending lock otherwise, including a lock whose markers or TOML this
script cannot read. ``--self-test`` runs the matcher over seven small locks, two of them the seeded faults
18.62 added: a PyPy-only Intel wheel, and a universal2 wheel on an entry whose markers exclude Intel macOS.
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import operator
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: A cryptography wheel's platform tag that macOS/x86_64 can actually install: the universal2 fat
#: binary, or a bare Intel build. Same two architectures as the item's acceptance check and ADR-0024's
#: verification bullet; the shape mirrors (but is not the same regex as) ``environment.py``'s
#: ``PREBUILT_MACOS_TAG``, which matches a bare tag string (``arm64`` included, no ``x86_64``) read from
#: an installed distribution's own ``WHEEL`` file, not a wheel filename's trailing segment read from a
#: lock. A future wheel-tagging change upstream needs both updated together.
INTEL_TAG = re.compile(r"^macosx_\d+_\d+_(universal2|x86_64)$")

#: The marker environment of CPython on an Intel Mac, minus the interpreter version, which
#: ``_admits_intel_macos`` varies. A ``resolution-markers`` entry naming any other variable is refused
#: rather than guessed at.
INTEL_MACOS = {
    "sys_platform": "darwin",
    "platform_machine": "x86_64",
    "platform_system": "Darwin",
    "os_name": "posix",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
}

#: Interpreter versions tried against every marker, on top of the versions the marker itself names
#: (``_candidate_versions``): an entry admits Intel macOS when any of them satisfies one of its markers.
PYTHON_VERSIONS = [(3, minor, patch) for minor in range(8, 21) for patch in range(31)]

_VERSION_VARIABLES = {"python_version", "python_full_version"}

_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def tracked_uv_locks() -> list[Path]:
    """Every tracked ``uv.lock``, found the way the rest of ``tools/`` lists tracked files (list all,
    filter in Python), not by handing git a pathspec glob."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [ROOT / p for p in out.stdout.split("\0") if p.endswith("uv.lock")]


def _cpython_intel_wheel(wheel_url: str) -> bool:
    """True for a wheel CPython on an Intel Mac installs: an Intel-macOS platform tag and a ``cp*``
    interpreter tag or ``abi3`` ABI tag, as in ``.../cryptography-48.0.1-cp311-abi3-
    macosx_10_9_universal2.whl``. A ``pp*`` wheel is PyPy-only."""
    filename = wheel_url.rsplit("/", 1)[-1].removesuffix(".whl")
    # The three tags are always the last three fields; an optional build tag sits before them.
    python, abi, platform = (["", "", ""] + filename.split("-"))[-3:]
    return bool(INTEL_TAG.match(platform)) and (
        python.startswith("cp") or abi == "abi3"
    )


def _version(text: str) -> tuple[int, ...]:
    parts = tuple(int(p) for p in text.split("."))
    return parts + (0,) * (3 - len(parts))


def _marker_value(node: ast.expr, env: dict[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    raise ValueError(f"unsupported marker term {ast.unparse(node)!r}")


def _marker_true(node: ast.expr, env: dict[str, str]) -> bool:
    """Evaluate a PEP 508 marker parsed as a Python expression (the two grammars agree on ``and``,
    ``or``, parentheses and single-operator comparisons). Versions compare numerically, and
    ``== '3.13.*'`` matches by prefix. Anything else raises ``ValueError``, so an unfamiliar marker
    fails the check instead of being guessed at."""
    if isinstance(node, ast.BoolOp):
        results = [_marker_true(value, env) for value in node.values]
        return all(results) if isinstance(node.op, ast.And) else any(results)
    if not (isinstance(node, ast.Compare) and len(node.ops) == 1):
        raise ValueError(f"unsupported marker {ast.unparse(node)!r}")
    op, right_node = type(node.ops[0]), node.comparators[0]
    left, right = _marker_value(node.left, env), _marker_value(right_node, env)
    names = {n.id for n in (node.left, right_node) if isinstance(n, ast.Name)}
    if op in (ast.In, ast.NotIn):
        return (left in right) == (op is ast.In)
    if op not in _COMPARE or (
        not names & _VERSION_VARIABLES and op not in (ast.Eq, ast.NotEq)
    ):
        raise ValueError(f"unsupported marker operator in {ast.unparse(node)!r}")
    if names & _VERSION_VARIABLES and right.endswith(".*"):
        if op not in (ast.Eq, ast.NotEq):
            raise ValueError(f"unsupported marker operator in {ast.unparse(node)!r}")
        prefix = _version(right.removesuffix(".*"))[: right.count(".")]
        return bool(_COMPARE[op](_version(left)[: len(prefix)], prefix))
    if names & _VERSION_VARIABLES:
        return bool(_COMPARE[op](_version(left), _version(right)))
    return bool(_COMPARE[op](left, right))


def _candidate_versions(marker: str) -> list[tuple[int, ...]]:
    """``PYTHON_VERSIONS`` plus each version the marker names and the patch release after it, so a
    bound outside the fixed grid (``>= '3.21'``) is still reached."""
    named = [_version(v) for v in re.findall(r"'(\d+(?:\.\d+)*)(?:\.\*)?'", marker)]
    return PYTHON_VERSIONS + named + [v[:2] + (v[2] + 1,) for v in named]


@functools.cache
def _marker_admits_intel_macos(marker: str) -> bool:
    tree = ast.parse(marker, mode="eval").body
    return any(
        _marker_true(
            tree,
            {
                **INTEL_MACOS,
                "python_version": f"{v[0]}.{v[1]}",
                "python_full_version": ".".join(map(str, v)),
            },
        )
        for v in _candidate_versions(marker)
    )


def _admits_intel_macos(markers: list[str]) -> bool:
    """True when a lock entry with these ``resolution-markers`` can be what CPython on an Intel Mac
    installs, for some interpreter version; an entry with no markers applies everywhere."""
    if not markers:
        return True
    return any(_marker_admits_intel_macos(marker) for marker in markers)


def lacks_intel_wheel(lock_text: str) -> bool:
    """True when the lock names ``cryptography`` but CPython on an Intel Mac would build it from
    source: no ``cryptography`` entry whose ``resolution-markers`` admit Intel macOS, or such an entry
    with no ``cp*``/``abi3`` Intel-macOS wheel. Parses the lock as TOML (as ``tools/no_ml_check.py``'s
    own ``packages_in_uv_lock`` does) rather than pattern-matching the raw text, so a reformatted or
    reordered lock is read the same way ``uv`` reads it."""
    data: dict[str, Any] = tomllib.loads(lock_text)
    packages = [
        pkg for pkg in data.get("package", []) if pkg.get("name") == "cryptography"
    ]
    if not packages:
        return False
    intel = [
        pkg
        for pkg in packages
        if _admits_intel_macos(pkg.get("resolution-markers", []))
    ]
    return not intel or not all(
        any(
            _cpython_intel_wheel(wheel.get("url", ""))
            for wheel in pkg.get("wheels", [])
        )
        for pkg in intel
    )


def check() -> list[str]:
    problems = []
    for lock in tracked_uv_locks():
        name = str(lock.relative_to(ROOT))
        try:
            lacking = lacks_intel_wheel(lock.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            problems.append(f"cannot read {name} as TOML: {exc}")
            continue
        except (SyntaxError, ValueError) as exc:
            problems.append(
                f"cannot read the cryptography resolution-markers in {name}: {exc}"
            )
            continue
        if lacking:
            problems.append(f"no Intel macOS cryptography wheel for CPython in {name}")
    return problems


def _lock(version: str, wheels: list[str], markers: list[str] | None = None) -> str:
    """A one-entry lock resolving ``cryptography`` to these wheel filenames."""
    lines = ["[[package]]", 'name = "cryptography"', f'version = "{version}"']
    if markers is not None:
        lines.append(
            "resolution-markers = [" + ", ".join(f'"{m}"' for m in markers) + "]"
        )
    urls = ", ".join(
        f'{{ url = "https://files.pythonhosted.org/packages/aa/{w}" }}' for w in wheels
    )
    return "\n".join(lines + [f"wheels = [{urls}]", ""])


INTEL_ONLY = "platform_machine == 'x86_64' and sys_platform == 'darwin'"
NOT_INTEL = "platform_machine != 'x86_64' or sys_platform != 'darwin'"

#: (case, lock text, outcome): ``lacks`` when the check must report the lock, ``has`` or ``out of
#: scope`` when it must not. The last two are the
#: seeded faults 18.62 added: a PyPy-only Intel wheel, and a universal2 wheel on an entry whose
#: markers keep it off Intel macOS. Both would build from source for CPython on an Intel Mac.
SELF_TEST_CASES = [
    (
        "universal2 cp311-abi3 wheel, no markers",
        _lock("48.0.1", ["cryptography-48.0.1-cp311-abi3-macosx_10_9_universal2.whl"]),
        "has",
    ),
    (
        "universal2 wheel on the Intel macOS entry of a forked lock",
        _lock(
            "48.0.1",
            ["cryptography-48.0.1-cp311-abi3-macosx_10_9_universal2.whl"],
            [INTEL_ONLY],
        )
        + _lock(
            "50.0.1",
            ["cryptography-50.0.1-cp311-abi3-macosx_11_0_arm64.whl"],
            [NOT_INTEL],
        ),
        "has",
    ),
    (
        "only Linux and arm64 wheels",
        _lock(
            "50.0.1",
            [
                "cryptography-50.0.1-cp311-abi3-manylinux_2_28_x86_64.whl",
                "cryptography-50.0.1-cp311-abi3-macosx_11_0_arm64.whl",
            ],
        ),
        "lacks",
    ),
    (
        "no cryptography entry",
        '[[package]]\nname = "jsonschema"\nversion = "4.21.0"\n',
        "out of scope",
    ),
    (
        "a second Intel macOS entry with only a PyPy wheel",
        _lock(
            "48.0.1",
            ["cryptography-48.0.1-cp311-abi3-macosx_10_9_universal2.whl"],
            [f"python_full_version < '3.13' and {INTEL_ONLY}"],
        )
        + _lock(
            "48.0.2",
            ["cryptography-48.0.2-pp311-pypy311_pp73-macosx_10_9_x86_64.whl"],
            [f"python_full_version >= '3.13' and {INTEL_ONLY}"],
        ),
        "lacks",
    ),
    (
        "PyPy-only Intel wheel",
        _lock(
            "45.0.7", ["cryptography-45.0.7-pp311-pypy311_pp73-macosx_10_9_x86_64.whl"]
        ),
        "lacks",
    ),
    (
        "universal2 wheel on an entry whose markers exclude Intel macOS",
        _lock(
            "50.0.1",
            ["cryptography-50.0.1-cp311-abi3-macosx_10_9_universal2.whl"],
            [NOT_INTEL],
        ),
        "lacks",
    ),
]


def self_test() -> int:
    for case, lock_text, outcome in SELF_TEST_CASES:
        assert lacks_intel_wheel(lock_text) is (outcome == "lacks"), (
            f"{case}: expected {outcome}"
        )
        detail = "" if outcome == "out of scope" else " a CPython Intel-macOS wheel"
        print(f"ok: {case}: {outcome}{detail}")
    print("ok: cryptography_intel_wheel_check's matcher discriminates")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the matcher discriminates an Intel wheel from one with no Intel wheel",
    )
    parser.add_argument(
        "--json", action="store_true", help="also print the machine-readable result"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    problems = check()
    if args.json:
        print(json.dumps({"problems": problems}, indent=2))
    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1
    print(
        "ok: every tracked uv.lock naming cryptography resolves a CPython Intel-macOS wheel"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
