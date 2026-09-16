"""Phase-4 P4.7 orchestrator: report-rendering checks (SPEC §9.3).

A thin front over :mod:`render_check`. ``python render.py --json`` prints
``{"render_check", "escaping_text", "second_language_identical"}`` — the shape the phase-4 eval reads.
"""

from __future__ import annotations

import argparse
import json
import sys

import render_check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase-4 P4.7 report-rendering gate.")
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.parse_args(argv)
    result = render_check.evaluate()
    print(json.dumps(result, sort_keys=True))
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
