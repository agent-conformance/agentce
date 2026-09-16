"""Fresh-machine proof (SPEC §13.4 AX-1, §14.4, P5.7).

Emits ``{"quickstart_ci", "pages", "image_builds"}`` for the phase-5 eval's P5.7 check, validating
the fresh-machine configuration without spinning a container (that is the workflow's own job):

* ``quickstart_ci`` -- ``.github/workflows/quickstart.yml`` runs the quickstart on a clean runner for
  each of the three engines, each within a five-minute budget, and pushes no image;
* ``pages`` -- a quickstart page exists for Python, TypeScript, and Java, each with a heading;
* ``image_builds`` -- the engine container image is defined and the workflow builds it without pushing.

Run it as::

    cd conformance && uv run python fresh_machine.py --dry-run
    cd conformance && uv run python fresh_machine.py --json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quickstart.yml"
DOCKERFILE = REPO_ROOT / "engines" / "python" / "Dockerfile"
DOCS = REPO_ROOT / "docs"
ENGINES = ("python", "typescript", "java")
BUDGET_MINUTES = 5


def _workflow() -> dict[str, Any]:
    if not WORKFLOW.is_file():
        return {}
    parsed = yaml.safe_load(WORKFLOW.read_text("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _quickstart_ci() -> bool:
    workflow = _workflow()
    if not workflow:
        return False
    if "docker push" in WORKFLOW.read_text("utf-8"):
        return False
    jobs = workflow.get("jobs", {})
    if not isinstance(jobs, dict):
        return False
    # One job per engine, each with a five-minute budget on a clean runner.
    for engine in ENGINES:
        job = jobs.get(engine)
        if not isinstance(job, dict):
            return False
        if job.get("timeout-minutes") != BUDGET_MINUTES:
            return False
        if "ubuntu" not in str(job.get("runs-on", "")):
            return False
    return True


def _pages() -> bool:
    for engine in ENGINES:
        page = DOCS / f"quickstart-{engine}.md"
        if not page.is_file() or not page.read_text("utf-8").lstrip().startswith("# "):
            return False
    return True


def _image_builds() -> bool:
    if not DOCKERFILE.is_file():
        return False
    if not WORKFLOW.is_file():
        return False
    text = WORKFLOW.read_text("utf-8")
    return "docker build" in text and "docker push" not in text


def run() -> dict[str, bool]:
    return {
        "quickstart_ci": _quickstart_ci(),
        "pages": _pages(),
        "image_builds": _image_builds(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fresh_machine", description="Fresh-machine proof (SPEC §13.4, P5.7)."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="validate config and print a verdict"
    )
    args = parser.parse_args(argv)

    result = run()
    ok = all(result.values())
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif args.dry_run:
        if ok:
            print("FRESH-MACHINE OK")
        else:
            failed = ", ".join(k for k, v in sorted(result.items()) if not v)
            print(f"FRESH-MACHINE FAILED: {failed}")
    else:
        for key, value in sorted(result.items()):
            print(f"{key}={value}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
