#!/usr/bin/env python3
"""A user-path check for OSCAL `methods` (18.17b): drives the real installed Python CLI's
``report --from`` over a real, committed assertions set spanning all three evaluation modes, and
asserts the rendered document's `methods` values directly -- not merely that engines agree with each
other (``ecs.py --compare``'s job), but that the value itself is right.

Run with (from the repository root):

    cd conformance && uv run --project ../engines/python --frozen python oscal_methods_cli_check.py

The assertions come from ``engines/typescript/testdata/report-golden.json``'s own ``assertions`` field
(itself a captured, real Python-reference run over the ``eu-ai-act`` catalog's ``OVS-03/failed.jsonl``
fixture; see ``generate_goldens.py``), so the expected counts below are pinned to that same real data,
not invented: 33 automated + 11 semi-automated = 44 observations get ``["TEST"]``, 5 manual get
``["EXAMINE"]``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "engines" / "typescript" / "testdata" / "report-golden.json"


def main() -> int:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assertions = golden["assertions"]

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(assertions, f)
        assertions_path = f.name

    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO_ROOT / "engines" / "python"),
            "--frozen",
            "agentce",
            "report",
            "--from",
            assertions_path,
            "--format",
            "oscal",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": __import__("os").environ["PATH"]},
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode

    doc = json.loads(result.stdout)
    observations = doc["assessment-results"]["results"][0]["observations"]
    counts = Counter(tuple(o["methods"]) for o in observations)

    expected = Counter({("TEST",): 44, ("EXAMINE",): 5})
    if counts != expected:
        print(
            f"FAIL: methods distribution is {dict(counts)}, expected {dict(expected)}",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: real CLI run over {len(observations)} observations matches {dict(expected)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
