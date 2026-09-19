"""Engine quickstart docs check: the commands a per-engine quickstart prescribes are commands that engine runs.

The TypeScript and Java engines implement the ``conformance run`` command. A quickstart page for either
engine that tells a reader to run a command the engine does not implement sends that reader straight to a
``cli.not_implemented`` error. This check keeps the two pages honest by running the real engine CLI, not by
reading source:

* every reference-CLI command name the page shows in code a reader would run, or after ``agentce``, is
  invoked on the engine's own CLI, and the run fails if any of them answers ``cli.not_implemented``. The
  vocabulary of command names comes from the reference engine's real parser, and the code is read as a
  bag of words, so a command written with a line continuation, through ``npx``, as a Gradle ``--args``
  string, or inside a list item's indented block is found the same way as a plain one;
* ``conformance run`` is then executed over the simulated corpus, and the run fails unless the engine
  claims ``full`` and wrote one non-empty ``assertions.json`` for every project it reports.

    cd conformance && uv run python engine_docs_check.py --engines typescript,java

The Java launcher must already be built (``./gradlew installDist`` in ``engines/java``); a missing launcher
or toolchain is a setup failure (exit 2), never a pass. Exit 0 = the docs match the engines, 1 = they do
not. Offline, deterministic, no learned component.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
NOT_IMPLEMENTED_KEY = "cli.not_implemented"
# A command the page tells a reader to run in prose: `agentce <command>` or `agentce.js <command>`.
PRESCRIBED = re.compile(r"\bagentce(?:\.js)?[ \t]+([a-z][a-z-]*)")
# A fence line at any indent (a list item's code block is indented), its marker, and the separators
# that split a shell line into words (continuations, quotes, ``--args=...`` assignments, operators).
FENCE_LINE = re.compile(r"^[ \t]*(?P<marker>`{3,}|~{3,})(?P<rest>.*)$")
INDENTED_CODE = re.compile(r"^(?: {4}|\t)")
# Blockquote markers in front of a line (a fenced block inside a quoted note).
QUOTE_PREFIX = re.compile(r"^(?:[ \t]*>)+ ?")
# An inline code span with more than one word: an instruction such as `npx @scope/cli assess`.
INLINE_SPAN = re.compile(r"`([^`\n]*\s[^`\n]*)`")
WORD_SPLIT = re.compile(r"""[\s\\"'`=;&|()<>]+""")
SKIPPED_VERBS = {"version"}
RUN_TIMEOUT_S = 300


@dataclass(frozen=True)
class Engine:
    name: str
    doc: Path
    cwd: Path
    launcher: tuple[str, ...]
    setup_hint: str


ENGINES: dict[str, Engine] = {
    "typescript": Engine(
        name="typescript",
        doc=REPO_ROOT / "docs" / "quickstart-typescript.md",
        cwd=REPO_ROOT / "engines" / "typescript",
        launcher=("node", "bin/agentce.js"),
        setup_hint="run `pnpm install --frozen-lockfile` and `pnpm build` in engines/typescript",
    ),
    "java": Engine(
        name="java",
        doc=REPO_ROOT / "docs" / "quickstart-java.md",
        cwd=REPO_ROOT / "engines" / "java",
        launcher=(str(REPO_ROOT / "engines/java/build/install/agentce/bin/agentce"),),
        setup_hint="run `./gradlew installDist` in engines/java",
    ),
}


class SetupError(Exception):
    """The engine CLI could not be run at all, so nothing can be concluded about the docs."""


def reference_commands() -> frozenset[str]:
    """The command names of the reference engine's real command-line parser."""
    from agentce.cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return frozenset(action.choices)
    raise SetupError("the reference engine's parser declares no commands.")


def code_text(doc: str) -> str:
    """The parts of a page a reader would copy and run: fenced blocks at any indent and fence length,
    indented code, and multi-word inline code spans. An unclosed fence runs to the end of the page, so
    a broken fence fails closed instead of hiding what follows it."""
    lines: list[str] = []
    fence: tuple[str, int] | None = None
    for raw in doc.splitlines():
        line = QUOTE_PREFIX.sub("", raw)
        m = FENCE_LINE.match(line)
        if fence is not None:
            closes = (
                m is not None
                and m["marker"][0] == fence[0]
                and len(m["marker"]) >= fence[1]
                and not m["rest"].strip()
            )
            if closes:
                fence = None
            else:
                lines.append(line)
        elif m:
            fence = (m["marker"][0], len(m["marker"]))
        elif INDENTED_CODE.match(line):
            lines.append(line)
        else:
            lines.extend(span.group(1) for span in INLINE_SPAN.finditer(line))
    return "\n".join(lines)


def prescribed_commands(doc: str, known: frozenset[str]) -> list[str]:
    """The distinct reference-command names a page tells a reader to run, in a stable order.

    A word counts when it appears in code a reader would run (see ``code_text``) or directly after
    ``agentce``; a command name written any other way in prose (a list of planned commands, say) is a
    mention, not an instruction. A verb assembled at run time, such as from a shell variable, and a
    command inside a raw HTML ``<pre>`` block are not seen: the check guards documentation drift on
    plain Markdown pages, not a page written to evade it.
    """
    words = {m.group(1) for m in PRESCRIBED.finditer(doc)}
    words.update(WORD_SPLIT.split(code_text(doc)))
    return sorted((words & known) - SKIPPED_VERBS)


def _envelope(stdout: str) -> dict[str, Any] | None:
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        parsed = json.loads(stdout[start:])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _run(engine: Engine, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [*engine.launcher, *args],
            cwd=engine.cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=RUN_TIMEOUT_S,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise SetupError(
            f"{engine.name}: cannot run the CLI ({exc}); {engine.setup_hint}."
        ) from exc


def rejected_commands(engine: Engine, commands: list[str]) -> list[str]:
    """The prescribed commands the engine's real CLI rejects with ``cli.not_implemented``."""
    if not commands:
        return []
    # A bare `conformance` answers with a JSON envelope on a working CLI, so no envelope means the CLI
    # (its build, its toolchain) is not running here, which is a setup failure and not a finding.
    probe = _run(engine, ["conformance", "--json"])
    if _envelope(probe.stdout) is None:
        raise SetupError(
            f"{engine.name}: the CLI did not start (exit {probe.returncode}); {engine.setup_hint}."
        )
    missing = []
    for command in commands:
        proc = _run(engine, [command, "--json"])
        envelope = _envelope(proc.stdout)
        error = envelope.get("error") if envelope else None
        key = error.get("message_key") if isinstance(error, dict) else None
        if key == NOT_IMPLEMENTED_KEY or NOT_IMPLEMENTED_KEY in proc.stdout:
            missing.append(command)
    return missing


def conformance_run_problems(engine: Engine, corpus: Path, out: Path) -> list[str]:
    """Run ``conformance run`` and report what is wrong with its output; empty when it is sound."""
    proc = _run(
        engine,
        [
            "conformance",
            "run",
            "--engine",
            ".",
            "--corpus",
            str(corpus),
            "--out",
            str(out),
            "--json",
        ],
    )
    envelope = _envelope(proc.stdout)
    if envelope is None:
        return [
            f"{engine.name}: `conformance run` printed no JSON envelope (exit {proc.returncode})."
        ]
    problems: list[str] = []
    if envelope.get("claim") != "full":
        problems.append(
            f"{engine.name}: `conformance run` claim is {envelope.get('claim')!r}, not 'full'."
        )
    projects = envelope.get("projects")
    total = projects.get("total") if isinstance(projects, dict) else None
    written = sorted((out / "projects").rglob("assertions.json"))
    if not isinstance(total, int) or total <= 0:
        problems.append(f"{engine.name}: `conformance run` reported no projects.")
    elif len(written) != total:
        problems.append(
            f"{engine.name}: {total} projects reported but {len(written)} assertions.json files written."
        )
    empty = [p for p in written if p.stat().st_size == 0]
    if empty:
        problems.append(f"{engine.name}: {len(empty)} assertions.json files are empty.")
    return problems


def check_engine(engine: Engine, corpus: Path, known: frozenset[str]) -> list[str]:
    """Every problem with one engine's quickstart page, empty when the page matches the CLI."""
    if not engine.doc.is_file():
        return [f"{engine.name}: quickstart page {engine.doc} does not exist."]
    commands = prescribed_commands(engine.doc.read_text(encoding="utf-8"), known)
    if not commands:
        return [f"{engine.name}: the quickstart page prescribes no command to run."]
    problems = [
        f"{engine.name}: {engine.doc.name} tells a reader to run `{c}`, which the {engine.name} CLI "
        f"answers with {NOT_IMPLEMENTED_KEY}."
        for c in rejected_commands(engine, commands)
    ]
    with tempfile.TemporaryDirectory(prefix="engine-docs-") as tmp:
        problems += conformance_run_problems(engine, corpus, Path(tmp))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="engine_docs_check", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--engines", default="typescript,java", help="comma-separated engines to check"
    )
    parser.add_argument(
        "--corpus", type=Path, default=REPO_ROOT / "corpus", help="the corpus directory"
    )
    parser.add_argument(
        "--doc",
        type=Path,
        help="check this page instead of the engine's quickstart page (one engine only); "
        "how a seeded fault is shown to be caught",
    )
    args = parser.parse_args(argv)

    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    unknown = [n for n in names if n not in ENGINES]
    if args.doc is not None and len(names) != 1:
        print("--doc needs exactly one engine in --engines.", file=sys.stderr)
        return 2
    if unknown or not names:
        print(
            f"unsupported engine(s) {unknown or names}; expected any of {', '.join(ENGINES)}.",
            file=sys.stderr,
        )
        return 2

    try:
        known = reference_commands()
    except SetupError as exc:
        print(f"SETUP: {exc}", file=sys.stderr)
        return 2
    failed = False
    for name in names:
        try:
            engine = ENGINES[name]
            if args.doc is not None:
                engine = replace(engine, doc=args.doc)
            problems = check_engine(engine, args.corpus, known)
        except SetupError as exc:
            print(f"SETUP: {exc}", file=sys.stderr)
            return 2
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        failed = failed or bool(problems)
        if not problems:
            print(f"ok: {name} quickstart matches the CLI")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
