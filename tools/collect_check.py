#!/usr/bin/env python3
"""collect_check - keep the pages and the example config that introduce ``agentce collect`` honest.

``agentce collect`` plans a collection job from a config; a real run has no source connector, so it records
every source ``incomplete`` and exits 1. A page that tells a reader to collect evidence from source systems
with it, or an example config that models an OTLP export (which is pushed to files or object storage, not
polled) as a URL a job pulls, claims something the engine cannot do. This check ties the prose and the
config to the real behaviour:

1. It runs the real ``agentce collect`` against the shipped example config and asks whether every source
   came back complete. If a connector ever ships, no caveat is owed and the check passes without one; the
   roadmap wording is then retired together with the claim's flip in the register.
2. While the run is incomplete, the generated command page must say so (``no source connector`` or
   ``incomplete``), the integration guide must say so beside any mention of the command (silence is also
   honest), and the example config must say so and must say OTLP export is pushed, not polled.

    collect_check.py              check the repository (the invocation the CI job uses)
    collect_check.py --self-test  prove the checker discriminates: a good tree passes, and each bad tree
                                  fails for exactly the rule it targets

It imports ``agentce`` to run the probe, so run it in the engine's environment
(``uv run --project engines/python --frozen python tools/collect_check.py``). Standard library only
otherwise; no network, no learned component.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = "adapters/fixtures/collect-dry-run.yaml"
INTEGRATE = "docs/integrate.md"
COMMAND_PAGE = "docs/reference/commands/collect.md"
# The engine's own words for the failure (collect.py writes both), so a caveat is recognisable
# without a phrase invented for this check.
HONEST = ("no source connector", "incomplete")
# Words that show a config acknowledges an OTLP export is pushed to files or object storage.
PUSH = ("push", "object storage", "file export", "exports traces")
# Lines around a mention of the command in the integration guide that may carry its caveat.
WINDOW = (2, 5)


def collect_completes(root: Path) -> bool:
    """Whether a real (non-dry-run) ``agentce collect`` of the shipped config completes every source."""
    cli = importlib.import_module("agentce.cli")
    with tempfile.TemporaryDirectory() as tmp:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(
                [
                    "collect",
                    "--config",
                    str(root / CONFIG),
                    "--out",
                    str(Path(tmp) / "bundle"),
                    "--json",
                ]
            )
    if code != 0:
        return False
    try:
        sources = json.loads(out.getvalue()).get("sources", [])
    except ValueError:
        return False
    return bool(sources) and all(s.get("completeness") != "incomplete" for s in sources)


def _honest(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in HONEST)


def problems_for(root: Path, completes: bool) -> list[str]:
    """Every way ``root`` presents ``agentce collect`` as more than it does while ``completes`` is false."""
    if completes:
        return []
    problems: list[str] = []
    for page in (CONFIG, INTEGRATE, COMMAND_PAGE):
        if not (root / page).is_file():
            problems.append(f"{page} is missing")
    if problems:
        return problems

    command_page = (root / COMMAND_PAGE).read_text(encoding="utf-8")
    if not _honest(command_page):
        problems.append(
            f"{COMMAND_PAGE} documents the command and never says a real run has no source connector "
            "and records every source incomplete"
        )
    lines = (root / INTEGRATE).read_text(encoding="utf-8").splitlines()
    for at, line in enumerate(lines):
        if "agentce collect" not in line:
            continue
        window = "\n".join(lines[max(0, at - WINDOW[0]) : at + WINDOW[1]])
        if not _honest(window):
            problems.append(
                f"{INTEGRATE} line {at + 1} mentions `agentce collect` without saying, beside it, that "
                "a real run has no source connector and records every source incomplete"
            )
    config = (root / CONFIG).read_text(encoding="utf-8")
    if not _honest(config):
        problems.append(
            f"{CONFIG} models pull jobs and never says a real run has no source connector"
        )
    if not any(marker in config.lower() for marker in PUSH):
        problems.append(
            f"{CONFIG} models an otel-genai endpoint as a URL the job polls without saying an OTLP "
            "export is pushed to files or object storage"
        )
    return problems


def check() -> int:
    completes = collect_completes(ROOT)
    problems = problems_for(ROOT, completes)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    state = "completes" if completes else "has no connector and every surface says so"
    print(f"collect_check: ok — a real collect {state}")
    return 0


# --- self-test ---------------------------------------------------------------------------------------

GOOD_CONFIG = (
    "# A real run has no source connector yet.\n"
    "# An OTLP export is pushed: a Collector exports traces to files or object storage.\n"
)
GOOD_INTEGRATE = (
    "1. Emit evidence into a bundle. `agentce collect` has no source connector: a real run records\n"
    "   every source `incomplete`.\n"
)
GOOD_PAGE = "A real run records every source incomplete: no source connector.\n"


def _tree(root: Path, files: dict[str, str]) -> Path:
    defaults = {CONFIG: GOOD_CONFIG, INTEGRATE: GOOD_INTEGRATE, COMMAND_PAGE: GOOD_PAGE}
    for page, text in {**defaults, **files}.items():
        path = root / page
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _fails(
    name: str,
    needle: str,
    files: dict[str, str] | None = None,
    completes: bool = False,
) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        found = problems_for(_tree(Path(tmp), files or {}), completes)
    ok = any(needle in p for p in found)
    print(f"self-test {name}: {'ok' if ok else 'FAIL'}")
    return ok


def _passes(
    name: str, files: dict[str, str] | None = None, completes: bool = False
) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        found = problems_for(_tree(Path(tmp), files or {}), completes)
    print(f"self-test {name}: {'ok' if not found else 'FAIL ' + str(found)}")
    return not found


def self_test() -> int:
    results = [
        _passes("good-tree"),
        _passes(
            "silence-in-the-guide-is-honest",
            {INTEGRATE: "Emit evidence into a bundle.\n"},
        ),
        _passes(
            "no-caveat-owed-once-collect-completes",
            {CONFIG: "job: {}\n", INTEGRATE: "collect with `agentce collect`\n"},
            completes=True,
        ),
        _fails(
            "guide-promises-collection",
            f"{INTEGRATE} line 1",
            {INTEGRATE: "1. Emit evidence, or collect it with `agentce collect`.\n"},
        ),
        _fails(
            "caveat-far-from-the-mention-does-not-count",
            f"{INTEGRATE} line 1",
            {
                INTEGRATE: "collect it with `agentce collect`.\n"
                + "\n" * 12
                + "The engine has no source connector.\n"
            },
        ),
        _fails(
            "command-page-silent",
            f"{COMMAND_PAGE} documents the command",
            {COMMAND_PAGE: "Pull evidence from sources via adapters.\n"},
        ),
        _fails(
            "config-does-not-disclose-the-missing-connector",
            f"{CONFIG} models pull jobs",
            {CONFIG: "# Exports are pushed to object storage.\n"},
        ),
        _fails(
            "config-models-push-as-pull",
            f"{CONFIG} models an otel-genai endpoint",
            {CONFIG: "# No source connector yet.\n"},
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = _tree(Path(tmp), {})
        (root / COMMAND_PAGE).unlink()
        missing = any(
            f"{COMMAND_PAGE} is missing" in p for p in problems_for(root, False)
        )
    print(f"self-test missing-page: {'ok' if missing else 'FAIL'}")
    results.append(missing)
    ok = all(results)
    print("collect_check self-test:", "ok" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if argv:
        print(__doc__)
        return 2
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
