#!/usr/bin/env python3
"""framework_examples_check - prove the five framework examples really run their framework.

Five of the six ``examples/<style>/`` agents (LangGraph, the OpenAI Agents SDK, CrewAI, Google ADK, the
Claude Agent SDK) are meant to really import and run their named framework end to end, offline and
keyless, against a scripted deterministic model or transport, each in its own isolated environment. This
check proves both halves of that:

1. **The example really runs.** For each of the five, ``examples/<style>/agent.py`` imports its named
   framework at the top level (never falling back to a "roadmap" label -- that escape hatch is for the
   truth-stage check, ``examples_check.py``, before the build existed), and running the example for real
   through its own ``run.sh`` produces a bundle the engine ingests with zero quarantines and at least the
   ``ModelCall``/``ToolCall``/``Decision``/``SessionStart``/``SessionEnd`` event types -- so a script that
   imports a framework and then does nothing with it cannot pass.
2. **The dependency boundary holds with the frameworks actually installed.** At least one of the five
   examples' own lockfiles resolves a package on the no-ml denylist (several of these frameworks require
   an LLM/embedding client of their own), yet the repository-wide ``no_ml_check.py`` scan still exits 0,
   because the exemption (``docs/adr/0016``) names exactly those five lockfiles and nothing else.

    framework_examples_check.py                 run both checks (the invocation the CI job uses)
    framework_examples_check.py --examples-only run only the real-run/validate check
    framework_examples_check.py --no-ml-only    run only the dependency-boundary check
    framework_examples_check.py --self-test     prove each check discriminates on a synthetic tree

Run it in the engine's environment (``uv run --project engines/python --frozen python
tools/framework_examples_check.py``): it imports ``agentce`` to validate each bundle, and imports
``no_ml_check`` from this same directory. No network beyond what each example's own scripted, offline
model/transport already guarantees; no learned component in this script itself.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_ml_check import DENYLIST, FRAMEWORK_EXAMPLE_LOCKS, load_denylist, packages_in_uv_lock  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: Style -> the top-level module its agent.py must really import (SPEC 13.4 AX-3/AX-4).
STYLES = {
    "langgraph": "langgraph",
    "crewai": "crewai",
    "openai-agents": "agents",
    "google-adk": "google",
    "claude-agent-sdk": "claude_agent_sdk",
}
REQUIRED_EVENT_TYPES = {"SessionStart", "ModelCall", "ToolCall", "Decision", "SessionEnd"}

#: The exact set docs/adr/0016 names, declared independently of no_ml_check.FRAMEWORK_EXAMPLE_LOCKS so a
#: silent widening of that constant (to smuggle in an unrelated, e.g. engine-tree, path) is itself a
#: finding, not something this check would wave through by re-reading the same value it is grading.
ADR_0016_EXEMPT_LOCKS = frozenset(
    {
        "examples/langgraph/uv.lock",
        "examples/crewai/uv.lock",
        "examples/openai-agents/uv.lock",
        "examples/google-adk/uv.lock",
        "examples/claude-agent-sdk/uv.lock",
    }
)


def top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def check_examples_really_run(root: Path, styles: dict[str, str]) -> list[str]:
    """Every way one of ``styles`` fails to really import and run its framework under ``root``."""
    problems: list[str] = []
    for style, top_module in styles.items():
        example_dir = root / "examples" / style
        agent_py = example_dir / "agent.py"
        if not agent_py.is_file():
            problems.append(f"{style}: agent.py missing")
            continue
        if top_module not in top_level_imports(agent_py):
            problems.append(f"{style}: agent.py does not import {top_module!r}")
            continue
        run_sh = example_dir / "run.sh"
        if not run_sh.is_file():
            problems.append(f"{style}: run.sh missing")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            proc = subprocess.run(
                [str(run_sh), str(bundle)],
                capture_output=True,
                text=True,
                cwd=str(example_dir),
                check=False,
            )
            if proc.returncode != 0 or not (bundle / "manifest.json").is_file():
                problems.append(
                    f"{style}: run.sh failed (exit {proc.returncode}): "
                    + (proc.stderr or proc.stdout).strip()[-800:]
                )
                continue
            # Deferred, dynamic import: the tools/ environment does not carry the engine.
            agentce_bundle = importlib.import_module("agentce.bundle")
            agentce_ingest = importlib.import_module("agentce.ingest")
            ingested = agentce_ingest.ingest(agentce_bundle.load_bundle(bundle))
            if ingested.quarantined:
                problems.append(f"{style}: quarantined events: {ingested.quarantined}")
                continue
            types = {str(e["data"]["@type"]) for e in ingested.accepted}
            missing = REQUIRED_EVENT_TYPES - types
            if missing:
                problems.append(f"{style}: missing required event types {missing} (got {sorted(types)})")
    return problems


def check_no_ml_boundary_holds(
    root: Path, denylist_path: Path, exempt: frozenset[str], *, expected_exempt: frozenset[str]
) -> list[str]:
    """The repo-wide no-ml scan stays clean with real framework deps installed, the exemption is doing
    real work (at least one exempted lockfile actually resolves a denylisted package), and the
    exemption is exactly the five named paths docs/adr/0016 documents -- never a wider list an
    implementer could append an unrelated (e.g. engine-tree) path to."""
    if exempt != expected_exempt:
        return [
            f"FRAMEWORK_EXAMPLE_LOCKS ({sorted(exempt)}) no longer matches the exact set docs/adr/0016 "
            f"names ({sorted(expected_exempt)}) -- widening or narrowing it needs its own reviewed ADR "
            "change, never a silent edit"
        ]
    deny = load_denylist(denylist_path)
    found: list[tuple[str, list[str]]] = []
    for rel in sorted(exempt):
        lock = root / rel
        if not lock.is_file():
            continue
        hit = sorted(packages_in_uv_lock(lock) & deny)
        if hit:
            found.append((rel, hit))
    if not found:
        return [
            "no framework-example lockfile currently resolves a denylisted package -- the exemption "
            "would be untested"
        ]
    result = subprocess.run(
        [sys.executable, str(root / "tools" / "no_ml_check.py"), str(root)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return [
            "repo-wide no_ml_check.py failed with real framework deps installed:\n"
            + result.stdout
            + result.stderr
        ]
    return []


def check(*, examples: bool = True, no_ml: bool = True) -> int:
    problems: list[str] = []
    if examples:
        problems += check_examples_really_run(ROOT, STYLES)
    if no_ml:
        problems += check_no_ml_boundary_holds(
            ROOT, DENYLIST, FRAMEWORK_EXAMPLE_LOCKS, expected_exempt=ADR_0016_EXEMPT_LOCKS
        )
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(
        f"framework_examples_check: ok — all {len(STYLES)} framework examples really run their "
        "framework and validate; the no-ml boundary holds with real dependencies installed"
    )
    return 0


# --- self-test ---------------------------------------------------------------------------------------

_REAL_AGENT = (
    '"""Runs the framework."""\nimport {module}\n\nif __name__ == "__main__":\n    pass\n'
)
_UNREAL_AGENT = '"""Stub."""\nimport agentce_emit\n'


def _write_style_tree(root: Path, styles: dict[str, str], *, real: bool) -> None:
    for style, module in styles.items():
        d = root / "examples" / style
        d.mkdir(parents=True)
        agent = _REAL_AGENT.format(module=module) if real else _UNREAL_AGENT
        (d / "agent.py").write_text(agent, encoding="utf-8")


def self_test() -> int:
    results: list[bool] = []

    # check_examples_really_run: a style whose agent.py never imports its named framework fails, named.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_style_tree(root, {"langgraph": "langgraph"}, real=False)
        problems = check_examples_really_run(root, {"langgraph": "langgraph"})
    ok = any("does not import" in p for p in problems)
    print(f"self-test unreal-import-fails: {'ok' if ok else 'FAIL ' + str(problems)}")
    results.append(ok)

    # A missing agent.py is reported by name, not a crash.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "examples" / "crewai").mkdir(parents=True)
        problems = check_examples_really_run(root, {"crewai": "crewai"})
    ok = any("agent.py missing" in p for p in problems)
    print(f"self-test missing-agent-fails: {'ok' if ok else 'FAIL ' + str(problems)}")
    results.append(ok)

    # check_no_ml_boundary_holds: no exempted lockfile resolves anything denylisted -> flagged untested.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "examples" / "langgraph").mkdir(parents=True)
        (root / "examples" / "langgraph" / "uv.lock").write_text(
            'version = 1\nrevision = 2\nrequires-python = ">=3.12"\n\n'
            '[[package]]\nname = "click"\nversion = "8.0"\nsource = { registry = "https://pypi.org/simple" }\n',
            encoding="utf-8",
        )
        problems = check_no_ml_boundary_holds(
            root,
            DENYLIST,
            frozenset({"examples/langgraph/uv.lock"}),
            expected_exempt=frozenset({"examples/langgraph/uv.lock"}),
        )
    ok = any("untested" in p for p in problems)
    print(f"self-test exemption-untested-is-flagged: {'ok' if ok else 'FAIL ' + str(problems)}")
    results.append(ok)

    # A real denylisted package inside the exempted lockfile, with the real (clean) repo behind it,
    # passes -- proving the happy path this check exists to certify.
    ok = not check_no_ml_boundary_holds(
        ROOT, DENYLIST, FRAMEWORK_EXAMPLE_LOCKS, expected_exempt=ADR_0016_EXEMPT_LOCKS
    )
    print(f"self-test real-repo-boundary-holds: {'ok' if ok else 'FAIL'}")
    results.append(ok)

    # A widened exemption (an unrelated path appended, e.g. to hide a real engine-tree violation) is
    # itself caught, independent of whether the appended path happens to carry a denylisted package.
    widened = FRAMEWORK_EXAMPLE_LOCKS | {"engines/python/uv.lock"}
    problems = check_no_ml_boundary_holds(
        ROOT, DENYLIST, widened, expected_exempt=ADR_0016_EXEMPT_LOCKS
    )
    ok = any("no longer matches the exact set" in p for p in problems)
    print(f"self-test widened-exemption-is-caught: {'ok' if ok else 'FAIL ' + str(problems)}")
    results.append(ok)

    passed = all(results)
    print("framework_examples_check self-test:", "ok" if passed else "FAILED")
    return 0 if passed else 1


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if argv == ["--examples-only"]:
        return check(no_ml=False)
    if argv == ["--no-ml-only"]:
        return check(examples=False)
    if argv:
        print(__doc__)
        return 2
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
