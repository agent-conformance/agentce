#!/usr/bin/env python3
"""Run one shard of the quick-tier gates' seeded-fault demos, or of the quick tier itself.

    verification/shard.py [--dry-run] <shard_index> <shard_total>
    verification/shard.py --quick [--dry-run] <shard_index> <shard_total>

With ``--quick``, the shard takes the registry's quick-tier ids (``tier == "quick"``, registry order,
the gates ``./verification/run --quick`` runs) instead of ``--list``, and runs
``./verification/run --gate`` for each, so the shards together judge every quick gate exactly as one
``--quick`` run does. The two options combine in either order; each may appear once.

Partitions the ids from ``./verification/run --list`` by ``index % shard_total == shard_index``
and runs ``./verification/run --demo-fault`` for each gate in this shard alone. ``--dry-run`` prints
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


def _run_script(*args: str) -> int:
    return subprocess.run(["python3", str(HERE / "run"), *args]).returncode


def main(argv: list[str]) -> int:
    flags = [arg for arg in argv if arg.startswith("-")]
    argv = [arg for arg in argv if not arg.startswith("-")]
    dry_run, quick = "--dry-run" in flags, "--quick" in flags
    if len(argv) != 2 or len(flags) != dry_run + quick:
        print(
            "usage: verification/shard.py [--quick] [--dry-run] <shard_index> <shard_total>",
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
    if quick:
        gates = quick_ids(json.loads((HERE / "gates.json").read_text(encoding="utf-8")))
    else:
        gates = subprocess.run(
            ["python3", str(HERE / "run"), "--list"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    if not gates:
        print("no gates to shard", file=sys.stderr)
        return 1
    mine = partition(gates, shard, total)
    if dry_run:
        print("\n".join(mine))
        return 0
    print(f"shard {shard}/{total}: {len(mine)} gate(s): {mine}", file=sys.stderr)
    mode = "--gate" if quick else "--demo-fault"
    return run_slice(
        mine,
        lambda gate: _run_script(mode, gate),
        f"shard {shard}/{total}",
        "passed or skipped" if quick else "demoed clean",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
