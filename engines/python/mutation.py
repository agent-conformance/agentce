#!/usr/bin/env python3
"""Mutation testing of the evaluator core (SPEC §4; phase-1 check P1.5).

Coverage says the tests *execute* the evaluator; mutation testing says they *pin its behaviour*. This
harness applies a fixed set of source mutations to the structural evaluator — the module that turns a
compiled shape and a graph into a conformant / non-conformant verdict — and, for each mutant, runs the
evaluator's test subset. A mutant a test kills is a behaviour the tests hold; a survivor is a change no
test would notice. The mutation score is killed / total, written to ``mutation.json`` and gated at
≥ 0.75.

Deterministic and self-contained: it uses only the standard library's ``ast`` to enumerate mutation
opportunities in a fixed order, rewrites the target in place with a guaranteed restore, and runs the
existing suite as the oracle. Run it from ``engines/python``::

    uv run python mutation.py            # score the default target
    uv run python mutation.py --limit 40 # a quick sample for iteration

No network, no learned component.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "agentce" / "structural.py"
#: The oracle: the tests that exercise the structural evaluator, run per mutant.
ORACLE = [
    "tests/test_structural.py",
    "tests/test_structural_constraints.py",
    "tests/test_structural_shacl.py",
    "tests/test_tolerance.py",
    "tests/test_assess.py",
]

_CMP_SWAP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}
_BOOL_SWAP: dict[type[ast.boolop], type[ast.boolop]] = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}


class _Mutator(ast.NodeTransformer):
    """Applies exactly one mutation, at the ``target``-th opportunity in source order.

    When ``target`` is ``None`` it mutates nothing and only counts opportunities into ``count``."""

    def __init__(self, target: int | None) -> None:
        self.target = target
        self.count = 0

    def _hit(self) -> bool:
        hit = self.count == self.target
        self.count += 1
        return hit

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        new_ops = []
        for op in node.ops:
            if type(op) in _CMP_SWAP:
                new_ops.append(_CMP_SWAP[type(op)]() if self._hit() else op)
            else:
                new_ops.append(op)
        node.ops = new_ops
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        if type(node.op) in _BOOL_SWAP and self._hit():
            node.op = _BOOL_SWAP[type(node.op)]()
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, bool):
            if self._hit():
                return ast.copy_location(ast.Constant(value=not node.value), node)
        elif isinstance(node.value, int):
            if self._hit():
                return ast.copy_location(ast.Constant(value=node.value + 1), node)
        return node


def _count(source: str) -> int:
    mutator = _Mutator(target=None)
    mutator.visit(ast.parse(source))
    return mutator.count


def _mutant(source: str, index: int) -> str:
    tree = _Mutator(target=index).visit(ast.parse(source))
    return ast.unparse(ast.fix_missing_locations(tree))


def _run_oracle() -> bool:
    """Return True if the oracle passes (a surviving mutant), False if any test fails (killed)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *ORACLE],
        cwd=HERE,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mutation test the evaluator core (SPEC §4, P1.5)."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="score only the first N opportunities (0 = all)",
    )
    parser.add_argument(
        "--out", default=str(HERE / "mutation.json"), help="where to write the report"
    )
    args = parser.parse_args(argv)

    original = TARGET.read_text(encoding="utf-8")
    # Baseline: the oracle must pass on the unmutated target, or every mutant would be falsely
    # "killed" (a missing test file or a pre-existing failure would inflate the score).
    if not _run_oracle():
        print(
            "mutation: the oracle does not pass on the unmutated target; aborting.",
            file=sys.stderr,
        )
        return 1
    total_points = _count(original)
    indices = range(total_points if not args.limit else min(args.limit, total_points))

    killed = 0
    survivors: list[int] = []
    considered = 0
    try:
        for index in indices:
            mutant_source = _mutant(original, index)
            if mutant_source == ast.unparse(ast.parse(original)):
                continue  # a no-op mutation; do not count it
            considered += 1
            TARGET.write_text(mutant_source, encoding="utf-8")
            if _run_oracle():
                survivors.append(index)
                verdict = "survived"
            else:
                killed += 1
                verdict = "killed"
            print(f"[{index + 1}/{total_points}] {verdict}", file=sys.stderr)
    finally:
        TARGET.write_text(original, encoding="utf-8")

    total = considered
    score = 1.0 if total == 0 else round(killed / total, 4)
    report = {
        "target": "agentce/structural.py",
        "tool": "engines/python/mutation.py",
        "operators": [
            "comparison-swap",
            "boolean-swap",
            "boolean-constant",
            "integer-constant",
        ],
        "total": total,
        "killed": killed,
        "survived": len(survivors),
        "survivor_indices": survivors,
        "score": score,
    }
    Path(args.out).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"mutation score {score} ({killed}/{total} killed); report at {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
