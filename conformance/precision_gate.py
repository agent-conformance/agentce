"""The precision/recall release gate (SPEC §11.6).

Scores the engine against the corpus ground truth and passes only when every seeded fault is detected
(recall 1.0) and no known-pass control is flagged (false-positive rate 0.0 for the rung-2 base
catalog). Prints ``GATE PASS`` and exits 0 when the gate holds, ``GATE FAIL`` and exits 1 otherwise —
the failure a rule regression must surface (DC-11). Run it as::

    cd conformance && uv run python precision_gate.py --corpus ../corpus

The metric itself lives in :mod:`corpus.precision_recall`; this script is the gate around it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The scoring logic lives in the corpus package; make it importable from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corpus.precision_recall import compute_precision_recall  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="precision_gate",
        description="Precision/recall release gate over the simulated corpus (SPEC §11.6).",
    )
    parser.add_argument(
        "--corpus",
        required=True,
        help="the corpus directory (generated or source tree)",
    )
    parser.add_argument(
        "--family",
        default=None,
        help="restrict the gate to one control family (e.g. REC); default scores every control",
    )
    args = parser.parse_args(argv)

    metrics = compute_precision_recall(Path(args.corpus), family=args.family)
    print(
        f"recall={metrics['recall']} fpr_rung2={metrics['fpr_rung2']} "
        f"seeded_faults={metrics['seeded_faults']} detected={metrics['detected']} "
        f"known_pass_controls={metrics['known_pass_controls']} false_positives={metrics['false_positives']}"
    )
    if metrics["recall"] == 1.0 and metrics["fpr_rung2"] == 0.0:
        print("GATE PASS")
        return 0
    print("GATE FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
