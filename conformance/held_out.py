"""The held-out and adversarial detection gate (SPEC §11.6, P3.5).

Scores the engine against the full corpus's held-out and adversarial subsets and reports the held-out
recall and whether every adversarial outcome equals the authored ground truth. ``held_out.py --json``
prints ``{"heldout_recall": …, "adversarial_match": …, …}`` — the interface the phase-3 eval's P3.5
check reads (recall ≥ 0.95 and adversarial_match true). Run it as::

    cd conformance && uv run python held_out.py --json

The metric itself lives in :mod:`corpus.held_out`; this script is the gate around it. The corpus source
tree is materialised as the full set on demand, so the corpus output need never be committed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The scoring logic lives in the corpus package; make it importable from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corpus.held_out import compute_held_out  # noqa: E402

DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / "corpus"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="held_out",
        description="Held-out recall and adversarial detection gate over the full corpus (P3.5).",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="the corpus directory (full set or source tree)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    metrics = compute_held_out(args.corpus)
    if args.json:
        print(json.dumps(metrics, sort_keys=True))
    else:
        print(
            f"heldout_recall={metrics['heldout_recall']} "
            f"adversarial_match={metrics['adversarial_match']} "
            f"(held_out={metrics['heldout_projects']}, adversarial={metrics['adversarial_projects']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
