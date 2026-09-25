#!/usr/bin/env python3
"""integration_breadth_check - aggregate item 16.3's framework/trace-store/GRC breadth checks.

Item 16.3 (SPEC/PHASE-16-DOMAIN-COMPLETION.md row 16.d-16.g) proves AgentCE fits an adopter's existing
stack in three independent ways: real framework integrations (``tools/framework_examples_check.py``,
C1/C2), trace-store ingestion from where an adopter's telemetry already lives
(``tools/trace_store_ingest_check.py``, C3/C4), and GRC connector recipes beyond raw OSCAL
(``tools/grc/evidence_export.py`` and ``tools/grc_connectors_check.py``, C6/C7). This module aggregates
all four by ``subprocess``-ing into each one's own real environment -- the ``tools`` environment this
module itself runs in never imports ``agentce`` or any of the five new frameworks, so every sub-check
runs in the environment it actually needs, not this one's.

    integration_breadth_check.py             run every sub-check for real (the invocation CI uses)
    integration_breadth_check.py --self-test run only each sub-check's own ``--self-test`` (fast,
                                              synthetic fixtures) -- the pinned item-16.3 acceptance
                                              command, ``uv run --project tools --frozen python -m
                                              integration_breadth_check --self-test``

Standard library only; no network beyond what each sub-check's own real (offline, keyless) invocation
already does; no learned component in this script itself.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The one real fixture every "real" evidence_export.py invocation reads (spec/report/examples/
#: assertions.example.json -- the same committed, genuine assertions.json the C6 self-test and
#: grc_connectors_check.py already use).
EVIDENCE_EXPORT_FIXTURE = "spec/report/examples/assertions.example.json"

#: name -> the uv project each sub-check must run under, its script path relative to ROOT. Three of
#: the four take no positional args in either mode (--self-test, or bare = "check the repo for
#: real"); evidence_export.py is the exception (see EVIDENCE_EXPORT_SCRIPT below), since unlike the
#: others it has no bare "check the repo" mode of its own -- it always needs exactly two positional
#: args, <assertions.json> <out.json>.
EVIDENCE_EXPORT_SCRIPT = "tools/grc/evidence_export.py"
SUBCHECKS: list[tuple[str, str, str]] = [
    ("framework-hooks (C1/C2)", "engines/python", "tools/framework_examples_check.py"),
    ("trace-store (C3/C4)", "engines/python", "tools/trace_store_ingest_check.py"),
    ("grc-evidence-export (C6)", "tools", EVIDENCE_EXPORT_SCRIPT),
    ("grc-connectors (C6/C7)", "engines/python", "tools/grc_connectors_check.py"),
]


def _run(project: str, script: str, args: list[str]) -> tuple[int, str]:
    cmd = ["uv", "run", "--project", project, "--frozen", "python", script, *args]
    proc = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True, check=False
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def check(*, self_test: bool) -> list[str]:
    """Every sub-check that failed, named, with its own tail of output."""
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out_path = str(Path(tmp) / "evidence.json")
        for name, project, script in SUBCHECKS:
            if self_test:
                args = ["--self-test"]
            elif script == EVIDENCE_EXPORT_SCRIPT:
                args = [EVIDENCE_EXPORT_FIXTURE, out_path]
            else:
                args = []
            code, output = _run(project, script, args)
            if code != 0:
                problems.append(f"{name}: exit {code}\n{output.strip()[-2000:]}")
    return problems


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        problems = check(self_test=True)
    elif not argv:
        problems = check(self_test=False)
    else:
        print(__doc__)
        return 2
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    mode = "self-tests" if argv else "real checks"
    print(
        f"integration_breadth_check: ok — all {len(SUBCHECKS)} sub-check {mode} passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
