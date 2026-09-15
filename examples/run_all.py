"""Run every example and check its bundle validates with zero quarantines (SPEC §13.4 AX-4).

For each ``examples/<style>/``, runs ``run.sh`` into a temporary bundle and confirms the engine's
ingest accepts every event with no quarantine. This is the check that keeps the examples green: they
are the documentation's source of truth, so a broken example is a broken doc. ``--json`` prints the
machine-readable report; exit 0 iff every example passes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from agentce.bundle import load_bundle
from agentce.ingest import ingest

HERE = Path(__file__).resolve().parent


def example_dirs() -> list[Path]:
    return sorted(d for d in HERE.iterdir() if d.is_dir() and (d / "run.sh").exists())


def check_one(directory: Path) -> dict[str, Any]:
    """Run one example's run.sh into a temp bundle and validate it."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "bundle"
        proc = subprocess.run(
            [str(directory / "run.sh"), str(bundle)],
            capture_output=True,
            text=True,
            cwd=str(directory),
            check=False,
        )
        if proc.returncode != 0 or not (bundle / "manifest.json").exists():
            return {
                "example": directory.name,
                "ok": False,
                "error": (proc.stderr or proc.stdout).strip()[-500:]
                or "no bundle written",
            }
        ingested = ingest(load_bundle(bundle))
        ok = not ingested.quarantined and bool(ingested.accepted)
        return {
            "example": directory.name,
            "ok": ok,
            "accepted": len(ingested.accepted),
            "quarantined": len(ingested.quarantined),
            "event_types": sorted({str(e["data"]["@type"]) for e in ingested.accepted}),
        }


def run() -> dict[str, Any]:
    results = [check_one(directory) for directory in example_dirs()]
    passed = bool(results) and all(r["ok"] for r in results)
    return {
        "results": results,
        "total": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        # The overall status: "ok" when every example produced a bundle that validated cleanly.
        "examples": "ok" if passed else "failed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run and validate every example (SPEC 13.4 AX-4)."
    )
    parser.add_argument(
        "--check", action="store_true", help="validate each example's bundle"
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    ns = parser.parse_args(argv)

    report = run()
    if ns.json:
        print(json.dumps(report))
    else:
        for result in report["results"]:
            status = "ok" if result["ok"] else "FAIL"
            print(
                f"{status:4} {result['example']}: {result.get('accepted', 0)} accepted, {result.get('quarantined', 0)} quarantined"
            )
        print(f"{report['passed']}/{report['total']} examples passed")
    return 0 if report["examples"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
