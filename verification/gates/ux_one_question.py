"""VG-UX-ONE-QUESTION (18.40a, USER_EXPERIENCE.md R2): at most one question in the first minute.

R2: first findings with no question, then at most one question in the first minute, asked at the
terminal, with the safe answer as the default. The first minute is what a person meets on the Try
path: the coding assistant loads an approved skill (skills/*/SKILL.md) and runs the engine's
first-run commands. This gate reads both, and the one question is a budget they share. When the
no-argument `agentce` (19.28) lands, its command joins RUNS below.

The CLI leg runs each command in RUNS in Python, TypeScript and Java under a pseudo-terminal with
nothing typed. A command that asks and waits is still running at the end of the first minute; one
that asks and goes on prints a question-shaped line. Either is a question. A command that exits
without asking passes here whatever its exit code; other gates hold exit codes.

The skill leg reads each SKILL.md's `## First minute` section. It must name a first command that runs
with no question (`agentce assess` or `agentce quickstart`), and may then ask one question, written as
a `**Question:**` line after that command and followed by a `**Default:**` line. Neither line may name
an action beyond reading and writing into the output folder, in any form of the verb. An instruction
to ask (ask, confirm with, request from, check with, prompt the person for, in any tense) inside the section is a question with no default. Outside the section it may only come
after it, under a heading that names a later step.

Stdout ends with `UX-ONE-QUESTION OK` when every check holds; each failure is one stderr line led by
its stable key. `--self-test` runs the skill reader over built-in texts.
"""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACE_FIXTURE = (
    ROOT / "adapters/otel-genai/fixtures/otel-genai-agent-session/input.json"
)
FIRST_MINUTE_S = 60.0
BUDGET = 1
# The first-run commands: the arguments after `agentce`, given an output folder and a trace folder.
RUNS = {
    "quickstart": lambda out, traces: ["quickstart", "--out", out],
    "assess": lambda out, traces: ["assess", traces, "--out", out],
}

SECTION = "## First minute"
QUESTION = "**Question:**"
DEFAULT = "**Default:**"
FIRST_COMMAND = re.compile(r"`agentce (?:assess|quickstart)\b[^`]*`")
# An instruction to the assistant to ask the person something, in any tense ("asks", "asking").
ASK = re.compile(
    r"\b(?:ask(?:s|ed|ing)?|confirm(?:s|ed|ing)? with|request(?:s|ed|ing)? from"
    r"|check(?:s|ed|ing)? with|prompt(?:s|ed|ing)? (?:the )?(?:user|person) for)\b",
    re.IGNORECASE,
)
# A heading for a step that comes after the first findings, where the assistant may ask more.
LATER_STEP = re.compile(r"^#{2,3} .*\b(?:Phases?|Stage \d|After the first minute)\b")
# An answer that acts beyond reading and writing into the output folder is not the safe one.
# Every form of each verb counts: "signing the report" signs it.
UNSAFE = re.compile(
    r"\b(?:sign(?:s|ed|ing)?|publish(?:es|ed|ing)?|upload(?:s|ed|ing)?|sen(?:d|ds|ding|t)"
    r"|push(?:es|ed|ing)?|delet(?:e|es|ed|ing)|overwr(?:ite|ites|iting|itten|ote)"
    r"|commit(?:s|ted|ting)?|install(?:s|ed|ing)?|deploy(?:s|ed|ing)?|shar(?:e|es|ed|ing)"
    r"|email(?:s|ed|ing)?|submit(?:s|ted|ting)?|transmit(?:s|ted|ting)?)\b|--for\b",
    re.IGNORECASE,
)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
YES_NO = re.compile(r"\[(?:y/n|yes/no)\]|\((?:y/n|yes/no)\)", re.IGNORECASE)
ENDS_IN_QUESTION = re.compile(r"\?[\s\"'*`)]*$")
QUESTION_MARK = re.compile(r"\?(?:\W|$)")


def is_ask(line: str) -> bool:
    """A skill line that asks the person something, or tells the assistant to."""
    return bool(ASK.search(line) or QUESTION_MARK.search(line))


def engine_commands() -> dict[str, list[str]]:
    jars = sorted(
        (ROOT / "engines/java/build/libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    ts = ROOT / "engines/typescript/bin/agentce.js"  # the npm bin a user runs
    if not jars or not ts.is_file():
        raise SystemExit(
            "ux.one_question.not_built: source verification/gates/build_engines.sh first"
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


def run_at_terminal(argv: list[str], timeout: float) -> tuple[int | None, str]:
    """Run argv with a pseudo-terminal as stdin, stdout and stderr, and type nothing.

    Returns (exit code, terminal text). The exit code is None when the command printed a question,
    which stops it at once, or was still running at the deadline, which with nothing typed means it is
    waiting for an answer.
    """
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["NO_COLOR"] = "1"
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        argv,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=ROOT,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    try:
        while (left := deadline - time.monotonic()) > 0:
            if select.select([master], [], [], min(left, 0.5))[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:  # EIO: every process holding the terminal has closed it
                    data = b""
                if not data:
                    return proc.wait(), decode(chunks)
                chunks.append(data)
                if question_lines(decode(chunks)):
                    break
            elif proc.poll() is not None:
                return proc.returncode, decode(chunks)
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        return None, decode(chunks)
    finally:
        os.close(master)


def decode(chunks: list[bytes]) -> str:
    return ANSI.sub("", b"".join(chunks).decode("utf-8", "replace")).replace("\r", "")


def question_lines(text: str) -> list[str]:
    """The lines of terminal output a person would read as a question to answer."""
    return [
        line.strip()
        for line in text.split("\n")
        if ENDS_IN_QUESTION.search(line) or YES_NO.search(line)
    ]


def cli_leg(errors: list[str]) -> int:
    asked = 0
    with tempfile.TemporaryDirectory() as tmp:
        traces = Path(tmp) / "traces"
        traces.mkdir()
        shutil.copy(TRACE_FIXTURE, traces / "input.json")
        for engine, base in engine_commands().items():
            for name, args in RUNS.items():
                out = str(Path(tmp) / f"{engine}-{name}")
                code, text = run_at_terminal(
                    base + args(out, str(traces)), FIRST_MINUTE_S
                )
                where = f"{engine} agentce {name}"
                found = question_lines(text)
                for line in found:
                    errors.append(
                        f"ux.one_question.cli_asked: {where} printed a question: {line!r}. "
                        "Remove the prompt or give it a flag."
                    )
                if code is None and not found:
                    last = (
                        text.rstrip().rsplit("\n", 1)[-1].strip() or "(nothing printed)"
                    )
                    errors.append(
                        f"ux.one_question.cli_waited: {where} was still running after "
                        f"{FIRST_MINUTE_S:.0f} s at a terminal with nothing typed, so it is waiting "
                        f"for an answer; last line: {last!r}. Remove the prompt or give it a flag."
                    )
                    found = [last]
                asked += len(found)
                print(
                    f"ux-one-question: {where}: exit {code}, {len(found)} question(s)"
                )
    return asked


def prose(lines: list[str]) -> list[tuple[int, str]]:
    """(1-based line number, text) for every line outside the front matter and fenced code."""
    out: list[tuple[int, str]] = []
    fenced = False
    front = bool(lines) and lines[0].rstrip() == "---"
    for number, line in enumerate(lines, start=1):
        if front:
            front = number == 1 or line.rstrip() != "---"
            continue
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            out.append((number, line))
    return out


def read_skill(rel: str, text: str, errors: list[str]) -> int:
    """Check one SKILL.md's first minute; return how many questions it asks there."""
    raw = text.split("\n")
    lines = prose(raw)
    start = next((n for n, line in lines if line.rstrip() == SECTION), None)
    if start is None:  # with no first minute, every ask comes before the first findings
        errors.append(
            f"ux.one_question.skill_no_first_minute: {rel} has no '{SECTION}' section"
        )
        start = end = len(raw) + 1
    else:
        end = next(
            (n for n, line in lines if n > start and line.startswith("## ")),
            len(lines) + 2,
        )
    body = [(n, line.strip()) for n, line in lines if start < n < end]

    heading = ""
    for n, line in lines:
        if line.startswith(("## ", "### ")):
            heading = line
        # Before the section any question counts; after it, only an instruction to ask (a "?" there
        # is the skill quoting what a person says, as in "When to use").
        if start <= n < end or not (is_ask(line) if n < start else ASK.search(line)):
            continue
        if n < start:
            errors.append(
                f"ux.one_question.skill_asks_before_first_minute: {rel}:{n} tells the assistant to "
                f"ask before the first findings: {line.strip()!r}. Move it to a later step."
            )
        elif not LATER_STEP.search(heading):
            errors.append(
                f"ux.one_question.skill_asks_outside_later_step: {rel}:{n} tells the assistant to "
                f"ask under {heading.strip()!r}, which names no step after the first minute: "
                f"{line.strip()!r}. Move it under a Phase or Stage heading."
            )
    if not body:
        return 0
    first_command = next((n for n, line in body if FIRST_COMMAND.search(line)), None)
    if first_command is None:
        errors.append(
            f"ux.one_question.skill_no_first_command: {rel} '{SECTION}' names no `agentce assess` "
            "or `agentce quickstart` command to run before any question"
        )
    asks = [(n, line) for n, line in body if line.startswith(QUESTION)]
    defaults = [(n, line) for n, line in body if line.startswith(DEFAULT)]
    unmarked = [
        (n, line)
        for n, line in body
        if not line.startswith((QUESTION, DEFAULT)) and is_ask(line)
    ]
    count = len(asks) + len(unmarked)
    if count > BUDGET:
        where = ", ".join(str(n) for n, _ in sorted(asks + unmarked))
        errors.append(
            f"ux.one_question.skill_too_many_questions: {rel} '{SECTION}' asks {count} questions "
            f"(lines {where}); R2 allows {BUDGET}"
        )
    for n, line in unmarked:
        errors.append(
            f"ux.one_question.skill_question_unmarked: {rel}:{n} asks something that is not its "
            f"'{QUESTION}' line, so it has no stated default: {line!r}"
        )
    for n, _ in asks:
        if first_command is not None and n < first_command:
            errors.append(
                f"ux.one_question.skill_question_before_value: {rel}:{n} asks before the first "
                f"command (line {first_command}) has run"
            )
    if len(defaults) != len(asks):
        errors.append(
            f"ux.one_question.skill_no_default: {rel} '{SECTION}' has {len(asks)} '{QUESTION}' and "
            f"{len(defaults)} '{DEFAULT}' line(s); each question needs one default"
        )
    for n, line in asks + defaults:
        answer = line.split(":**", 1)[1].strip()
        bad = UNSAFE.search(answer)
        if not answer or bad:
            errors.append(
                f"ux.one_question.skill_default_unsafe: {rel}:{n} "
                f"{'is empty' if not bad else f'names {bad.group(0)!r}'}; "
                "the question and its default may only read, write into the output folder and send nothing"
            )
    return count


def skill_leg(errors: list[str]) -> int:
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    if not skills:
        errors.append("ux.one_question.no_skills: no skills/*/SKILL.md found")
    most = 0
    for skill in skills:
        rel = str(skill.relative_to(ROOT))
        count = read_skill(rel, skill.read_text(encoding="utf-8"), errors)
        print(f"ux-one-question: {rel}: {count} question(s) in '{SECTION}'")
        most = max(most, count)
    return most


GOOD_QUESTION = '**Question:** "Is this the folder your traces land in, or should I read another one?"'
GOOD_DEFAULT = "**Default:** keep the folder you found; it only reads files and writes into agentce-out."
GOOD = (
    """---
name: demo
description: "Use when asked to? never a question here"
---
# demo

Intro.

## First minute

Run `agentce assess traces --out agentce-out` and show the verdict.

"""
    + GOOD_QUESTION
    + "\n"
    + GOOD_DEFAULT
    + """

```
echo "a fenced line? not a question"
```

## Phases

**A**. After the first findings, ask for the intended purpose, one question at a time.
"""
)

SELF_TEST: list[tuple[str, str, set[str]]] = [
    ("clean, one question", GOOD, set()),
    (
        "clean, no question",
        GOOD.replace(
            GOOD_QUESTION + "\n",
            "",
        ).replace(
            GOOD_DEFAULT + "\n",
            "",
        ),
        set(),
    ),
    (
        "no section: every ask comes before the first findings",
        GOOD.replace("## First minute", "## Start"),
        {"skill_no_first_minute", "skill_asks_before_first_minute"},
    ),
    (
        "no first command",
        GOOD.replace("`agentce assess traces --out agentce-out`", "the tool"),
        {"skill_no_first_command"},
    ),
    (
        "question before the command",
        GOOD.replace(
            "Run `agentce assess traces --out agentce-out` and show the verdict.\n\n",
            "",
        ).replace(
            "agentce-out.\n\n```",
            "agentce-out.\n\nThen run `agentce assess traces --out agentce-out`.\n\n```",
        ),
        {"skill_question_before_value"},
    ),
    (
        "two questions",
        GOOD.replace(
            "**Default:** keep",
            '**Question:** "Which catalog?"\n**Default:** baseline.\n**Default:** keep',
        ),
        {"skill_too_many_questions"},
    ),
    (
        "unmarked question",
        GOOD.replace("show the verdict.", "show the verdict. Want the report too?"),
        {"skill_too_many_questions", "skill_question_unmarked"},
    ),
    (
        "ask instruction in the section",
        GOOD.replace(
            "show the verdict.",
            "show the verdict.\n\nAsk for anything missing (repository path, environments).",
        ),
        {"skill_too_many_questions", "skill_question_unmarked"},
    ),
    (
        "no default",
        GOOD.replace(
            GOOD_DEFAULT + "\n",
            "",
        ),
        {"skill_no_default"},
    ),
    (
        "empty default",
        GOOD.replace(
            GOOD_DEFAULT,
            "**Default:**",
        ),
        {"skill_default_unsafe"},
    ),
    (
        "default signs",
        GOOD.replace("keep the folder you found;", "sign the report;"),
        {"skill_default_unsafe"},
    ),
    (
        "default uploads",
        GOOD.replace("keep the folder you found;", "upload the report;"),
        {"skill_default_unsafe"},
    ),
    (
        "default signs, -ing form",
        GOOD.replace("keep the folder you found;", "signing the report;"),
        {"skill_default_unsafe"},
    ),
    (
        "default publishes, -es form",
        GOOD.replace(
            "keep the folder you found;", "the report publishes to your site;"
        ),
        {"skill_default_unsafe"},
    ),
    (
        "default committing",
        GOOD.replace("keep the folder you found;", "committing the evidence to main;"),
        {"skill_default_unsafe"},
    ),
    (
        "question shares, default yes",
        GOOD.replace(
            GOOD_QUESTION.removeprefix("**Question:** "),
            '"Share the report with your auditor now?"',
        ).replace("keep the folder you found;", "yes;"),
        {"skill_default_unsafe"},
    ),
    (
        "default emails",
        GOOD.replace("keep the folder you found;", "email the report to compliance;"),
        {"skill_default_unsafe"},
    ),
    (
        "default narrows",
        GOOD.replace("keep the folder you found;", "use `--for share`;"),
        {"skill_default_unsafe"},
    ),
    (
        "unsafe question, default yes",
        GOOD.replace(
            GOOD_QUESTION.removeprefix("**Question:** "),
            '"Sign and publish the report now?"',
        ).replace("keep the folder you found;", "yes;"),
        {"skill_default_unsafe"},
    ),
    (
        "ask before the section",
        GOOD.replace("Intro.", "Intro. Ask for the repository path first."),
        {"skill_asks_before_first_minute"},
    ),
    (
        "asking before the section",
        GOOD.replace("Intro.", "Start by asking which repository to use."),
        {"skill_asks_before_first_minute"},
    ),
    (
        "asks before the section",
        GOOD.replace("Intro.", "The assistant asks for the repository path first."),
        {"skill_asks_before_first_minute"},
    ),
    (
        "check with before the section",
        GOOD.replace("Intro.", "Check with the person which catalog applies."),
        {"skill_asks_before_first_minute"},
    ),
    (
        "prompt the user before the section",
        GOOD.replace("Intro.", "Prompt the user for the trace folder."),
        {"skill_asks_before_first_minute"},
    ),
    (
        "asked in the section",
        GOOD.replace(
            "show the verdict.",
            "show the verdict.\n\nOnce asked, wait for the repository path.",
        ),
        {"skill_too_many_questions", "skill_question_unmarked"},
    ),
    (
        "question before the section",
        GOOD.replace("Intro.", "Which repository is it?"),
        {"skill_asks_before_first_minute"},
    ),
    (
        "ask after the section, no later step",
        GOOD.replace("## Phases", "## When to use"),
        {"skill_asks_outside_later_step"},
    ),
]


TERMINAL_CASES: list[tuple[str, bool]] = [
    ("Continue? ", True),
    ("Overwrite the output folder [y/N] ", True),
    ("Proceed (yes/no)", True),
    ('Use the sample? "', True),
    ("Next step: run the assessment again.", False),
    ("see docs/errors.md?plain=1 for more", False),
]


def self_test() -> int:
    failed = 0
    for name, text, want in SELF_TEST:
        errors: list[str] = []
        read_skill("demo/SKILL.md", text, errors)
        got = {e.split(":", 1)[0].removeprefix("ux.one_question.") for e in errors}
        if got != want:
            failed += 1
            print(
                f"self-test FAIL {name}: want {sorted(want)}, got {sorted(got)}: {errors}",
                file=sys.stderr,
            )
    for line, asks_question in TERMINAL_CASES:
        if bool(question_lines(line)) != asks_question:
            failed += 1
            print(
                f"self-test FAIL question_lines({line!r}) should be {asks_question}",
                file=sys.stderr,
            )
    if failed:
        return 1
    print(
        f"UX-ONE-QUESTION SELF-TEST OK ({len(SELF_TEST) + len(TERMINAL_CASES)} cases)"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the skill reader over built-in texts",
    )
    if parser.parse_args().self_test:
        return self_test()
    errors: list[str] = []
    skill_questions = skill_leg(errors)
    cli_questions = cli_leg(errors)
    if skill_questions + cli_questions > BUDGET:
        errors.append(
            f"ux.one_question.over_budget: a skill's first minute asks {skill_questions} and the "
            f"engines ask {cli_questions}; together R2 allows {BUDGET}"
        )
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print("UX-ONE-QUESTION OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
