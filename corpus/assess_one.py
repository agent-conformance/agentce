"""Run one corpus project end to end through the engine (SPEC §11.5; eval interface, item 1.10).

This helper owns the corpus project layout: it maps a project id to the bundle, applicability profile,
domain binding, and deviation register the generator wrote, points the assessment at the base catalog
under ``spec/catalogs/``, and invokes the engine's ``assess`` command in process. It is the entrypoint
the phase-1 performance smoke drives against the high-volume project (SPEC §11.4, P1.8), and the corpus
test suite uses it to prove every project's authored outcomes against the reference engine (SPEC §11.3).

Its exit code reports whether the assessment *ran*, not whether the subject was conformant: a clean run
that produced ``assertions.json`` exits 0 even when the report carries findings; only a genuine engine
or input error is a non-zero exit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentce.cli import main as agentce_main

#: The repository root (the parent of this ``corpus`` package).
REPO_ROOT = Path(__file__).resolve().parents[1]
#: The base catalog the Phase-1 corpus is evaluated against (item 1.9).
CATALOG_DIR = REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
CATALOG_ID = "eu-ai-act@2026.09"


def assess_project(corpus: Path, project_id: str, out: Path) -> int:
    """Assess corpus project ``project_id`` into ``out``; return a process exit code."""
    proj = corpus / "projects" / project_id
    bundle = proj / "evidence"
    profile = proj / "applicability.yaml"
    domain = proj / "domain.linkml.yaml"
    deviations = proj / "deviations.yaml"
    for required, what in (
        (bundle, "evidence bundle"),
        (profile, "applicability profile"),
        (domain, "domain binding"),
    ):
        if not required.exists():
            print(
                f"assess_one: project {project_id!r} has no {what} at {required}",
                file=sys.stderr,
            )
            return 3
    if not CATALOG_DIR.is_dir():
        print(f"assess_one: base catalog not found at {CATALOG_DIR}", file=sys.stderr)
        return 3
    out.mkdir(parents=True, exist_ok=True)

    argv = [
        "assess",
        "--bundle",
        str(bundle),
        "--profile",
        str(profile),
        "--domain",
        str(domain),
        "--catalog",
        CATALOG_ID,
        "--catalog-dir",
        str(CATALOG_DIR),
        "--out",
        str(out),
    ]
    if deviations.is_file():
        argv += ["--deviations", str(deviations)]

    rc = agentce_main(argv)
    if rc == 3:  # an input/engine error; the assessment did not run
        return rc
    if not (out / "assertions.json").is_file():
        print("assess_one: the assessment produced no assertions.json", file=sys.stderr)
        return 1
    print(f"assess_one: assessed {project_id} into {out} (engine exit {rc})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus.assess_one",
        description="Run one simulated-corpus project end to end through the engine (SPEC §11.5).",
    )
    parser.add_argument(
        "--corpus", required=True, help="the generated corpus directory"
    )
    parser.add_argument(
        "--project",
        required=True,
        help="the project id, e.g. credit/custom-loop/known-pass",
    )
    parser.add_argument(
        "--out", required=True, help="the output directory for the report"
    )
    args = parser.parse_args(argv)
    return assess_project(Path(args.corpus), args.project, Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
