"""argv_grammar - hold every engine's command line to the one grammar table (SPEC 8.5, item 18.184w).

`spec/cli/argv-grammar.json` is written by hand and is the source; each engine vendors it byte for
byte and loads it as data. `--check` fails when:

* the table does not validate against `spec/cli/argv-grammar.schema.json`;
* an engine's vendored copy differs from it by a byte;
* Python's argparse parser, kept only to render `--help` (so the generated command reference stays
  unchanged), declares a command, action, option spelling, kind or repeat bit the table does not, or
  the table one the parser does not;
* a TypeScript or Java command-line source names an option spelling outside the table, or keeps a
  hand-written option table (a `*_GRAMMAR` literal, `parseVerifyArgs`, `parseReadinessArgs`,
  `parseSignArgs`), the shape every port had before the table.

The schema and parser checks run under the Python engine's own environment (`uv run --project
engines/python`), since this script's `tools` environment installs neither `agentce` nor `jsonschema`.

Usage:
    argv_grammar.py --check
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAMMAR = ROOT / "spec" / "cli" / "argv-grammar.json"
SCHEMA = ROOT / "spec" / "cli" / "argv-grammar.schema.json"
COPIES = (
    ROOT / "engines" / "python" / "agentce" / "data" / "cli" / "argv-grammar.json",
    ROOT / "engines" / "typescript" / "data" / "cli" / "argv-grammar.json",
    ROOT / "engines" / "java" / "src" / "main" / "resources" / "cli" / "argv-grammar.json",
)
#: The sources that read a command line. Other files may name an option of a command they run
#: (conformance passes `--set` to the engine under test), which is not a grammar of their own.
CLI_SOURCES = (
    ROOT / "engines" / "typescript" / "src" / "cli.ts",
    ROOT / "engines" / "typescript" / "src" / "argv.ts",
    ROOT / "engines" / "java" / "src" / "main" / "java" / "org" / "agentce" / "Cli.java",
    ROOT / "engines" / "java" / "src" / "main" / "java" / "org" / "agentce" / "Argv.java",
)
HAND_TABLE = re.compile(r"\b[A-Z_]+_GRAMMAR\b|\bparse(?:Verify|Readiness|Sign)Args\b")
SPELLING = re.compile(r'"(--?[A-Za-z][A-Za-z0-9-]*)"')


def spellings(grammar: dict) -> set[str]:
    """Every option spelling the table declares, globals and top-level ones included."""
    names = {n for o in grammar["globals"] for n in o["names"]}
    names |= set(grammar["top"]["help"]) | set(grammar["top"]["version"])

    def walk(level: dict) -> None:
        for option in level["options"]:
            names.update(option["names"])
        for action in level.get("actions", {}).values():
            walk(action)

    for command in grammar["commands"].values():
        walk(command)
    return names


def check_sources(grammar: dict, sources: tuple[Path, ...] = CLI_SOURCES) -> list[str]:
    known = spellings(grammar)
    problems = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        for match in HAND_TABLE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"{rel}:{line}: hand-written option table '{match.group(0)}'")
        for match in SPELLING.finditer(text):
            if match.group(1) not in known:
                line = text.count("\n", 0, match.start()) + 1
                problems.append(
                    f"{rel}:{line}: option '{match.group(1)}' is not in {GRAMMAR.relative_to(ROOT)}"
                )
    return problems


def check_copies() -> list[str]:
    source = GRAMMAR.read_bytes()
    return [
        f"{copy.relative_to(ROOT)} differs from {GRAMMAR.relative_to(ROOT)}"
        for copy in COPIES
        if not copy.is_file() or copy.read_bytes() != source
    ]


def python_side() -> list[str]:
    """Run inside the Python engine's environment: the schema, and argparse against the table."""
    import argparse

    import jsonschema

    from agentce.cli import build_parser

    grammar = json.loads(GRAMMAR.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    problems = [
        f"{GRAMMAR.relative_to(ROOT)}: {'/'.join(map(str, e.absolute_path))}: {e.message}"
        for e in jsonschema.Draft202012Validator(schema).iter_errors(grammar)
    ]
    global_names = {n for o in grammar["globals"] for n in o["names"]}

    def parser_level(parser: argparse.ArgumentParser) -> tuple[dict, dict]:
        options, actions = {}, {}
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                actions = {n: parser_level(p) for n, p in action.choices.items()}
            elif (
                action.option_strings
                and not isinstance(action, argparse._HelpAction)
                and not set(action.option_strings) & global_names
            ):
                kind = "flag" if action.nargs == 0 else "value"
                repeatable = isinstance(action, argparse._AppendAction)
                options[tuple(action.option_strings)] = (kind, repeatable)
        return options, actions

    def table_level(level: dict) -> tuple[dict, dict]:
        options = {tuple(o["names"]): (o["kind"], o["repeatable"]) for o in level["options"]}
        actions = {n: table_level(a) for n, a in level.get("actions", {}).items()}
        return options, actions

    def compare(where: str, parsed: tuple[dict, dict], table: tuple[dict, dict]) -> None:
        for names in sorted(set(parsed[0]) | set(table[0])):
            got, want = parsed[0].get(names), table[0].get(names)
            if got != want:
                problems.append(
                    f"{where} {'/'.join(names)}: argparse declares {got}, the table {want}"
                )
        for name in sorted(set(parsed[1]) | set(table[1])):
            if name not in parsed[1] or name not in table[1]:
                side = "argparse" if name in parsed[1] else "the table"
                problems.append(f"{where} {name}: only {side} has this command or action")
            else:
                compare(f"{where} {name}", parsed[1][name], table[1][name])

    top = parser_level(build_parser())
    commands = {n: table_level(c) for n, c in grammar["commands"].items()}
    compare("agentce", ({}, top[1]), ({}, commands))
    return problems


def check() -> int:
    grammar = json.loads(GRAMMAR.read_text(encoding="utf-8"))
    problems = check_copies() + check_sources(grammar)
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    python = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(ROOT / "engines" / "python"),
            "--frozen",
            "python",
            __file__,
            "--python-side",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if python.returncode not in (0, 1):
        problems.append(f"the Python engine's check did not run: {python.stderr.strip()[-400:]}")
    problems += [line for line in python.stdout.splitlines() if line]
    for problem in problems:
        print(f"FAIL {problem}")
    print(
        f"{'FAIL' if problems else 'OK'} argv_grammar: {GRAMMAR.relative_to(ROOT)}, "
        f"{len(COPIES)} vendored copies, Python's help parser and {len(CLI_SOURCES)} TypeScript "
        f"and Java sources, {len(problems)} problem(s)"
    )
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    if argv == ["--check"]:
        return check()
    if argv == ["--python-side"]:
        problems = python_side()
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    print("usage: argv_grammar.py --check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
