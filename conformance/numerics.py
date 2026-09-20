"""The cross-language numerics gate (SPEC §7.2, ``spec/rules/numerics.md``, P3.1).

Runs the numerics-vectors golden through both engines and confirms they agree to the last published
digit. ``numerics.py --json`` prints ``{"total", "python_failed", "ts_failed", "identical"}`` — the
interface the phase-3 P3.1 check reads (every case passes in Python and TypeScript, and the two engines
produce identical results). The Python side calls :mod:`agentce.numerics` in process; the TypeScript
side is the engine's ``numerics`` verb, invoked once per vector file.

``--engines python,typescript,java`` also runs the shared exact-ordering edge vectors
(``edge-comparison.json``, the ``literal_order`` algorithm) and the shared number-token vectors
(``edge-canonical-numbers.json``, ``canonical_token``) through the Java engine's ``numerics`` verb
and adds ``java_failed``; ``identical`` then covers all three engines. The other vector files are not
implemented by the Java engine and are scored by Python and TypeScript only.
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
from agentce.canonical import CanonicalizationError, canonical_string
from agentce.structural import compare_literals
from ecs import java_launcher

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES = REPO_ROOT / "spec" / "rules" / "numerics-vectors" / "cases"
TS_ENGINE = REPO_ROOT / "engines" / "typescript"
FILES = (
    "quantile.json",
    "compare.json",
    "clopper-pearson.json",
    "incomplete-beta.json",
    "beta-quantile.json",
    "edge-comparison.json",
    "edge-canonical-numbers.json",
)
JAVA_FILES = ("edge-comparison.json", "edge-canonical-numbers.json")


def _python_result(algorithm: str, case: dict[str, Any]) -> Any:
    if algorithm == "nearest_rank_quantile":
        values = [N.parse_rational(v) for v in case["values"]]
        return str(N.nearest_rank_quantile(values, N.parse_rational(case["q"])))
    if algorithm == "canonical_token":
        try:
            return "canonical:" + canonical_string(json.loads(case["input"]))
        except CanonicalizationError as error:
            return f"error:{error.reason}"
    if algorithm == "literal_order":
        order = compare_literals(case["lhs"], case["rhs"])
        return "incomparable" if order is None else ("lt", "eq", "gt")[order + 1]
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


def _java_results(launcher: Path, casefile: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [str(launcher), "numerics", str(casefile.resolve())],
        capture_output=True,
        text=True,
        check=False,
    )
    start = proc.stdout.find("{")
    if start < 0:
        raise SystemExit(
            f"the Java numerics verb produced no JSON:\n{(proc.stderr or proc.stdout)[-800:]}"
        )
    parsed: dict[str, Any] = json.loads(proc.stdout[start:])
    return parsed


def run(
    engines: tuple[str, ...] = ("python", "typescript"), cases: Path = CASES
) -> dict[str, Any]:
    """Score every numerics vector in ``cases`` through the selected engines (P3.1)."""
    total = python_failed = ts_failed = java_failed = 0
    identical = True
    launcher = java_launcher() if "java" in engines else None
    for name in FILES:
        casefile = cases / name
        data = json.loads(casefile.read_text(encoding="utf-8"))
        algorithm = data["algorithm"]
        ts = _ts_results(casefile)
        java = (
            _java_results(launcher, casefile)
            if launcher is not None and name in JAVA_FILES
            else None
        )
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
            if java is not None:
                java_result = java.get(case["name"])
                if java_result != expected:
                    java_failed += 1
                if java_result != py_result:
                    identical = False
    result: dict[str, Any] = {
        "total": total,
        "python_failed": python_failed,
        "ts_failed": ts_failed,
        "identical": identical,
    }
    if launcher is not None:
        result["java_failed"] = java_failed
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="numerics",
        description="Cross-language numerics-vectors gate for both engines (P3.1).",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--engines",
        default="python,typescript",
        help="comma-separated engines to score (python and typescript always run; add java)",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=CASES,
        help="directory holding the vector files (default: spec/rules/numerics-vectors/cases)",
    )
    args = parser.parse_args(argv)
    engines = tuple(part for part in args.engines.split(",") if part)
    unknown = set(engines) - {"python", "typescript", "java"}
    if unknown or not {"python", "typescript"} <= set(engines):
        parser.error(
            "--engines must include python and typescript and only known engines"
        )
    result = run(engines, args.cases)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"total={result['total']} python_failed={result['python_failed']} "
            f"ts_failed={result['ts_failed']} "
            + (
                f"java_failed={result['java_failed']} "
                if "java_failed" in result
                else ""
            )
            + f"identical={result['identical']}"
        )
    failed = (
        result["python_failed"] + result["ts_failed"] + result.get("java_failed", 0)
    )
    return 0 if failed == 0 and result["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
