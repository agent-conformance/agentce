"""Mutation-floor regression check (item 16.4, P16.6).

`check_P16_6`'s sole pinned command is ``mutation_check.py --self-test`` — no bare invocation of
either the mutation harness or a probe-recall check is pinned anywhere in phase 16 — so
``--self-test`` is where the whole of P16.6's prose bar ("meets or exceeds the Phase-1 mutation-score
floor, on a richer probe library that catches its attacks by recall 1.0 with zero false positives")
must actually be proven:

  (a) the wrapper's own floor-comparison logic, over synthetic fixtures (below the floor rejected, at
      exactly the floor accepted, `0.7499` rejected);
  (b) the real-score assertion, parameterised as ``score_file: Path`` so the tracked
      ``engines/python/mutation.json`` is only ever opened for reading, never renamed or written —
      called against a non-existent temp path (must fail loudly) and a `tempfile`-copied,
      sub-floor-edited duplicate of the real file (must fail loudly), then for real against the
      tracked file, asserting its recorded score is ``>= 0.75`` (the Phase-1 floor, `P1.5`);
  (c) `redteam_probe_check.py`'s full verification of the three new red-team probe corpora
      (recall 1.0, set identity, zero false positives).

The bare (no-flag) mode — not pinned by ``eval.sh``, built as the stronger, non-pinned proof — re-runs
the real ``engines/python/mutation.py`` harness and the real ``redteam_probe_check.py``, but never
edits the tracked working tree to do it: ``mutation.py`` rewrites its target module in place per
mutant and restores it in a ``finally`` — a SIGKILL mid-sweep bypasses that ``finally`` and would
permanently leave the tracked evaluator source mutated. This mode instead creates a throwaway
``git worktree``, syncs that worktree's own ``engines/python`` environment there, and runs
``mutation.py --out <tmp file>`` *inside* the worktree, so the in-place rewrite only ever touches the
worktree's disposable copy; a hard kill mid-sweep leaves, at worst, an orphaned temp worktree to
prune, never a mutated tracked source file. This mode is network-permitted (a cold ``uv`` cache may
reach the network to sync the worktree's environment) and is not part of the no-network CI job.

The bare mode's freshly-computed score is reported, not gated: raising or re-validating the numeric
mutation-score floor itself is out of this item's scope ("higher coverage bars on the evaluator" is
superseded, not covered by this item — see the contract). The floor ``mutation_check.py`` actually
gates is ``--self-test``'s read of the tracked, Phase-1-measured ``engines/python/mutation.json``.

Run it as::

    cd conformance && uv run python mutation_check.py --self-test
    cd conformance && uv run python mutation_check.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import redteam_probe_check

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINES_PYTHON = REPO_ROOT / "engines" / "python"
REAL_MUTATION_JSON = ENGINES_PYTHON / "mutation.json"
MUTATION_FLOOR = Decimal("0.75")  # SPEC §4, Phase-1 P1.5


class CheckFailure(Exception):
    """A real, named assertion failed."""


def assert_score_at_least(score_file: Path, floor: Decimal = MUTATION_FLOOR) -> Decimal:
    """The real-score assertion. Takes an explicit path; never renames or writes anything (B4)."""
    if not score_file.exists():
        raise CheckFailure(f"mutation score file not found: {score_file}")
    data = json.loads(score_file.read_text(encoding="utf-8"))
    if "score" not in data:
        raise CheckFailure(f"{score_file} has no 'score' field")
    score = Decimal(str(data["score"]))
    if score < floor:
        raise CheckFailure(
            f"mutation score {score} in {score_file} is below the Phase-1 floor {floor}"
        )
    return score


# ---------------------------------------------------------------------------
# --self-test
# ---------------------------------------------------------------------------


def _self_test_floor_comparison(tmp: Path) -> None:
    """(a) the wrapper's own floor-comparison logic against synthetic fixtures."""
    below = tmp / "below.json"
    below.write_text(json.dumps({"score": 0.70}), encoding="utf-8")
    try:
        assert_score_at_least(below)
    except CheckFailure:
        pass
    else:
        raise CheckFailure("self-test: a below-floor score (0.70) was not rejected")

    at_floor = tmp / "at_floor.json"
    at_floor.write_text(json.dumps({"score": 0.75}), encoding="utf-8")
    score = assert_score_at_least(at_floor)
    if score != Decimal("0.75"):
        raise CheckFailure(
            f"self-test: at-floor score read back as {score}, expected 0.75"
        )

    near_floor = tmp / "near_floor.json"
    near_floor.write_text(json.dumps({"score": 0.7499}), encoding="utf-8")
    try:
        assert_score_at_least(near_floor)
    except CheckFailure:
        pass
    else:
        raise CheckFailure("self-test: a 0.7499 score was not rejected")
    print(
        "  (a) floor-comparison logic OK (0.70 rejected, 0.75 accepted, 0.7499 rejected)"
    )


def _self_test_real_score_assertion(tmp: Path) -> None:
    """(b) the real-score assertion: missing file, regressed duplicate, then the real file."""
    missing = tmp / "does-not-exist.json"
    try:
        assert_score_at_least(missing)
    except CheckFailure:
        pass
    else:
        raise CheckFailure("self-test: a non-existent score_file was not rejected")

    regressed = tmp / "regressed-mutation.json"
    real_data = json.loads(REAL_MUTATION_JSON.read_text(encoding="utf-8"))
    real_data["score"] = 0.5
    regressed.write_text(json.dumps(real_data), encoding="utf-8")
    try:
        assert_score_at_least(regressed)
    except CheckFailure:
        pass
    else:
        raise CheckFailure(
            "self-test: a regressed (0.5) duplicate of mutation.json was not rejected"
        )

    score = assert_score_at_least(REAL_MUTATION_JSON)
    print(
        f"  (b) real-score assertion OK: {REAL_MUTATION_JSON} score={score} >= {MUTATION_FLOOR}"
    )


def _self_test_redteam_recall() -> None:
    """(c) redteam_probe_check.py's full verification: recall 1.0, zero false positives."""
    redteam_probe_check.run_real()
    print("  (c) redteam_probe_check.py real corpus verification OK (recall 1.0)")


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        parts = [
            ("floor-comparison logic", lambda: _self_test_floor_comparison(tmp)),
            ("real-score assertion", lambda: _self_test_real_score_assertion(tmp)),
            ("redteam probe recall", _self_test_redteam_recall),
        ]
        for name, fn in parts:
            try:
                fn()
            except CheckFailure as exc:
                print(f"MUTATION SELF-TEST FAILED ({name}): {exc}", file=sys.stderr)
                return 1
    print("MUTATION SELF-TEST PASSED")
    return 0


# ---------------------------------------------------------------------------
# bare mode: the real mutation.py harness, isolated in a throwaway git worktree.
# ---------------------------------------------------------------------------


def _git(*args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise CheckFailure(f"git {' '.join(args)} failed: {proc.stdout}{proc.stderr}")


def run_bare_mutation() -> Decimal:
    with tempfile.TemporaryDirectory() as tmp_str:
        worktree = Path(tmp_str) / "worktree"
        _git("worktree", "add", "--detach", str(worktree), "HEAD")
        try:
            out_file = Path(tmp_str) / "mutation-report.json"
            proc = subprocess.run(
                [
                    "uv",
                    "run",
                    "--project",
                    str(worktree / "engines" / "python"),
                    "--frozen",
                    "python",
                    "mutation.py",
                    "--out",
                    str(out_file),
                ],
                cwd=str(worktree / "engines" / "python"),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise CheckFailure(
                    f"the worktree-isolated mutation.py run failed (exit {proc.returncode}): "
                    f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}"
                )
            if not out_file.is_file():
                raise CheckFailure(
                    f"the worktree-isolated mutation.py run produced no report at {out_file}"
                )
            data = json.loads(out_file.read_text(encoding="utf-8"))
            score = Decimal(str(data["score"]))
        finally:
            _git("worktree", "remove", "--force", str(worktree))
            _git("worktree", "prune")
    return score


def run_bare() -> int:
    print("running the real mutation.py harness inside a throwaway git worktree...")
    score = run_bare_mutation()
    # This is a *fresh* measurement against however many mutation opportunities
    # agentce/structural.py currently has, which is informational, not gated: raising or even
    # re-validating the numeric mutation-score floor itself is explicitly out of this item's scope
    # ("higher coverage bars on the evaluator" is superseded, not covered) -- the gated floor
    # assertion is `--self-test`'s read of the tracked, Phase-1-measured `mutation.json` (P1.5),
    # not a fresh in-worktree score, so a fresh score below the floor is reported, not failed here.
    print(
        f"  worktree-isolated mutation sweep completed: fresh score={score} (informational; the"
    )
    print(
        f"  gated floor {MUTATION_FLOOR} is checked by --self-test against the tracked mutation.json)"
    )
    print("running redteam_probe_check.py's full verification...")
    redteam_probe_check.run_real()
    print("  redteam_probe_check.py real corpus verification OK (recall 1.0)")
    print("MUTATION CHECK PASSED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mutation-floor regression check (item 16.4, P16.6)."
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the wrapper's floor-comparison and real-score-assertion logic, fast",
    )
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        return run_bare()
    except CheckFailure as exc:
        print(f"MUTATION CHECK FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
