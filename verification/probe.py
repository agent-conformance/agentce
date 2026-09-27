#!/usr/bin/env python3
"""Probe the runner with registries this script writes, so the runner's report about itself is never the evidence.

Run it with the runner to probe: ``python3 verification/probe.py [--runner PATH]``. Exit 0 when every probe holds.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def gate(gid: str, cmd: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "id": gid,
        "title": f"Probe gate {gid}",
        "tier": "quick",
        "cmd": cmd,
        "timeout_s": 30,
        "rubric": {"pass": "exits 0", "fail": "exits non-zero"},
        "invariant": "Tests run with no network",
        "faults": [
            {
                "description": "flip the marker",
                "file": "marker.txt",
                "search": "good",
                "replace": "bad",
            }
        ],
        **extra,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, default=HERE / "run")
    runner = parser.parse_args().runner.resolve()
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "verification").mkdir()
        (root / "AGENTS.md").write_text(
            "- Tests run with no network\n", encoding="utf-8"
        )
        (root / "verification" / "CHANGELOG.md").write_text(
            "VG-FAILS VG-MISSING VG-BLIND VG-SENSITIVE\n", encoding="utf-8"
        )
        (root / "marker.txt").write_text("good\n", encoding="utf-8")
        sensitive = [
            sys.executable,
            "-c",
            "import sys; sys.exit(0 if 'good' in open('marker.txt').read() else 1)",
        ]
        registry = root / "gates.json"

        def write(gates: list[dict[str, Any]]) -> None:
            registry.write_text(
                json.dumps({"version": 1, "suite_version": "0", "gates": gates}),
                encoding="utf-8",
            )

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    sys.executable,
                    str(runner),
                    "--root",
                    str(root),
                    "--registry",
                    str(registry),
                    *args,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        def expect(name: str, ok: bool) -> None:
            print(f"{'ok  ' if ok else 'FAIL'} {name}")
            if not ok:
                failures.append(name)

        write(
            [
                gate("VG-FAILS", [sys.executable, "-c", "raise SystemExit(3)"]),
                gate("VG-MISSING", ["true"], requires=["no-such-tool-for-the-probe"]),
                gate("VG-BLIND", [sys.executable, "-c", "pass"]),
                gate("VG-SENSITIVE", sensitive),
            ]
        )
        fails = run("--gate", "VG-FAILS", "--json")
        expect(
            "a failing command is fail and exits 1",
            fails.returncode == 1 and json.loads(fails.stdout)["status"] == "fail",
        )
        missing = run("--gate", "VG-MISSING", "--json")
        expect(
            "a missing tool is skipped, never pass",
            json.loads(missing.stdout)["status"] == "skipped",
        )
        expect(
            "a passing command is pass",
            json.loads(run("--gate", "VG-SENSITIVE", "--json").stdout)["status"]
            == "pass",
        )
        demo = run("--demo-fault", "VG-SENSITIVE", "--json")
        expect(
            "a sensitive gate turns RED on its fault and the file is restored",
            demo.returncode == 0
            and json.loads(demo.stdout)["red_on_fault"]
            and (root / "marker.txt").read_text(encoding="utf-8") == "good\n",
        )
        expect(
            "a gate blind to its fault is reported",
            run("--demo-fault", "VG-BLIND").returncode == 1,
        )
        quick = run("--quick", "--json")
        expect(
            "the quick tier fails when a gate fails",
            quick.returncode == 1 and json.loads(quick.stdout)["status"] == "fail",
        )
        expect("an unknown gate exits 2", run("--gate", "VG-NOPE").returncode == 2)

        write([gate("VG-SENSITIVE", sensitive, faults=[])])
        bad = run("--lint-registry")
        expect(
            "a gate without a seeded fault is rejected with its key",
            bad.returncode == 1 and "registry.gate_without_fault" in bad.stderr,
        )
        registry.write_text("{not json", encoding="utf-8")
        broken = run("--lint-registry")
        expect(
            "an unreadable registry gives a stable key, not a traceback",
            broken.returncode == 1
            and "registry.unreadable" in broken.stderr
            and "Traceback" not in broken.stderr,
        )
    print(f"{len(failures)} probe(s) failed" if failures else "all probes hold")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
