#!/usr/bin/env python3
"""Self-test for the verification runner: run it against small throwaway registries and check every outcome.

    python3 verification/selftest.py

Prints one `ok` line per case and exits 0, or names the first case that ended differently and exits 1.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUN = HERE / "run"
SCHEMA = json.loads((HERE / "results.schema.json").read_text(encoding="utf-8"))


def conforms(value: Any, schema: dict[str, Any], defs: dict[str, Any]) -> bool:
    """The small subset of JSON Schema that results.schema.json uses."""
    if "$ref" in schema:
        return conforms(value, defs[schema["$ref"].rsplit("/", 1)[1]], defs)
    if "enum" in schema:
        return bool(value in schema["enum"])
    kinds = {"object": dict, "array": list, "string": str}
    if not isinstance(value, kinds[schema["type"]]):
        return False
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and any(
            key not in props for key in value
        ):
            return False
        return all(conforms(item, props[key], defs) for key, item in value.items())
    if isinstance(value, list):
        return all(conforms(item, schema["items"], defs) for item in value)
    return True


def gate(gid: str, cmd: list[str], **more: Any) -> dict[str, Any]:
    return {
        "id": gid,
        "title": f"case {gid}",
        "tier": "quick",
        "cmd": cmd,
        "timeout_s": 5,
        "rubric": {"pass": "exits 0", "fail": "exits non-zero"},
        "invariant": "the sandbox invariant",
        "faults": [
            {
                "description": "make the target say bad",
                "file": "target.txt",
                "search": "good",
                "replace": "bad",
            }
        ],
        **more,
    }


def run(root: Path, gates: list[dict[str, Any]], *args: str) -> tuple[int, str]:
    (root / "gates.json").write_text(
        json.dumps({"version": 1, "suite_version": "0.0.0", "gates": gates}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(RUN),
            "--root",
            str(root),
            "--registry",
            str(root / "gates.json"),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    failures: list[str] = []

    def expect(case: str, condition: bool) -> None:
        print(("ok    " if condition else "FAIL  ") + case)
        if not condition:
            failures.append(case)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "AGENTS.md").write_text("the sandbox invariant\n", encoding="utf-8")
        (root / "verification").mkdir()
        (root / "verification" / "CHANGELOG.md").write_text(
            "## 0.0.0\n\nVG-PASS VG-FAIL VG-SLOW VG-MISSING VG-TEETH VG-BLIND\n",
            encoding="utf-8",
        )
        target = root / "target.txt"
        target.write_text("good\n", encoding="utf-8")
        py = sys.executable
        grep = [
            py,
            "-c",
            "import sys; sys.exit(0 if 'good' in open('target.txt').read() else 1)",
        ]
        blind = [py, "-c", "pass"]

        code, out = run(root, [gate("VG-PASS", blind)], "--gate", "VG-PASS", "--json")
        result = json.loads(out)
        expect(
            "a passing command is reported as pass, exit 0",
            code == 0 and result["status"] == "pass",
        )
        expect(
            "a single-gate result matches the schema",
            conforms(result, SCHEMA["$defs"]["gate"], SCHEMA["$defs"]),
        )

        code, out = run(
            root,
            [gate("VG-FAIL", [py, "-c", "raise SystemExit(3)"])],
            "--gate",
            "VG-FAIL",
            "--json",
        )
        result = json.loads(out)
        expect(
            "a failing command is reported as fail, exit 1",
            code == 1 and result["status"] == "fail",
        )
        expect(
            "a failure carries a fix hint and the re-run command",
            bool(result.get("fix_hint")) and result["rerun"].endswith("VG-FAIL"),
        )

        code, out = run(
            root,
            [gate("VG-SLOW", [py, "-c", "import time; time.sleep(30)"], timeout_s=1)],
            "--gate",
            "VG-SLOW",
            "--json",
        )
        result = json.loads(out)
        expect(
            "a command that runs out of time is reported as fail",
            code == 1
            and result["status"] == "fail"
            and "timed out" in result["reason"],
        )

        code, out = run(
            root,
            [gate("VG-MISSING", blind, requires=["no-such-tool-xyz"])],
            "--gate",
            "VG-MISSING",
            "--json",
        )
        result = json.loads(out)
        expect(
            "a missing tool is skipped with a reason, never pass",
            code == 0
            and result["status"] == "skipped"
            and "no-such-tool-xyz" in result["reason"],
        )

        code, out = run(
            root,
            [
                gate("VG-PASS", blind),
                gate("VG-FAIL", [py, "-c", "raise SystemExit(1)"]),
            ],
            "--quick",
            "--json",
        )
        summary = json.loads(out)
        expect(
            "a tier run fails when any gate fails and matches the schema",
            code == 1
            and summary["status"] == "fail"
            and conforms(summary, SCHEMA, SCHEMA["$defs"]),
        )

        code, out = run(
            root, [gate("VG-TEETH", grep)], "--demo-fault", "VG-TEETH", "--json"
        )
        outcome = json.loads(out)
        expect(
            "a sensitive gate is RED on the fault and GREEN after restore",
            code == 0 and outcome["red_on_fault"] and outcome["green_after_restore"],
        )
        expect("the fault is restored byte for byte", target.read_bytes() == b"good\n")

        code, out = run(
            root, [gate("VG-BLIND", blind)], "--demo-fault", "VG-BLIND", "--json"
        )
        outcome = json.loads(out)
        expect(
            "a gate that ignores the fault is reported, exit 1",
            code == 1 and not outcome["red_on_fault"],
        )
        expect(
            "the fault is restored after an insensitive gate",
            target.read_bytes() == b"good\n",
        )

        code, out = run(root, [gate("VG-PASS", blind)], "--gate", "VG-NOPE")
        expect(
            "an unknown gate id exits 2 with a stable key",
            code == 2 and "suite.unknown_gate" in out,
        )

        code, out = run(root, [gate("VG-PASS", blind)], "--lint-registry")
        expect("a good registry is accepted", code == 0 and "REGISTRY-LINT OK" in out)
        bad_cases = {
            "registry.gate_without_fault": {
                k: v for k, v in gate("VG-PASS", blind).items() if k != "faults"
            },
            "registry.gate_without_claim": {
                k: v for k, v in gate("VG-PASS", blind).items() if k != "invariant"
            },
            "registry.bad_gate_id": gate("not-an-id", blind),
            "registry.duplicate_gate_id": None,
            "registry.gate_not_in_changelog": gate("VG-UNLISTED", blind),
            "registry.fault_not_applicable": gate(
                "VG-PASS",
                blind,
                faults=[
                    {
                        "description": "x",
                        "file": "target.txt",
                        "search": "absent",
                        "replace": "y",
                    }
                ],
            ),
        }
        for key, bad in bad_cases.items():
            gates = (
                [gate("VG-PASS", blind), gate("VG-PASS", blind)]
                if bad is None
                else [bad]
            )
            code, out = run(root, gates, "--lint-registry")
            expect(f"the lint rejects with {key}", code == 1 and key in out)

    demo = subprocess.run(
        [sys.executable, str(RUN), "--lint-registry", "--demo-reject"],
        capture_output=True,
        text=True,
        check=False,
    )
    expect(
        "the built-in bad registries are all rejected",
        demo.returncode == 0 and "REGISTRY-LINT REJECTED" in demo.stdout,
    )
    print(f"{len(failures)} failed" if failures else "all cases ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
