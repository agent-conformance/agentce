"""Perf-at-scale gate check (item 16.4, P16.7): the Phase-3 measured-or-ADR pattern, unmodified.

`check_P16_7`'s pinned bare invocation runs a fresh `perf.py --run` — with no `--out` override, so it
writes to `perf.py`'s own default, gitignored `conformance/perf-results.json` — then `perf.py --check`,
which passes on either a within-targets recorded run or `docs/adr/0009-performance.md`'s presence, and
prints which path it took. Nothing here reimplements `perf.py`'s measurement or gate logic: this is a
thin wrapper around the real, unmodified module.

The wrapper must never redirect `--run`'s `--out` to a temp file: doing so would make `--check` blind
to the fresh measurement and silently collapse every real run onto the ADR-presence fallback, defeating
the point of a fresh-measurement gate.

`--self-test` proves the wrapper's own pass/fail decision logic against synthetic fixtures (a
not-within-targets result with the ADR present still passes; the same result with the ADR fixture
removed fails) and proves, structurally, that the bare mode's own `--run` invocation carries no `--out`
override.

Run it as::

    cd conformance && uv run python perf_gate_check.py --self-test
    cd conformance && uv run python perf_gate_check.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import perf

#: The exact argv `run_bare()` passes to `perf.main` for the measurement leg — no `--out` override.
BARE_RUN_ARGS = ["--run"]
BARE_CHECK_ARGS = ["--check"]


class CheckFailure(Exception):
    """A real, named assertion failed."""


def _gate_decision(results_file: Path, adr_file: Path) -> tuple[bool, str]:
    """The same measured-or-ADR decision `perf.check()` makes, parameterised for fixtures."""
    if results_file.is_file():
        try:
            recorded = json.loads(results_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            recorded = {}
        if recorded.get("within_targets") is True:
            return True, f"recorded scale run within targets ({results_file.name})"
    if adr_file.is_file():
        return True, f"performance ADR present ({adr_file.name})"
    return False, "no recorded scale run within targets and no performance ADR"


def self_test() -> int:
    if "--out" in BARE_RUN_ARGS:
        print(
            "PERF GATE SELF-TEST FAILED: bare mode's --run invocation carries an --out override",
            file=sys.stderr,
        )
        return 1
    print("  bare --run invocation carries no --out override OK")

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        results_file = tmp / "perf-results.json"
        results_file.write_text(
            json.dumps({"within_targets": False, "estimated_total_seconds": 99999.0}),
            encoding="utf-8",
        )
        adr_file = tmp / "0009-performance.md"
        adr_file.write_text(
            "# performance ADR fixture\n\nnot within targets; gap documented.\n"
        )

        passed, reason = _gate_decision(results_file, adr_file)
        if not passed:
            print(
                f"PERF GATE SELF-TEST FAILED: ADR-present fixture did not pass ({reason})",
                file=sys.stderr,
            )
            return 1
        print(f"  ADR-fallback fixture (ADR present) passes OK: {reason}")

        adr_file.unlink()
        passed, reason = _gate_decision(results_file, adr_file)
        if passed:
            print(
                "PERF GATE SELF-TEST FAILED: not-within-targets fixture with no ADR "
                f"unexpectedly passed ({reason})",
                file=sys.stderr,
            )
            return 1
        print(f"  no-ADR-no-target fixture correctly fails: {reason}")

    print("PERF GATE SELF-TEST PASSED")
    return 0


def run_bare() -> int:
    """`perf.py --run` (no `--out` override) then `perf.py --check` — unmodified from `perf.py`."""
    run_rc = perf.main(BARE_RUN_ARGS)
    if run_rc != 0:
        print(f"PERF GATE CHECK FAILED: perf.py --run exited {run_rc}", file=sys.stderr)
        return run_rc
    check_rc = perf.main(BARE_CHECK_ARGS)
    if check_rc == 0:
        print("PERF GATE CHECK PASSED")
    else:
        print("PERF GATE CHECK FAILED: perf.py --check did not pass", file=sys.stderr)
    return check_rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Perf-at-scale gate check (item 16.4, P16.7)."
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the wrapper's own pass/fail logic against synthetic fixtures",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_bare()


if __name__ == "__main__":
    sys.exit(main())
