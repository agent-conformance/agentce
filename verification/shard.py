#!/usr/bin/env python3
"""Run one shard of the quick-tier gates' seeded-fault demos.

    verification/shard.py <shard_index> <shard_total>
    verification/shard.py --dry-run <shard_index> <shard_total>

Partitions the ids from ``./verification/run --list`` by ``index % shard_total == shard_index``
and runs ``./verification/run --demo-fault`` for each gate in this shard alone. ``--dry-run`` prints
the partition (one gate per line) without running any demo, for VG-DEMO-SHARD-COVERAGE to check that
every shard's slice, unioned, covers the real gate list exactly once each -- cheaply, without paying
for the real RED/GREEN cycle. Standard library only; no network beyond what an individual gate's own
demo needs. Exit 0 only if every gate assigned to this shard turned RED on its seeded fault and GREEN
after restore (or, under ``--dry-run``, exit 0 unconditionally once the partition is printed).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def partition(gates: list[str], index: int, total: int) -> list[str]:
    """The slice of ``gates`` assigned to shard ``index`` of ``total``. Every element of ``gates``
    belongs to exactly one shard's partition across ``index in range(total)``, for any ``total > 0``
    -- this is checked directly (not merely argued) by VG-DEMO-SHARD-COVERAGE against the real
    ``./verification/run --list``, with a seeded fault in this function's own line."""
    return [gate for position, gate in enumerate(gates) if position % total == index]


def main(argv: list[str]) -> int:
    dry_run = bool(argv) and argv[0] == "--dry-run"
    if dry_run:
        argv = argv[1:]
    if len(argv) != 2:
        print(
            "usage: verification/shard.py [--dry-run] <shard_index> <shard_total>",
            file=sys.stderr,
        )
        return 2
    try:
        shard, total = int(argv[0]), int(argv[1])
    except ValueError:
        print(f"shard index and total must be integers, got {argv!r}", file=sys.stderr)
        return 2
    if total <= 0 or not 0 <= shard < total:
        print(
            f"shard index {shard} is out of range for {total} shard(s)", file=sys.stderr
        )
        return 2
    gates = subprocess.run(
        ["python3", str(HERE / "run"), "--list"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    if not gates:
        print("`verification/run --list` printed no gates", file=sys.stderr)
        return 1
    mine = partition(gates, shard, total)
    if dry_run:
        print("\n".join(mine))
        return 0
    print(f"shard {shard}/{total}: {len(mine)} gate(s): {mine}", file=sys.stderr)
    failed = []
    for gate in mine:
        result = subprocess.run(["python3", str(HERE / "run"), "--demo-fault", gate])
        if result.returncode != 0:
            failed.append(gate)
    if failed:
        print(f"shard {shard}/{total}: FAILED: {failed}", file=sys.stderr)
        return 1
    print(f"shard {shard}/{total}: all {len(mine)} gate(s) demoed clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
