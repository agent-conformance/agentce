"""The cross-language numerics gate (SPEC §7.2, ``spec/rules/numerics.md``, P3.1).

Runs the numerics-vectors golden through both engines and confirms they agree to the last published
digit. ``numerics.py --json`` prints ``{"total", "python_failed", "ts_failed", "identical"}`` — the
interface the phase-3 P3.1 check reads (every case passes in Python and TypeScript, and the two engines
produce identical results). The Python side calls :mod:`agentce.numerics` in process; the TypeScript
side is the engine's ``numerics`` verb, invoked once per vector file.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from agentce import numerics as N

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES = REPO_ROOT / "spec" / "rules" / "numerics-vectors" / "cases"
TS_ENGINE = REPO_ROOT / "engines" / "typescript"
FILES = (
    "quantile.json",
    "compare.json",
    "clopper-pearson.json",
    "incomplete-beta.json",
    "beta-quantile.json",
)


def _python_result(algorithm: str, case: dict[str, Any]) -> Any:
    if algorithm == "nearest_rank_quantile":
        values = [N.parse_rational(v) for v in case["values"]]
        return str(N.nearest_rank_quantile(values, N.parse_rational(case["q"])))
    if algorithm == "compare":
        lhs = Fraction(int(case["numerator"]), int(case["denominator"]))
        return N.compare(lhs, case["op"], N.parse_rational(case["threshold"]))
    if algorithm == "clopper_pearson":
        interval = N.clopper_pearson(case["k"], case["n"], Decimal(case["alpha"]))
        return {
            "lower": str(N.round_presentation(interval.lower)),
            "upper": str(N.round_presentation(interval.upper)),
        }
    if algorithm == "regularised_incomplete_beta":
        got = N.regularised_incomplete_beta(Decimal(case["x"]), case["a"], case["b"])
        return str(N.round_presentation(got))
    if algorithm == "beta_quantile":
        got = N.beta_quantile(case["a"], case["b"], Decimal(case["p"]))
        return str(N.round_presentation(got))
    raise SystemExit(f"unknown algorithm {algorithm!r}")


def _expected(algorithm: str, case: dict[str, Any]) -> Any:
    if algorithm == "clopper_pearson":
        return {"lower": case["lower"], "upper": case["upper"]}
    return case["expected"]


def _ts_results(casefile: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["pnpm", "--silent", "agentce", "numerics", str(casefile.resolve())],
        cwd=str(TS_ENGINE),
        capture_output=True,
        text=True,
        check=False,
    )
    start = proc.stdout.find("{")
    if start < 0:
        raise SystemExit(
            f"the TypeScript numerics verb produced no JSON:\n{(proc.stderr or proc.stdout)[-800:]}"
        )
    parsed: dict[str, Any] = json.loads(proc.stdout[start:])
    return parsed


def run() -> dict[str, Any]:
    """Score every numerics vector through both engines (P3.1)."""
    total = python_failed = ts_failed = 0
    identical = True
    for name in FILES:
        casefile = CASES / name
        data = json.loads(casefile.read_text(encoding="utf-8"))
        algorithm = data["algorithm"]
        ts = _ts_results(casefile)
        for case in data["cases"]:
            total += 1
            expected = _expected(algorithm, case)
            py_result = _python_result(algorithm, case)
            ts_result = ts.get(case["name"])
            if py_result != expected:
                python_failed += 1
            if ts_result != expected:
                ts_failed += 1
            if py_result != ts_result:
                identical = False
    return {
        "total": total,
        "python_failed": python_failed,
        "ts_failed": ts_failed,
        "identical": identical,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="numerics",
        description="Cross-language numerics-vectors gate for both engines (P3.1).",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)
    result = run()
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"total={result['total']} python_failed={result['python_failed']} "
            f"ts_failed={result['ts_failed']} identical={result['identical']}"
        )
    return 0 if result["python_failed"] == 0 and result["ts_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
