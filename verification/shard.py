#!/usr/bin/env python3
"""Run one shard of the gates' seeded-fault demos, or of the quick tier itself.

    verification/shard.py [--dry-run] <shard_index> <shard_total>
    verification/shard.py --quick [--dry-run] <shard_index> <shard_total>
    verification/shard.py --balance-check <shard_total>

With ``--quick``, the shard takes the registry's quick-tier ids (``tier == "quick"``, registry order,
the gates ``./verification/run --quick`` runs) instead of ``--list``, and runs
``./verification/run --gate`` for each, so the shards together judge every quick gate exactly as one
``--quick`` run does. The two options combine in either order; each may appear once.

Without ``--quick``, the work is every seeded fault of every gate in ``./verification/run --list``:
one (gate, fault) pair per fault in ``verification/gates.json``. A gate's demo seconds, recorded in
``verification/demo-durations.json``, are split evenly over its fault runs and its one restore run, so
a pair costs ``seconds / (faults + 1)``. A gate over the file's ``max_chunk_seconds`` is cut into the
fewest runs of consecutive faults that fit (``demo_chunks()``); each chunk costs its faults plus one
restore run. The chunks are dealt longest first, each to the shard with the least load so far
(``balanced_shards()``), and the shard runs ``./verification/run --demo-fault <gate> --fault=<n> ...``
once per chunk it holds. The quick tier is dealt round-robin by gate (``partition()``).
``--balance-check`` prints each demo shard's estimated minutes at ``shard_total`` and exits 1 when a
gate has no recorded duration and the file sets no ``default_seconds``, when the heaviest shard is over
the larger of 1.25 times the mean and the longest single chunk, or when a shard is over the file's
``lane_bound_minutes``. ``--dry-run`` prints the shard's pairs, one ``<gate> <fault>`` per line, without
running any demo, for VG-DEMO-SHARD-COVERAGE to check that every shard's slice, unioned, covers every
registered fault exactly once -- cheaply, without paying for the real RED/GREEN cycle. Standard
library only; no network beyond what an individual gate's own demo needs. Exit 0 only if every chunk
assigned to this shard turned RED on each of its seeded faults and GREEN after restore (or, under
``--dry-run``, exit 0 unconditionally once the partition is printed).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

HERE = Path(__file__).resolve().parent
T = TypeVar("T")
Pair = tuple[str, int]


def partition(gates: list[str], index: int, total: int) -> list[str]:
    """The slice of ``gates`` assigned to shard ``index`` of ``total``. Every element of ``gates``
    belongs to exactly one shard's partition across ``index in range(total)``, for any ``total > 0``
    -- this is checked directly (not merely argued) by VG-DEMO-SHARD-COVERAGE against the real
    ``./verification/run --list``, with a seeded fault in this function's own line."""
    return [gate for position, gate in enumerate(gates) if position % total == index]


def demo_pairs(gates: list[str], registry: dict[str, Any]) -> list[Pair]:
    """Every (gate, fault index) pair of ``gates``, in ``gates`` order then fault order."""
    counts = {gate["id"]: len(gate["faults"]) for gate in registry["gates"]}
    return [(gate, fault) for gate in gates for fault in range(counts[gate])]


def _seconds(gate: str, durations: dict[str, Any]) -> float:
    """A gate's recorded demo seconds, else the file's ``default_seconds``, else 0 (which
    ``balance_problems()`` reports)."""
    return float(durations["gates"].get(gate, durations.get("default_seconds", 0)))


def demo_chunks(pairs: list[Pair], durations: dict[str, Any]) -> list[list[Pair]]:
    """``pairs`` cut into the chunks one ``--demo-fault`` call runs: each gate's faults, in order,
    in the fewest runs of consecutive faults whose share of the gate's recorded seconds is at most
    ``max_chunk_seconds`` (sizes differ by at most one fault, the longer runs first), or one chunk
    per gate when the file sets none."""
    by_gate: dict[str, list[Pair]] = {}
    for pair in pairs:
        by_gate.setdefault(pair[0], []).append(pair)
    cap = durations.get("max_chunk_seconds")
    chunks = []
    for gate, faults in by_gate.items():
        wanted = math.ceil(_seconds(gate, durations) / cap) if cap else 1
        count = max(1, min(len(faults), wanted))
        size, extra = divmod(len(faults), count)
        start = 0
        for index in range(count):
            end = start + size + (index < extra)
            chunks.append(faults[start:end])
            start = end
    return chunks


def chunk_seconds(chunk: list[Pair], durations: dict[str, Any], faults: int) -> float:
    """A chunk's estimated seconds: its fault runs plus one restore run, each an even share of the
    gate's recorded seconds over its ``faults`` runs and one restore."""
    return _seconds(chunk[0][0], durations) * (len(chunk) + 1) / (faults + 1)


def _costed_chunks(
    pairs: list[Pair], durations: dict[str, Any]
) -> list[tuple[list[Pair], float]]:
    faults = Counter(gate for gate, _ in pairs)
    return [
        (chunk, chunk_seconds(chunk, durations, faults[chunk[0][0]]))
        for chunk in demo_chunks(pairs, durations)
    ]


def _deal(
    pairs: list[Pair], total: int, durations: dict[str, Any]
) -> tuple[list[list[list[Pair]]], list[float]]:
    """Every shard's chunks and estimated seconds: longest estimate first (ties in ``pairs``
    order), each to the shard with the least load so far (ties to the lowest index). Each chunk is
    placed once and the chunks partition ``pairs``, so every pair lands in exactly one shard; no
    clock or randomness, so every job computes the same split."""
    shards: list[list[list[Pair]]] = [[] for _ in range(total)]
    loads = [0.0] * total
    # sorted() is stable, so equal estimates keep their ``pairs`` order
    for chunk, seconds in sorted(_costed_chunks(pairs, durations), key=lambda c: -c[1]):
        lightest = loads.index(min(loads))
        shards[lightest].append(chunk)
        loads[lightest] += seconds
    return shards, loads


def balanced_shards(
    pairs: list[Pair], total: int, durations: dict[str, Any]
) -> list[list[list[Pair]]]:
    """Every shard's chunks (see ``_deal()``)."""
    return _deal(pairs, total, durations)[0]


def balanced_partition(
    pairs: list[Pair], index: int, total: int, durations: dict[str, Any]
) -> list[Pair]:
    """The demo pairs of shard ``index`` of ``total`` (see ``balanced_shards()``)."""
    return slice_pairs(balanced_shards(pairs, total, durations)[index])


def slice_pairs(chunks: list[list[Pair]]) -> list[Pair]:
    """A shard's chunks as one list of (gate, fault) pairs, in dealing order."""
    return [pair for chunk in chunks for pair in chunk]


def shard_minutes(
    pairs: list[Pair], total: int, durations: dict[str, Any]
) -> list[float]:
    """Each demo shard's estimated minutes at ``total`` shards."""
    return [seconds / 60 for seconds in _deal(pairs, total, durations)[1]]


def balance_problems(
    pairs: list[Pair], durations: dict[str, Any], total: int
) -> list[str]:
    """Why the demo shards at ``total`` are not balanced: a gate with no recorded seconds when the
    file sets no ``default_seconds``, a heaviest shard over the larger of 1.25 times the mean and
    the longest single chunk, or a shard over ``lane_bound_minutes`` when the file sets one. Empty
    when balanced."""
    problems = []
    if "default_seconds" not in durations:
        untimed = sorted({gate for gate, _ in pairs if gate not in durations["gates"]})
        if untimed:
            problems.append(
                f"no recorded duration and no default_seconds for: {untimed}"
            )
    minutes = shard_minutes(pairs, total, durations)
    longest = max(seconds for _, seconds in _costed_chunks(pairs, durations)) / 60
    bound = max(1.25 * sum(minutes) / total, longest)
    if max(minutes) > bound:
        problems.append(
            f"heaviest shard {max(minutes):.1f} min is over the bound {bound:.1f} min"
        )
    lane_bound = durations.get("lane_bound_minutes")
    if lane_bound is not None and max(minutes) > lane_bound:
        problems.append(
            f"heaviest shard {max(minutes):.1f} min is over lane_bound_minutes {lane_bound}"
        )
    return problems


def load_durations() -> dict[str, Any]:
    return json.loads((HERE / "demo-durations.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def quick_ids(registry: dict[str, Any]) -> list[str]:
    """The quick-tier gate ids, in registry order: the gates ``./verification/run --quick`` runs."""
    return [gate["id"] for gate in registry["gates"] if gate["tier"] == "quick"]


def run_slice(items: list[T], run: Callable[[T], int], label: str, done: str) -> int:
    """Run each item with ``run``; exit 1 if any run exited non-zero, else 0."""
    failed = [item for item in items if run(item) != 0]
    if failed:
        print(f"{label}: FAILED: {failed}", file=sys.stderr)
        return 1
    print(f"{label}: all {len(items)} {done}")
    return 0


def demo_args(chunk: list[Pair]) -> list[str]:
    """The ``./verification/run`` arguments that demo exactly ``chunk``'s faults of its gate."""
    return ["--demo-fault", chunk[0][0], *(f"--fault={fault}" for _, fault in chunk)]


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


def balance_check(pairs: list[Pair], total: int) -> int:
    durations = load_durations()
    for index, minutes in enumerate(shard_minutes(pairs, total, durations)):
        print(f"shard {index}: {minutes:.1f} min")
    problems = balance_problems(pairs, durations, total)
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
    if total <= 0:
        print(f"shard total must be positive, got {total}", file=sys.stderr)
        return 2
    if not 0 <= shard < total:
        print(
            f"shard index {shard} is out of range for {total} shard(s)", file=sys.stderr
        )
        return 2
    registry = json.loads((HERE / "gates.json").read_text(encoding="utf-8"))
    if quick:
        gates = quick_ids(registry)
    else:
        gates = _run_script("--list", capture=True).stdout.splitlines()
    if not gates:
        print("no gates to shard", file=sys.stderr)
        return 1
    label = f"shard {shard}/{total}"
    if quick:
        mine = partition(gates, shard, total)
        if dry_run:
            print("\n".join(mine))
            return 0
        print(f"{label}: {len(mine)} gate(s): {mine}", file=sys.stderr)
        return run_slice(
            mine,
            lambda gate: _run_script("--gate", gate).returncode,
            label,
            "gate(s) passed or skipped",
        )
    pairs = demo_pairs(gates, registry)
    if balance:
        return balance_check(pairs, total)
    # one slice for both paths, so the dry-run the gate judges is the slice CI runs
    chunks = balanced_shards(pairs, total, load_durations())[shard]
    if dry_run:
        print("\n".join(f"{gate} {fault}" for gate, fault in slice_pairs(chunks)))
        return 0
    print(
        f"{label}: {len(chunks)} chunk(s): {[demo_args(c) for c in chunks]}",
        file=sys.stderr,
    )
    return run_slice(
        chunks,
        lambda chunk: _run_script(*demo_args(chunk)).returncode,
        label,
        "chunk(s) demoed clean",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
