"""VG-UX-COPY (18.40b, USER_EXPERIENCE.md R6): the copy rules over every user-facing string in scope.

R6: say records; never prove, performance, automagically or any format; no em dashes. The word
list and the dash are lexical, so this gate holds them; "say records", "name what the product does"
and "numbers over adjectives" are held by review. SCOPE names each source of reader-visible text and
how to pull that text out:

- `markdown`: tracked files, minus fenced blocks, inline code, HTML comments, link targets, URLs and
  front matter keys other than `title` and `description`;
- `catalogue`: every string value of the English message catalogue (the engines' vendored copies are
  held equal to it by their sync tests);
- `cli`: what the three engines print for `--help`, each command's `--help`, `quickstart` and
  `assess <trace folder>`. Every run has an expected exit code and marker, so a crash or a skipped
  run fails (`ux.copy.run_failed`) instead of reading as clean.

Text is read a paragraph at a time with its whitespace folded and HTML entities decoded, so a phrase
split over a line break, or written `&mdash;`, still matches.

SWEEP holds the site's short descriptions, llms.txt and the website package text to the approved
tagline (imported from tools/locked_copy_check.py), and keeps the pre-lock and retired lines out of
those files. An extractor that no longer matches fails (`ux.copy.sweep_unreadable`), never skips.

Stdout ends with `UX-COPY OK` when every check holds; each failure is one stderr line led by its
stable key, `ux.copy.<rule>: <file>:<line>: <excerpt>; <what to write instead>`. `--self-test` runs
the extractors, the rules and the run checker over built-in texts.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from locked_copy_check import RETIRED, TAGLINE  # type: ignore[import-not-found]  # noqa: E402

TRACE_FIXTURE = (
    ROOT / "adapters/otel-genai/fixtures/otel-genai-agent-session/input.json"
)
CATALOGUE = "spec/i18n/messages.en.json"
# (source, kind, git pathspecs). New file sources (reports, docs, the built site) join this
# table; the `cli` source is the engine runs in cli_leg.
SCOPE: tuple[tuple[str, str, list[str]], ...] = (
    ("readmes", "markdown", ["README.md", "website/README.md", "engines/*/README.md"]),
    ("skills", "markdown", ["skills/**/*.md"]),
    ("catalogue", "catalogue", [CATALOGUE]),
)
# (key, pattern, what to write instead). Every pattern is case-insensitive.
RULES = (
    (
        "em_dash",
        r"[\u2014\u2015\u2e3a\u2e3b\ufe58\ufe31]|(?<=\s)(?:--|\u2013)(?=\s)",
        "use a comma, colon, full stop or parentheses",
    ),
    ("prove", r"\bprov(?:e|es|ed|en|ing)\b", "say show, check or document"),
    ("performance", r"\bperformances?\b", "say behavior, speed or how it runs"),
    ("automagically", r"\bautomagic(?:al|ally)?\b", "say what it does"),
    ("any_format", r"\bany\s+formats?\b", "name the formats"),
)
COMPILED = [
    (key, re.compile(pattern, re.IGNORECASE), fix) for key, pattern, fix in RULES
]

FENCE = re.compile(r"^\s*(```|~~~)")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1", re.DOTALL)
LINK_TARGET = re.compile(r"\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")
URL = re.compile(r"<?\b(?:https?|ftp|mailto):[^\s<>)\]]+>?")
FRONT_KEY = re.compile(r"^(title|description)\s*:\s*(.*)$")
BLOCK = {
    "",
    ">",
    "|",
    ">-",
    "|-",
    ">+",
    "|+",
}  # a YAML value on the lines below its key
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

UNBUILT_MARKER = "not yet implemented"
# The TypeScript and Java commands that print UNBUILT_MARKER for --help (exit 3) at base.
# A command that grows its --help must print usage: then, so this list only shrinks.
UNBUILT = {
    "typescript": {"collect", "ingest", "catalog", "doctor", "init", "config"},
    "java": {"catalog", "collect", "ingest", "doctor", "init", "config"},
}

PRE_LOCK = "A deterministic, model-free, vendor-neutral standard for assessing how AI agents behave"


def blank(match: re.Match[str]) -> str:
    """Replace a match with a space, keeping its line breaks so line numbers hold."""
    return " " + "\n" * match.group().count("\n")


def markdown_lines(text: str) -> list[tuple[int, str]]:
    """(1-based line, reader-visible text) for a Markdown file; skipped lines come back blank."""
    lines = text.split("\n")
    out: list[tuple[int, str]] = []
    front = bool(lines) and lines[0].rstrip() == "---"
    fenced = reading = False
    for number, line in enumerate(lines, start=1):
        if front:
            if number > 1 and line.rstrip() == "---":
                front = False
                out.append((number, ""))
            elif line[
                :1
            ].isspace():  # a continuation line of the key above (`description: >`)
                out.append((number, line if reading else ""))
            else:  # a key: each title or description value is its own paragraph
                key = FRONT_KEY.match(line)
                reading = bool(key)
                value = key.group(2).strip() if key else ""
                out += [
                    (number, ""),
                    (number, "" if value in BLOCK else value.strip("'\"")),
                ]
            continue
        if FENCE.match(line):
            fenced = not fenced
            out.append((number, ""))
        else:
            out.append((number, "" if fenced else line))
    body = "\n".join(text for _, text in out)
    for pattern in (COMMENT, CODE_SPAN, LINK_TARGET, URL):
        body = pattern.sub(blank, body)
    return [(out[i][0], text) for i, text in enumerate(body.split("\n"))]


def plain_lines(text: str) -> list[tuple[int, str]]:
    """(1-based line, text) for catalogue strings and terminal output: code spans and URLs dropped."""
    for pattern in (CODE_SPAN, URL):
        text = pattern.sub(blank, text)
    return list(enumerate(text.split("\n"), start=1))


def scan(where: str, lines: list[tuple[int, str]]) -> list[str]:
    """Every rule hit, a paragraph at a time, with whitespace folded and entities decoded."""
    hits: list[str] = []
    paragraph: list[tuple[int, str]] = []

    def flush() -> None:
        if not paragraph:
            return
        starts, parts, at = [], [], 0
        for number, line in paragraph:
            piece = " ".join(html.unescape(line).split())
            starts.append((at, number))
            parts.append(piece)
            at += len(piece) + 1
        joined = " ".join(parts)
        for key, pattern, fix in COMPILED:
            for match in pattern.finditer(joined):
                at_line = max(n for start, n in starts if start <= match.start())
                excerpt = joined[max(0, match.start() - 30) : match.end() + 30]
                hits.append(f"ux.copy.{key}: {where}:{at_line}: {excerpt!r}; {fix}")
        paragraph.clear()

    for number, line in lines:
        if line.strip():
            paragraph.append((number, line))
        else:
            flush()
    flush()
    return hits


def tracked(pathspecs: list[str]) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *(f":(glob){p}" for p in pathspecs)],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return sorted(p for p in out.split("\0") if p)


def catalogue_lines(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """(key, lines) per catalogue string, each line numbered where its key sits in the file."""
    first_line: dict[str, int] = {}
    for number, line in enumerate(text.split("\n"), 1):
        for key in re.findall(r'"([^"\\]*)"\s*:', line):
            first_line.setdefault(key, number)
    out = []

    def walk(node: object, key: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            at = first_line.get(key, 1)
            out.append((key, [(at, line) for _, line in plain_lines(node)]))

    walk(json.loads(text), "")
    return out


def engine_commands() -> dict[str, list[str]]:
    jars = sorted(
        (ROOT / "engines/java/build/libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    ts = ROOT / "engines/typescript/bin/agentce.js"  # the npm bin a user runs
    if not jars or not ts.is_file():
        raise SystemExit(
            "ux.copy.not_built: source verification/gates/build_engines.sh first"
        )
    return {
        "python": [
            "uv",
            "run",
            "--project",
            str(ROOT / "engines/python"),
            "--frozen",
            "agentce",
        ],
        "typescript": ["node", str(ts)],
        "java": ["java", "-jar", str(jars[-1])],
    }


def run(argv: list[str]) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["NO_COLOR"] = "1"
    done = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=300,
    )
    text = (done.stdout + done.stderr).decode("utf-8", "replace")
    return done.returncode, ANSI.sub("", text)


def run_failed(
    where: str, code: int, text: str, expected: list[tuple[int, str]]
) -> str | None:
    """A failure line unless the run exited with one of the expected codes and printed its marker
    (in any case: Python prints "Verdict:", TypeScript and Java "verdict:")."""
    if any(
        code == want and marker.lower() in text.lower() for want, marker in expected
    ):
        return None
    last = text.rstrip().rsplit("\n", 1)[-1].strip() or "(nothing printed)"
    wanted = " or ".join(f"exit {c} with {m!r}" for c, m in expected)
    return (
        f"ux.copy.run_failed: {where}: exit {code}, last line {last!r}; expected {wanted}. "
        "Fix the command so the gate can read what it prints."
    )


def help_commands(text: str) -> list[str]:
    """The command names Python's --help lists under positional arguments."""
    section = text.split("positional arguments:", 1)[-1].split("\n\n", 1)[0]
    return re.findall(r"^    ([a-z][a-z-]*)\s{2,}", section, re.MULTILINE)


def engine_runs(
    engine: str, names: list[str], tmp: str, traces: Path
) -> list[tuple[str, list[str], list[tuple[int, str]]]]:
    """(label, args, expected outcomes) for every run the gate makes in one engine."""
    runs: list[tuple[str, list[str], list[tuple[int, str]]]] = [
        ("--help", ["--help"], [(0, "usage:")])
    ]
    for name in names:
        expected = [(0, "usage:")]
        if name in UNBUILT.get(engine, set()):
            expected.append((3, UNBUILT_MARKER))
        runs.append((f"{name} --help", [name, "--help"], expected))
    runs.append(
        ("quickstart", ["quickstart", "--out", f"{tmp}/{engine}-qs"], [(0, "Verdict:")])
    )
    assess = [(0, "Verdict:")]
    if engine == "java":  # Java's folder reader is other items' scope
        assess = [(3, "input.records_unsupported")]
    runs.append(
        ("assess", ["assess", str(traces), "--out", f"{tmp}/{engine}-as"], assess)
    )
    return runs


def cli_leg(errors: list[str]) -> None:
    commands = engine_commands()
    python_help = run(commands["python"] + ["--help"])
    names = help_commands(python_help[1])
    if not names:
        errors.append(
            "ux.copy.no_commands: python agentce --help lists no commands under positional "
            "arguments; the gate cannot find what to run."
        )
    with tempfile.TemporaryDirectory() as tmp:
        traces = Path(tmp) / "traces"
        traces.mkdir()
        shutil.copy(TRACE_FIXTURE, traces / "input.json")

        def one_engine(
            engine: str,
        ) -> list[tuple[str, int, str, list[tuple[int, str]]]]:
            done = []
            for label, args, expected in engine_runs(engine, names, tmp, traces):
                reuse = engine == "python" and label == "--help"
                code, text = python_help if reuse else run(commands[engine] + args)
                done.append((label, code, text, expected))
            return done

        # The engines write to their own folders, so they run side by side; results are read
        # back in engine order, so the output does not depend on which finished first.
        with ThreadPoolExecutor(max_workers=len(commands)) as pool:
            results = list(pool.map(one_engine, commands))
    for engine, done in zip(commands, results, strict=True):
        for label, code, text, expected in done:
            where = f"{engine} agentce {label}"
            failed = run_failed(where, code, text, expected)
            if failed:
                errors.append(failed)
                continue
            errors.extend(scan(where, plain_lines(text)))
            print(f"ux-copy: {where}: exit {code}")


def value_of(pattern: str) -> Callable[[str], str | None]:
    def extract(text: str) -> str | None:
        match = re.search(pattern, text, re.MULTILINE)
        return match.group(1) if match else None

    return extract


def package_description(text: str) -> str | None:
    try:
        value = json.loads(text).get("description")
    except ValueError:
        return None
    return value if isinstance(value, str) else None


def readme_opening(text: str) -> str | None:
    """The first paragraph after the H1, folded to one line."""
    body = text.split("\n# ", 1)[-1] if not text.startswith("# ") else text[2:]
    parts = body.split("\n\n")
    return " ".join(parts[1].split()) if len(parts) > 1 else None


def front_description(text: str) -> str | None:
    if not text.startswith("---\n"):
        return None
    front = text[4:].split("\n---", 1)[0]
    match = re.search(r"^description:\s*(.+)$", front, re.MULTILINE)
    return match.group(1).strip().strip("'\"") if match else None


# (file, extractor, whole value or contained). Each value carries the approved tagline.
SWEEP: tuple[tuple[str, Callable[[str], str | None], bool], ...] = (
    (
        "website/src/layouts/BaseLayout.astro",
        value_of(r"^\s*description = '([^']*)',?$"),
        True,
    ),
    ("website/astro.config.mjs", value_of(r"^\s*description:\s*'([^']*)',?$"), True),
    ("website/scripts/build-llms.mjs", value_of(r"^\s*'> ([^']*)',?$"), True),
    ("website/package.json", package_description, True),
    ("website/README.md", readme_opening, False),
    ("website/src/content/docs/docs/concepts.md", front_description, True),
)


def sweep_one(
    rel: str, text: str, extract: Callable[[str], str | None], whole: bool
) -> list[str]:
    errors = []
    folded = " ".join(text.split()).lower()
    for retired in (PRE_LOCK, *RETIRED):
        if retired.lower() in folded:
            errors.append(
                f"ux.copy.sweep_retired: {rel}: still says {retired!r}; write the tagline {TAGLINE!r}"
            )
    value = extract(text)
    if value is None:
        errors.append(
            f"ux.copy.sweep_unreadable: {rel}: the gate cannot find the description it holds; "
            "keep it where the SWEEP extractor reads it, or update the extractor"
        )
    elif (value != TAGLINE) if whole else (TAGLINE not in value):
        errors.append(
            f"ux.copy.sweep_value: {rel}: reads {value!r}; write the tagline {TAGLINE!r}"
        )
    return errors


def check(errors: list[str], with_cli: bool = True) -> None:
    for source, kind, pathspecs in SCOPE:
        files = tracked(pathspecs)
        if not files:
            errors.append(
                f"ux.copy.scope_empty: {source}: no tracked file matches {pathspecs}"
            )
        for rel in files:
            text = (ROOT / rel).read_text(encoding="utf-8")
            if kind == "markdown":
                errors.extend(scan(rel, markdown_lines(text)))
            else:
                for key, lines in catalogue_lines(text):
                    errors.extend(scan(f"{rel} {key}", lines))
        print(f"ux-copy: {source}: {len(files)} file(s)")
    if with_cli:
        cli_leg(errors)
    for rel, extract, whole in SWEEP:
        errors.extend(
            sweep_one(rel, (ROOT / rel).read_text(encoding="utf-8"), extract, whole)
        )
    print(f"ux-copy: sweep: {len(SWEEP)} place(s)")


def keys(lines: list[str]) -> list[str]:
    return [line.split(":", 1)[0].removeprefix("ux.copy.") for line in lines]


def self_test() -> int:
    # (reader, expected keys, texts): each text read by that reader gives exactly those keys.
    table: tuple[
        tuple[Callable[[str], list[tuple[int, str]]], list[str], tuple[str, ...]], ...
    ] = (
        (
            markdown_lines,
            ["prove"],
            ("records prove it", "it proves", "proven", "proving", "it proved"),
        ),
        (
            markdown_lines,
            ["performance"],
            ("Performance", "PERFORMANCES", "good performance"),
        ),
        (
            markdown_lines,
            ["automagically"],
            ("automagic", "automagical", "automagically"),
        ),
        (
            markdown_lines,
            ["em_dash"],
            (
                "a \u2014 b",
                "a\u2014b",
                "a\u2015b",
                "a\u2e3ab",
                "a\ufe58b",
                "a -- b",
                "a \u2013 b",
                "pages 10 \u2013 20",
                "a &mdash; b",
                "a &#8212; b",
                "a &#x2014; b",
                "a\n-- b",
                "---\ndescription: a \u2014 b\n---\nbody",
                "---\nname: x\ndescription: >\n  a fine line\n  a \u2014 b\n---\nbody",
                "---\ndescription: |\n  records\n  \u2014 kept\n---\nbody",
            ),
        ),
        (markdown_lines, ["any_format"], ("any format", "any\nformat", "ANY  formats")),
        (
            markdown_lines,
            [],
            (
                "run with --out ./out",
                "a--b",
                "10\u201320 records",
                "approve the improvement, disprove it",
                "use `a \u2014 b` here",
                "```\nrecords prove it\n```",
                "~~~\nany format\n~~~",
                "<!-- records\nprove it -->",
                "[link](https://x.org/prove--it)",
                "see https://x.org/performance",
                "---\nowner: performance\ndescription: fine\n---\nbody",
                "---\nowner: >\n  performance\ndescription: fine\n---\nbody",
                "| a | b |\n|---|---|",
                "---",
            ),
        ),
        (plain_lines, ["em_dash"], ("Incomplete \u2014 no control failed",)),
        (plain_lines, [], ("usage: agentce [-h] --out OUT", "`a -- b`")),
    )
    cases = [
        (w, keys(scan("t", reader(w))), want)
        for reader, want, texts in table
        for w in texts
    ]
    failed = [f"{w!r}: got {got}, want {want}" for w, got, want in cases if got != want]
    total = len(cases)

    def expect(ok: bool, failure: str) -> None:
        nonlocal total
        total += 1
        if not ok:
            failed.append(failure)

    line = scan("f", markdown_lines("one\n\ntwo\nrecords\nprove"))
    expect(
        bool(line) and ":5:" in line[0],
        f"line number of a hit split over lines: {line}",
    )
    cat = catalogue_lines('{\n  "a": "fine",\n  "b": "no \u2014 dash"\n}')
    got = [(k, scan(k, ls)) for k, ls in cat]
    expect(
        len(got) == 2 and not got[0][1] and bool(got[1][1]) and "b:3:" in got[1][1][0],
        f"catalogue reader: {got}",
    )

    base_rule = SWEEP[0][1]
    whole_of = {extract: whole for _, extract, whole in SWEEP}
    good = f"description = '{TAGLINE}',"
    sweep_cases = [
        (base_rule, f"  {good}\n", []),
        (base_rule, "  description = 'Other.',\n", ["sweep_value"]),
        (base_rule, "  title = 'x',\n", ["sweep_unreadable"]),
        (base_rule, f"  {good}\n// {PRE_LOCK}\n", ["sweep_retired"]),
        (package_description, json.dumps({"description": TAGLINE}), []),
        (package_description, json.dumps({"name": "x"}), ["sweep_unreadable"]),
        (package_description, "{not json", ["sweep_unreadable"]),
        (readme_opening, f"# t\n\nIntro. {TAGLINE} More.\n", []),
        (readme_opening, "# t\n\nIntro.\n", ["sweep_value"]),
        (readme_opening, "# t\n", ["sweep_unreadable"]),
        (front_description, f"---\ndescription: {TAGLINE}\n---\n", []),
        (front_description, "---\ntitle: x\n---\n", ["sweep_unreadable"]),
    ]
    for extract, text, want in sweep_cases:
        got_keys = keys(sweep_one("f", text, extract, whole_of[extract]))
        expect(
            got_keys == want,
            f"sweep {getattr(extract, '__name__', '?')} {text!r}: got {got_keys}, want {want}",
        )

    usage = [(0, "usage:")]
    unbuilt = [(0, "usage:"), (3, UNBUILT_MARKER)]
    run_cases = [
        (0, "usage: agentce", usage, True),
        (1, "usage: agentce", usage, False),
        (0, "", usage, False),
        (0, "Traceback", usage, False),
        (3, UNBUILT_MARKER, usage, False),
        (3, f"error: {UNBUILT_MARKER}", unbuilt, True),
        (0, "Verdict: Incomplete", [(0, "Verdict:")], True),
        (0, "verdict: incomplete", [(0, "Verdict:")], True),
        (0, "quickstart complete", [(0, "Verdict:")], False),
        (2, "Verdict: Incomplete", [(0, "Verdict:")], False),
    ]
    for code, text, expected, ok in run_cases:
        expect(
            (run_failed("w", code, text, expected) is None) == ok,
            f"run checker: exit {code} {text!r} {expected}: want ok={ok}",
        )
    expect(
        help_commands(
            "x\npositional arguments:\n  <command>\n    assess   run\n    a-b  x\n\nopts:\n    no  x"
        )
        == ["assess", "a-b"],
        "help_commands",
    )

    for failure in failed:
        print(f"ux.copy.self_test_failed: {failure}", file=sys.stderr)
    print(f"ux-copy self-test: {total - len(failed)}/{total} cases")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-cli", action="store_true", help="skip the engine runs")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    errors: list[str] = []
    check(errors, with_cli=not args.no_cli)
    for line in errors:
        print(line, file=sys.stderr)
    if errors:
        print(f"ux-copy: {len(errors)} failure(s)")
        return 1
    print("UX-COPY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
