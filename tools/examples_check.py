#!/usr/bin/env python3
"""examples_check - keep the framework examples and the integration docs honest about what is built.

The six ``examples/<style>/agent.py`` scripts are named for an implementation style (LangGraph, the
OpenAI Agents SDK, CrewAI, Google ADK, the Claude Agent SDK, a hand-rolled loop). A script that shares one
framework-free template and imports none of them proves nothing about that framework, and a page that lets
a reader think ``agentce_emit.auto()`` captures a framework's evidence by itself is a claim the code cannot
back. This check ties both to the real behaviour:

1. It runs the real ``agentce_emit.auto()`` with the emitter switched on and makes no ``emit_*`` call. While
   that captures no events, automatic framework capture is not built.
2. While it is not built, every example either imports a real third-party package of its own or says
   ``roadmap`` in its own source or README (never merely somewhere else in the docs), and every page that
   explains the one-line setup says ``roadmap`` too.
3. If ``auto()`` ever does capture events on its own, the check fails and names the wording to retire, so
   the claim is widened deliberately together with its check and never by drift.

    examples_check.py              check the repository (the invocation the CI job uses)
    examples_check.py --self-test  prove the checker discriminates: a good tree passes, and each bad tree
                                   fails for exactly the rule it targets

It imports ``agentce_emit`` to run the probe, so run it in that package's environment
(``uv run --project engines/python-emit --frozen python tools/examples_check.py``). Standard library
only otherwise; no network, no learned component.
"""

from __future__ import annotations

import ast
import importlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLES = (
    "langgraph",
    "crewai",
    "openai-agents",
    "google-adk",
    "claude-agent-sdk",
    "custom-loop",
)
# Modules an example may import without that counting as integrating a framework.
LOCAL_MODULES = {"__future__", "agentce_emit", "scenario"}
# Pages that explain the one-line setup and so must not let it read as framework capture.
LABELLED_PAGES = (
    "docs/integrate.md",
    "engines/python-emit/README.md",
    "website/src/content/docs/docs/ci-integration.md",
)
LABEL = "roadmap"


def third_party_imports(path: Path) -> set[str]:
    """Top-level names an example imports that are neither the standard library nor its own helpers."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots - LOCAL_MODULES - set(sys.stdlib_module_names)


def auto_captures_events() -> int:
    """How many events ``agentce_emit.auto()`` records when nothing but ``auto()`` is called."""
    # Imported by name at call time: the tools environment does not carry the emitter, and the
    # self-test's fixtures never need it.
    agentce_emit = importlib.import_module("agentce_emit")

    saved = os.environ.get("AGENTCE_EMIT")
    os.environ["AGENTCE_EMIT"] = "1"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            emitter = agentce_emit.auto(out=str(Path(tmp) / "bundle"))
            captured = len(emitter.events)
            emitter.flush()
            return captured
    finally:
        if saved is None:
            os.environ.pop("AGENTCE_EMIT", None)
        else:
            os.environ["AGENTCE_EMIT"] = saved


def problems_for(root: Path, captured: int) -> list[str]:
    """Every way ``root`` states more than ``captured`` events of automatic capture can back."""
    if captured:
        return [
            f"agentce_emit.auto() captured {captured} event(s) with no emit_* call: automatic capture "
            "now exists, so retire the roadmap wording and flip its claim in the register together "
            "with a check that proves it"
        ]
    problems: list[str] = []
    for style in STYLES:
        agent = root / "examples" / style / "agent.py"
        if not agent.is_file():
            problems.append(f"examples/{style}/agent.py is missing")
            continue
        if third_party_imports(agent):
            continue
        readme = root / "examples" / style / "README.md"
        own_text = agent.read_text(encoding="utf-8")
        if readme.is_file():
            own_text += readme.read_text(encoding="utf-8")
        if LABEL not in own_text.lower():
            problems.append(
                f"examples/{style}/agent.py imports no framework of its own and does not say "
                f"'{LABEL}' in its source or README"
            )
    for page in LABELLED_PAGES:
        path = root / page
        if not path.is_file():
            problems.append(f"{page} is missing")
        elif LABEL not in path.read_text(encoding="utf-8").lower():
            problems.append(
                f"{page} explains the one-line setup but does not say the automatic framework capture "
                f"is on the '{LABEL}'"
            )
    return problems


def check() -> int:
    captured = auto_captures_events()
    problems = problems_for(ROOT, captured)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(
        "examples_check: ok — automatic capture is not built and every example and page says so "
        f"({len(STYLES)} examples, {len(LABELLED_PAGES)} pages)"
    )
    return 0


# --- self-test ---------------------------------------------------------------------------------------

LABELLED_AGENT = '"""Scripted example. Framework capture is on the roadmap."""\nimport agentce_emit\n'
UNLABELLED_AGENT = (
    '"""Scripted example."""\nimport agentce_emit\nfrom scenario import run\n'
)
FRAMEWORK_AGENT = '"""Runs the framework."""\nimport langgraph\nimport agentce_emit\n'
STDLIB_ONLY_AGENT = (
    '"""Scripted example."""\nimport json\nimport os\nimport agentce_emit\n'
)


def _tree(
    root: Path,
    *,
    agents: dict[str, str] | None = None,
    pages: dict[str, str] | None = None,
) -> Path:
    for style in STYLES:
        agent = root / "examples" / style / "agent.py"
        agent.parent.mkdir(parents=True)
        agent.write_text((agents or {}).get(style, LABELLED_AGENT), encoding="utf-8")
    for page in LABELLED_PAGES:
        path = root / page
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            (pages or {}).get(page, "Automatic capture is on the roadmap.\n"),
            encoding="utf-8",
        )
    return root


def _fails(name: str, build: dict[str, object], needle: str, captured: int = 0) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        found = problems_for(_tree(Path(tmp), **build), captured)  # type: ignore[arg-type]
    ok = any(needle in p for p in found)
    print(f"self-test {name}: {'ok' if ok else 'FAIL'}")
    return ok


def self_test() -> int:
    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp:
        clean = problems_for(_tree(Path(tmp)), 0)
    good = not clean
    print(f"self-test good-tree: {'ok' if good else 'FAIL ' + str(clean)}")
    results.append(good)
    with tempfile.TemporaryDirectory() as tmp:
        framework = problems_for(
            _tree(Path(tmp), agents={s: FRAMEWORK_AGENT for s in STYLES}, pages={}),
            0,
        )
    passes = not framework
    print(f"self-test framework-import-passes: {'ok' if passes else 'FAIL'}")
    results.append(passes)
    results.append(
        _fails(
            "unlabelled-example",
            {"agents": {"crewai": UNLABELLED_AGENT}},
            "examples/crewai/agent.py imports no framework",
        )
    )
    results.append(
        _fails(
            "stdlib-import-is-not-a-framework",
            {"agents": {"langgraph": STDLIB_ONLY_AGENT}},
            "examples/langgraph/agent.py imports no framework",
        )
    )
    results.append(
        _fails(
            "page-without-label",
            {"pages": {"docs/integrate.md": "Wire it in with one line.\n"}},
            "docs/integrate.md explains the one-line setup",
        )
    )
    results.append(
        _fails(
            "label-elsewhere-does-not-count",
            {
                "agents": {"google-adk": UNLABELLED_AGENT},
                "pages": {"docs/integrate.md": "This is on the roadmap.\n"},
            },
            "examples/google-adk/agent.py imports no framework",
        )
    )
    results.append(_fails("auto-now-captures", {}, "now exists", captured=3))
    with tempfile.TemporaryDirectory() as tmp:
        root = _tree(Path(tmp))
        (root / "examples" / "custom-loop" / "agent.py").unlink()
        missing = any(
            "custom-loop/agent.py is missing" in p for p in problems_for(root, 0)
        )
    print(f"self-test missing-example: {'ok' if missing else 'FAIL'}")
    results.append(missing)
    ok = all(results)
    print("examples_check self-test:", "ok" if ok else "FAILED")
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
