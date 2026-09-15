"""Adapter conformance across every shipped adapter (SPEC 11.5, 12.3).

The v1 adapters each live in their own environment because they share the package name
``agentce_adapters``; this orchestrator runs each one in its own environment (``uv run --project
<adapter> python _probe.py``) and aggregates the results. For every adapter it reports, over that
adapter's fixtures, how many events are byte-identical to their expected canonical form and whether
they round-trip through the engine's ingest.

Run it as ``cd adapters && uv run python conformance.py --json``. With ``--out <dir>`` it also writes a
per-adapter implementation report (SPEC 11.5) to ``<dir>/adapters/<name>/implementation-report.json``.
The aggregate ``identical == total`` and ``round_trip == true`` are the adapter-section gates of the
engine conformance suite. Standard library only; no network.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROBE = HERE / "_probe.py"


def adapter_dirs(root: Path) -> list[Path]:
    """Every conformance-testable adapter under ``root`` (a project with a fixtures module)."""
    return sorted(
        child
        for child in root.iterdir()
        if child.is_dir()
        and (child / "pyproject.toml").is_file()
        and (child / "src" / "agentce_adapters" / "fixtures.py").is_file()
    )


def _probe_adapter(adapter_dir: Path) -> dict[str, Any]:
    """Run one adapter's conformance in its own environment; return its result (or an error entry)."""
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(adapter_dir),
            "--quiet",
            "python",
            str(PROBE),
            str(adapter_dir),
        ],
        capture_output=True,
        text=True,
        cwd=str(HERE),
        check=False,
    )
    if proc.returncode != 0:
        return {
            "adapter": adapter_dir.name,
            "conventions": [],
            "total": 0,
            "identical": 0,
            "round_trip": False,
            "events_by_type": {},
            "error": (proc.stderr or proc.stdout).strip()[-800:],
        }
    parsed: dict[str, Any] = json.loads(proc.stdout)
    return parsed


def aggregate(per_adapter: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold per-adapter results into the adapter-conformance report."""
    total = sum(int(a["total"]) for a in per_adapter)
    identical = sum(int(a["identical"]) for a in per_adapter)
    round_trip = bool(per_adapter) and all(bool(a["round_trip"]) for a in per_adapter)
    return {
        "adapters": [a["adapter"] for a in per_adapter],
        "total": total,
        "identical": identical,
        "round_trip": round_trip,
        "by_adapter": per_adapter,
    }


def run(root: Path, out_dir: Path | None = None) -> dict[str, Any]:
    """Run conformance over every adapter under ``root`` and return the aggregate report."""
    per_adapter = [_probe_adapter(directory) for directory in adapter_dirs(root)]
    report = aggregate(per_adapter)
    if out_dir is not None:
        for entry in per_adapter:
            report_dir = out_dir / "adapters" / str(entry["adapter"])
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "implementation-report.json").write_text(
                json.dumps(entry, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "adapter-conformance.json").write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run adapter conformance across every adapter."
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--out", help="write per-adapter implementation reports to this directory"
    )
    parser.add_argument(
        "--root", default=str(HERE), help="the adapters directory (default: this one)"
    )
    ns = parser.parse_args(argv)

    report = run(Path(ns.root), Path(ns.out) if ns.out else None)
    if ns.json:
        print(json.dumps(report))
    else:
        print(
            f"adapter conformance: {report['identical']}/{report['total']} identical; "
            f"round_trip {report['round_trip']}; {len(report['adapters'])} adapters"
        )
    ok = (
        report["total"] > 0
        and report["identical"] == report["total"]
        and report["round_trip"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
