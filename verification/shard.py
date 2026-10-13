#!/usr/bin/env python3
"""Run one shard of the gates' seeded-fault demos, or of the quick tier itself.

    verification/shard.py [--dry-run] <shard_index> <shard_total>
    verification/shard.py --quick [--dry-run] <shard_index> <shard_total>
    verification/shard.py --balance-check <shard_total>

With ``--quick``, the shard takes the registry's quick-tier ids (``tier == "quick"``, registry order,
the gates ``./verification/run --quick`` runs) instead of ``--list``, and runs
``./verification/run --gate`` for each, so the shards together judge every quick gate exactly as one
``--quick`` run does. The two options combine in either order; each may appear once.

Without ``--quick``, deals the ids from ``./verification/run --list`` to the shards by the demo
durations recorded in ``verification/demo-durations.json``: longest first, each to the shard with the
least load so far (``balanced_partition()``), and runs ``./verification/run --demo-fault`` for each
gate in this shard alone. The quick tier is dealt round-robin (``partition()``).
``--balance-check`` prints each demo shard's estimated minutes at ``shard_total`` and exits 1 when a
gate has no recorded duration and the file sets no ``default_seconds``, or when the heaviest shard is
over the larger of 1.25 times the mean and the longest single gate. ``--dry-run`` prints
the partition (one gate per line) without running any demo, for VG-DEMO-SHARD-COVERAGE to check that
every shard's slice, unioned, covers the real gate list exactly once each -- cheaply, without paying
for the real RED/GREEN cycle. Standard library only; no network beyond what an individual gate's own
demo needs. Exit 0 only if every gate assigned to this shard turned RED on its seeded fault and GREEN
after restore (or, under ``--dry-run``, exit 0 unconditionally once the partition is printed).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def partition(gates: list[str], index: int, total: int) -> list[str]:
    """The slice of ``gates`` assigned to shard ``index`` of ``total``. Every element of ``gates``
    belongs to exactly one shard's partition across ``index in range(total)``, for any ``total > 0``
    -- this is checked directly (not merely argued) by VG-DEMO-SHARD-COVERAGE against the real
    ``./verification/run --list``, with a seeded fault in this function's own line."""
    return [gate for position, gate in enumerate(gates) if position % total == index]


def _seconds(gate: str, durations: dict[str, Any]) -> float:
    """A gate's recorded demo seconds, else the file's ``default_seconds``, else 0 (which
    ``balance_problems()`` reports)."""
    return float(durations["gates"].get(gate, durations.get("default_seconds", 0)))


def balanced_shards(
    gates: list[str], total: int, durations: dict[str, Any]
) -> list[list[str]]:
    """Every shard's slice of ``gates``: longest recorded demo first (ties in ``gates`` order), each
    to the shard with the least load so far (ties to the lowest index). Each gate is placed once,
    so every gate lands in exactly one shard; no clock or randomness, so every job computes the
    same split."""
    shards: list[list[str]] = [[] for _ in range(total)]
    loads = [0.0] * total
    # sorted() is stable, so equal durations keep their ``gates`` order
    for gate in sorted(gates, key=lambda gate: -_seconds(gate, durations)):
        lightest = loads.index(min(loads))
        shards[lightest].append(gate)
        loads[lightest] += _seconds(gate, durations)
    return shards


def balanced_partition(
    gates: list[str], index: int, total: int, durations: dict[str, Any]
) -> list[str]:
    """The demo slice of shard ``index`` of ``total`` (see ``balanced_shards()``)."""
    return balanced_shards(gates, total, durations)[index]


def shard_minutes(
    gates: list[str], total: int, durations: dict[str, Any]
) -> list[float]:
    """Each demo shard's estimated minutes at ``total`` shards."""
    return [
        sum(_seconds(gate, durations) for gate in mine) / 60
        for mine in balanced_shards(gates, total, durations)
    ]


def balance_problems(
    gates: list[str], durations: dict[str, Any], total: int
) -> list[str]:
    """Why the demo shards at ``total`` are not balanced: a gate with no recorded seconds when the
    file sets no ``default_seconds``, or a heaviest shard over the larger of 1.25 times the mean and
    the longest single gate. Empty when balanced."""
    problems = []
    if "default_seconds" not in durations:
        untimed = [gate for gate in gates if gate not in durations["gates"]]
        if untimed:
            problems.append(
                f"no recorded duration and no default_seconds for: {untimed}"
            )
    minutes = shard_minutes(gates, total, durations)
    longest = max(_seconds(gate, durations) for gate in gates) / 60
    bound = max(1.25 * sum(minutes) / total, longest)
    if max(minutes) > bound:
        problems.append(
            f"heaviest shard {max(minutes):.1f} min is over the bound {bound:.1f} min"
        )
    return problems


def load_durations() -> dict[str, Any]:
    return json.loads((HERE / "demo-durations.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def quick_ids(registry: dict[str, Any]) -> list[str]:
    """The quick-tier gate ids, in registry order: the gates ``./verification/run --quick`` runs."""
    return [gate["id"] for gate in registry["gates"] if gate["tier"] == "quick"]


def run_slice(
    gates: list[str], run: Callable[[str], int], label: str, done: str
) -> int:
    """Run each gate with ``run``; exit 1 if any run exited non-zero, else 0."""
    failed = [gate for gate in gates if run(gate) != 0]
    if failed:
        print(f"{label}: FAILED: {failed}", file=sys.stderr)
        return 1
    print(f"{label}: all {len(gates)} gate(s) {done}")
    return 0


def _run_script(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(HERE / "run"), *args],
        capture_output=capture,
        text=True,
        check=capture,
    )


USAGE = (
    "usage: verification/shard.py [--quick] [--dry-run] <shard_index> <shard_total>\n"
    "       verification/shard.py --balance-check <shard_total>"
)


def balance_check(gates: list[str], total: int) -> int:
    durations = load_durations()
    for index, minutes in enumerate(shard_minutes(gates, total, durations)):
        print(f"shard {index}: {minutes:.1f} min")
    problems = balance_problems(gates, durations, total)
    for problem in problems:
        print(f"balance-check: {problem}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    flags = [arg for arg in argv if arg.startswith("-")]
    argv = [arg for arg in argv if not arg.startswith("-")]
    balance = "--balance-check" in flags
    dry_run, quick = "--dry-run" in flags, "--quick" in flags
    if balance:
        usable = flags == ["--balance-check"] and len(argv) == 1
    else:
        usable = len(argv) == 2 and len(flags) == dry_run + quick
    if not usable:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        numbers = [int(arg) for arg in argv]
    except ValueError:
        print(f"shard index and total must be integers, got {argv!r}", file=sys.stderr)
        return 2
    shard, total = (0, numbers[0]) if balance else (numbers[0], numbers[1])
    if total <= 0 or not 0 <= shard < total:
        print(
            f"shard index {shard} is out of range for {total} shard(s)", file=sys.stderr
        )
        return 2
    if quick:
        gates = quick_ids(json.loads((HERE / "gates.json").read_text(encoding="utf-8")))
    else:
        gates = _run_script("--list", capture=True).stdout.splitlines()
    if not gates:
        print("no gates to shard", file=sys.stderr)
        return 1
    if balance:
        return balance_check(gates, total)
    mine = (
        partition(gates, shard, total)
        if quick
        else balanced_partition(gates, shard, total, load_durations())
    )
    if dry_run:
        print("\n".join(mine))
        return 0
    print(f"shard {shard}/{total}: {len(mine)} gate(s): {mine}", file=sys.stderr)
    mode = "--gate" if quick else "--demo-fault"
    return run_slice(
        mine,
        lambda gate: _run_script(mode, gate).returncode,
        f"shard {shard}/{total}",
        "passed or skipped" if quick else "demoed clean",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
