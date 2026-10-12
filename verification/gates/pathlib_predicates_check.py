#!/usr/bin/env python3
"""VG-PATHLIB-PREDICATES (18.163): no new bare pathlib predicate in the Python engine.

``Path.is_file()``, ``is_dir()``, ``exists()`` and ``is_symlink()`` answer a path under a folder that
can be listed but not entered differently by Python version: 3.12 and 3.13 raise PermissionError, 3.14
returns False. 18.114 and 18.163 each found such a site only after a verifier hit it. This check lists
every bare call of the four in ``engines/python/agentce`` by site and compares the list with a committed
baseline that only shrinks. A new site must use ``signing.is_regular_file``/``is_directory``, which
answer the same on every Python and make the caller choose between raising and "not there", or be added
to the baseline in review.

A site is ``<path under engines/python>::<qualified function>::<receiver>.<predicate>`` (the receiver
as ``ast.unparse`` prints it), counted; line numbers are left out so unrelated edits do not move it. The
check fails on a site missing from the baseline, a count above the baseline's, and a baseline count
above the code's (a paid-down site must leave the file).

Standard library only; reads source, runs nothing.

    pathlib_predicates_check.py <engine-package-dir> <baseline.json>
    pathlib_predicates_check.py --self-test
"""

from __future__ import annotations

import ast
import json
import sys
from collections import Counter
from pathlib import Path

PREDICATES = frozenset({"is_file", "is_dir", "exists", "is_symlink"})


def sites_in(source: str, module: str) -> Counter[str]:
    """Every bare predicate call in ``source`` (no positional argument; keywords such as
    ``follow_symlinks`` still count), keyed by site."""
    found: Counter[str] = Counter()
    scope: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _scoped(
            self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ) -> None:
            scope.append(node.name)
            self.generic_visit(node)
            scope.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in PREDICATES
                and not node.args
            ):
                found[
                    f"{module}::{'.'.join(scope) or '<module>'}::{ast.unparse(func)}"
                ] += 1
            self.generic_visit(node)

    Visitor().visit(ast.parse(source))
    return found


def census(package: Path) -> Counter[str]:
    """All sites in every module under ``package``, keyed relative to its parent folder."""
    total: Counter[str] = Counter()
    for path in sorted(package.rglob("*.py")):
        total += sites_in(
            path.read_text(encoding="utf-8"),
            path.relative_to(package.parent).as_posix(),
        )
    return total


def problems(found: Counter[str], baseline: dict[str, int]) -> list[str]:
    out = []
    for site in sorted(set(found) | set(baseline)):
        have, allowed = found.get(site, 0), baseline.get(site, 0)
        if have > allowed:
            out.append(
                f"new bare pathlib predicate: {site} ({have} call(s), baseline {allowed}); "
                "use signing.is_regular_file/is_directory, choosing unreadable_is_absent"
            )
        elif have < allowed:
            out.append(
                f"stale baseline entry: {site} (baseline {allowed}, code {have}); lower or remove it"
            )
    return out


def self_test() -> int:
    source = (
        "def f(p, q):\n"
        "    if p.is_file() and (q / 'x').exists():\n"
        "        return p.is_file()\n"
        "    return p.is_dir(follow_symlinks=False) or os.path.exists(p)\n"
        "class C:\n"
        "    def g(self):\n"
        "        return self.path.is_symlink()\n"
        "TOP = Path('x').is_dir()\n"
    )
    found = sites_in(source, "agentce/m.py")
    assert found == Counter(
        {
            "agentce/m.py::f::p.is_file": 2,
            "agentce/m.py::f::(q / 'x').exists": 1,
            "agentce/m.py::C.g::self.path.is_symlink": 1,
            "agentce/m.py::<module>::Path('x').is_dir": 1,
            "agentce/m.py::f::p.is_dir": 1,
        }
    ), found
    exact = dict(found)
    assert problems(found, exact) == []
    new = problems(found, {k: v for k, v in exact.items() if "is_symlink" not in k})
    assert len(new) == 1 and new[0].startswith(
        "new bare pathlib predicate: agentce/m.py::C.g"
    ), new
    raised = problems(found, {**exact, "agentce/m.py::f::p.is_file": 1})
    assert len(raised) == 1 and raised[0].startswith(
        "new bare pathlib predicate: agentce/m.py::f::p.is_file"
    ), raised
    stale = problems(found, {**exact, "agentce/gone.py::h::p.exists": 1})
    assert stale == [
        "stale baseline entry: agentce/gone.py::h::p.exists (baseline 1, code 0); lower or remove it"
    ], stale
    print(
        "pathlib-predicates self-test: ok (sites, new site, raised count, stale entry, exact match)"
    )
    return 0


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    package, baseline_path = Path(argv[0]), Path(argv[1])
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["sites"]
    found = census(package)
    issues = problems(found, baseline)
    for issue in issues:
        print(f"pathlib-predicates: {issue}", file=sys.stderr)
    print(
        f"pathlib-predicates: {sum(found.values())} bare call(s) at {len(found)} site(s); baseline {sum(baseline.values())}"
    )
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
