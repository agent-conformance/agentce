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
a `**Question:**` line after that command and followed by a `**Default:**` line. Each is read with the
lines that continue its paragraph, and every "?" in a Question paragraph is a question. Neither may name
an action beyond reading and writing into the output folder, in any form of the verb. An instruction
to ask (ask, confirm with, request from, check with, prompt the person for, tell me, let me know, in
any tense) inside the section is a question with no default, and so is a request such as "which
catalogs to use". Outside the section it may only come after it, under a heading that names a later
step; a Question or Default marker outside the section is an instruction to ask, and a marker is read
wherever it sits in its line (mid-sentence or in a table cell). A setext heading (underlined with --- or ===) is
refused, so the section's bounds are the ones a reader of the raw file sees.

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

from markdown_reader import Block, blocks, need_parser, split_lines

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
MARKERS = (QUESTION, DEFAULT)
# Where a marker starts, wherever it sits in its line: mid-sentence, or in a table cell.
MARKER_AT = re.compile("(?=" + "|".join(map(re.escape, MARKERS)) + ")")
FIRST_COMMAND = re.compile(r"`agentce (?:assess|quickstart)\b[^`]*`")


def any_form(*verbs: str) -> str:
    """A pattern for every form of each verb ("sign" also matches signs, signed, signing).

    A verb may carry its irregular forms after a "|" ("send|sent"), and words after it ("ask for")."""
    alternatives = []
    for verb in verbs:
        word, _, rest = verb.partition(" ")
        base, *irregular = word.split("|")
        stem = (
            base[:-1] + "(?:e|es|ed|ing)"
            if base.endswith("e")
            else base + "(?:s|es|ed|ing)?"
        )
        head = "(?:" + "|".join([stem, *irregular]) + ")"
        alternatives.append(f"{head} {rest}" if rest else head)
    return r"\b(?:" + "|".join(alternatives) + r")\b"


# An instruction to the assistant to ask the person something, in any tense ("asks", "asking").
ASK = re.compile(
    any_form(
        "ask",
        "confirm with",
        "request from",
        "check with",
        "prompt (?:the )?(?:user|person) for",
        "tell|told (?:me|us)",
        "let (?:me|us) know",
        "give|gave|given (?:me|us)",
        "show (?:me|us)",
        "point (?:me|us) to",
    ),
    re.IGNORECASE,
)
# A question written without a "?" ("Which catalogs to use, too."): a wh-word, then an infinitive. It
# counts where any "?" counts: before the section and inside it.
REQUEST = re.compile(
    r"\b(?:which|what|where|whether)\b(?:\s+\S+){0,5}?\s+to\s+\w+", re.IGNORECASE
)
# A heading for a step that comes after the first findings, where the assistant may ask more.
LATER_STEP = re.compile(r"^#{2,3} .*\b(?:Phases?|Stage \d|After the first minute)\b")
# An answer that acts beyond reading and writing into the output folder is not the safe one,
# in any form of the verb: "signing the report" signs it.
UNSAFE = re.compile(
    any_form(
        "sign",
        "publish",
        "upload",
        "send|sent",
        "push",
        "delete",
        "overwrite|overwrote|overwritten",
        "commit|committed|committing",
        "install",
        "deploy",
        "share",
        "email",
        "submit|submitted|submitting",
        "transmit|transmitted|transmitting",
        "post",
        "attest",
    )
    + r"|--for\b",
    re.IGNORECASE,
)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
YES_NO = re.compile(r"\[(?:y/n|yes/no)\]|\((?:y/n|yes/no)\)", re.IGNORECASE)
ENDS_IN_QUESTION = re.compile(r"\?[\s\"'*`)]*$")
QUESTION_MARK = re.compile(r"\?(?:\W|$)")


def is_ask(line: str) -> bool:
    """A skill line that asks the person something, or tells the assistant to."""
    return bool(ASK.search(line) or REQUEST.search(line) or QUESTION_MARK.search(line))


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


def atx(block: Block) -> str:
    """A heading block as its ATX form, `## words`."""
    return "#" * block.level + " " + " ".join(line.text for line in block.lines)


def prose(found: list[Block]) -> list[tuple[int, str]]:
    """(1-based first line, text) for each piece of prose outside the front matter and fenced code, read
    as CommonMark blocks (markdown_reader): a heading as its ATX form, and every other block whole, its
    lines joined without their block-quote or list markers, so a phrase or a default wrapped onto a
    second line is read in one piece. A Question or Default marker starts a new piece wherever it sits
    (at a line start, mid-sentence or in a table cell), so each marker is read with the text that
    continues it up to the next marker or the end of its block."""
    out: list[tuple[int, str]] = []
    for block in found:
        if block.kind == "fence":
            continue
        if block.kind == "heading":
            out.append((block.start, atx(block)))
            continue
        piece: list[str] = []
        for line in block.text_lines:
            for n, part in enumerate(MARKER_AT.split(line.text.strip())):
                words = part.strip()
                if not words:
                    continue
                if n == 0 and piece:
                    piece.append(words)
                    out[-1] = (out[-1][0], " ".join(piece))
                else:
                    piece = [words]
                    out.append((line.number, words))
    return out


def section_headings(found: list[Block]) -> dict[int, str]:
    """Line number -> ATX form of each top-level heading (no block quote or list item around it). Only
    these bound a section: a heading inside a quote or a list item, or a heading-shaped line of indented
    code, is part of the section it sits in."""
    return {
        block.start: atx(block)
        for block in found
        if block.kind == "heading" and not block.containers
    }


def read_skill(rel: str, text: str, errors: list[str]) -> int:
    """Check one SKILL.md's first minute; return how many questions it asks there."""
    raw = split_lines(text)
    found = blocks(text)
    # A setext heading (text underlined with --- or ===) is a heading only to a CommonMark reader: an
    # assistant reading the file sees a paragraph and a rule, so it could carry a question out of the
    # section the gate reads. Headings in a skill are written in ATX form.
    for block in found:
        if block.kind == "heading" and block.end > block.start:
            errors.append(
                f"ux.one_question.skill_setext_heading: {rel}:{block.start} is a heading underlined "
                f"at line {block.end}; write it as {atx(block)!r} so the sections the "
                "gate reads are the ones the assistant sees"
            )
    lines = prose(found)
    headings = section_headings(found)
    start = next((n for n, h in headings.items() if h.rstrip() == SECTION), None)
    if start is None:  # with no first minute, every ask comes before the first findings
        errors.append(
            f"ux.one_question.skill_no_first_minute: {rel} has no '{SECTION}' section"
        )
        start = end = len(raw) + 1
    else:
        end = next(
            (n for n, h in headings.items() if n > start and h.startswith("## ")),
            len(raw) + 1,
        )
    body = [(n, line) for n, line in lines if start < n < end]

    heading = ""
    for n, line in lines:
        if n in headings and headings[n].startswith(("## ", "### ")):
            heading = headings[n]
        # Before the section any question counts; after it, only an instruction to ask (a "?" there
        # is the skill quoting what a person says, as in "When to use"). A Question or Default
        # marker is an instruction to ask wherever it sits.
        asks_here = line.startswith(MARKERS) or (
            is_ask(line) if n < start else ASK.search(line)
        )
        if start <= n < end or not asks_here:
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
    # A Question paragraph asks once per "?" in it; a Default paragraph that asks or tells the
    # assistant to ask holds a question with no default of its own.
    unmarked = [
        (n, line) for n, line in body if not line.startswith(QUESTION) and is_ask(line)
    ]
    count = len(unmarked) + sum(
        max(1, len(QUESTION_MARK.findall(line))) for _, line in asks
    )
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
        "question shares, default yes",
        GOOD.replace(
            GOOD_QUESTION.removeprefix("**Question:** "),
            '"Share the report with your auditor now?"',
        ).replace("keep the folder you found;", "yes;"),
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


# Read as CommonMark blocks (18.211): a question on a list item still counts, and a fence closes only on a
# run as long as its opener, so a question after a ```` block holding ``` is in the prose.
SELF_TEST += [
    (
        "a second question on a list item",
        GOOD.replace(
            GOOD_DEFAULT + "\n", GOOD_DEFAULT + '\n\n- **Question:** "Which catalog?"\n'
        ),
        {"skill_too_many_questions", "skill_no_default"},
    ),
    (
        "a question after a long fence holding a short one",
        GOOD.replace(
            '```\necho "a fenced line? not a question"\n```',
            "````\n```\n````\n\nWant the report too?",
        ),
        {"skill_too_many_questions", "skill_question_unmarked"},
    ),
]

# Only a top-level heading bounds the first minute: a heading in indented code, in a block quote, or an
# indented-code copy of the section's own heading before it neither ends nor starts the section.
EXTRA_ASKS = "Want the report too? And should I sign it?\n\n"
SELF_TEST += [
    (
        "an indented-code heading inside the first minute",
        GOOD.replace(
            GOOD_QUESTION, "A note.\n\n    ## Phases\n\n" + EXTRA_ASKS + GOOD_QUESTION
        ),
        {"skill_too_many_questions", "skill_question_unmarked"},
    ),
    (
        "a block-quoted heading inside the first minute",
        GOOD.replace(GOOD_QUESTION, "> ## Note\n\n" + EXTRA_ASKS + GOOD_QUESTION),
        {"skill_too_many_questions", "skill_question_unmarked"},
    ),
    (
        "an indented-code copy of the section heading before it",
        GOOD.replace(
            "## First minute", "    ## First minute\n\n## First minute"
        ).replace(GOOD_QUESTION, EXTRA_ASKS + GOOD_QUESTION),
        {"skill_too_many_questions", "skill_question_unmarked"},
    ),
]

# Every form of an unsafe verb in a default is refused, and every tense of an ask before the section.
LAST_SECTION = GOOD.split("## Phases", 1)[0]
SELF_TEST += [
    ("first minute as the last section, one question", LAST_SECTION, set()),
    (
        "first minute as the last section, two questions",
        LAST_SECTION.replace(
            "**Default:** keep",
            '**Question:** "Which catalog?"\n**Default:** baseline.\n**Default:** keep',
        ),
        {"skill_too_many_questions"},
    ),
]
SELF_TEST += [
    (
        f"default {answer!r}",
        GOOD.replace("keep the folder you found;", f"{answer};"),
        {"skill_default_unsafe"},
    )
    for answer in (
        "signing the report",
        "the report publishes to your site",
        "committing the evidence to main",
        "email the report to compliance",
        "the evidence is sent to the auditor",
        "overwrote the old report",
    )
] + [
    (
        f"ask before the section: {line!r}",
        GOOD.replace("Intro.", line),
        {"skill_asks_before_first_minute"},
    )
    for line in (
        "Start by asking which repository to use.",
        "The assistant asks for the repository path first.",
        "Check with the person which catalog applies.",
        "Prompt the user for the trace folder.",
    )
]

# A Question or Default is its whole paragraph (verdicts/P18-18.40a-69e939da.md F1, F2): an unsafe verb or
# a second "?" on a wrapped line still counts, and a question wrapped before its "?" is still one question.
SELF_TEST += [
    (
        "default wrapped, unsafe verb on its second line",
        GOOD.replace(
            GOOD_DEFAULT, GOOD_DEFAULT + "\nThen it signs the report for you."
        ),
        {"skill_default_unsafe"},
    ),
    (
        "two questions on one Question line",
        GOOD.replace(
            GOOD_QUESTION,
            '**Question:** "Is this your trace folder? Which catalogs should I use?"',
        ),
        {"skill_too_many_questions"},
    ),
    (
        "a second question on the Question's wrapped line",
        GOOD.replace(GOOD_QUESTION, GOOD_QUESTION + "\nIf not, which one?"),
        {"skill_too_many_questions"},
    ),
    (
        "a question on the Default's wrapped line",
        GOOD.replace(GOOD_DEFAULT, GOOD_DEFAULT + "\nShould I also read the logs?"),
        {"skill_too_many_questions", "skill_question_unmarked"},
    ),
    (
        "a question wrapped before its question mark",
        GOOD.replace(
            GOOD_QUESTION,
            '**Question:** "Is this the folder your traces land in,\nor should I read another one?"',
        ),
        set(),
    ),
    (
        "an ask wrapped across two lines in the section",
        GOOD.replace(
            "show the verdict.",
            "show the verdict.\n\nThen confirm\nwith the person which catalogs to use.",
        ),
        {"skill_too_many_questions", "skill_question_unmarked"},
    ),
    (
        "an ask wrapped across two lines before the section",
        GOOD.replace(
            "Intro.", "Intro. Check\nwith the person which repository to use."
        ),
        {"skill_asks_before_first_minute"},
    ),
    # A rule under the Question and Default turns them into a setext heading that ends the section
    # (verdicts/P18-18.40a-2af99f74.md B1), and a marked question after the section under no later
    # step is still asked before the first minute is over (B2).
    (
        "a rule under the Question and Default",
        GOOD.replace(GOOD_DEFAULT, GOOD_DEFAULT + "\nThen it signs the report.\n---"),
        {"skill_setext_heading"},
    ),
    (
        "a heading underlined with ===",
        GOOD.replace("# demo\n", "demo\n===\n"),
        {"skill_setext_heading"},
    ),
    (
        "a Question and Default under When to use",
        GOOD.replace(
            "## Phases",
            '## When to use\n\n**Question:** "Which catalogs?"\n**Default:** sign it.\n\n## Phases',
        ),
        {"skill_asks_outside_later_step"},
    ),
    (
        "a Question and Default before the section",
        GOOD.replace(
            "Intro.", '**Question:** "Which catalogs"\n**Default:** baseline.'
        ),
        {"skill_asks_before_first_minute"},
    ),
] + [
    (
        f"default {answer!r}",
        GOOD.replace("keep the folder you found;", f"{answer};"),
        {"skill_default_unsafe"},
    )
    for answer in (
        "post the report to the team channel",
        "the report is posted to the team channel",
        "attest the report",
    )
]

# A marker is read wherever it sits in its line (verdicts/P18-18.40a-6be13405.md B1), and an imperative
# request is an ask (KD-776).
MID_LINE_PAIR = "Then: **Question:** which catalogs to assess against. **Default:** sign the report and publish it."
TABLE_PAIR = '| step | say |\n|---|---|\n| 1 | **Question:** "Which catalogs?" **Default:** sign the report. |'
PLACES = (
    (
        "in the section",
        GOOD_DEFAULT,
        GOOD_DEFAULT + "\n\n{}",
        {"skill_too_many_questions", "skill_default_unsafe"},
    ),
    (
        "under When to use",
        "## Phases",
        "## When to use\n\n{}\n\n## Phases",
        {"skill_asks_outside_later_step"},
    ),
)
SELF_TEST += [
    (
        f"a Question and Default {shape} {where}",
        GOOD.replace(old, new.format(pair)),
        want,
    )
    for shape, pair in (("mid-line", MID_LINE_PAIR), ("in a table cell", TABLE_PAIR))
    for where, old, new, want in PLACES
]
SELF_TEST += [
    (
        "a mid-line Question and Default in a later step",
        GOOD.replace(
            "one question at a time.",
            "one question at a time. **Question:** which catalogs? **Default:** baseline.",
        ),
        set(),
    ),
    (
        "an imperative ask after the section under When to use",
        GOOD.replace(
            "## Phases", "## When to use\n\nTell me which catalogs apply.\n\n## Phases"
        ),
        {"skill_asks_outside_later_step"},
    ),
]
SELF_TEST += [
    (
        f"imperative ask in the section: {line!r}",
        GOOD.replace("show the verdict.", f"show the verdict.\n\n{line}"),
        {"skill_too_many_questions", "skill_question_unmarked"},
    )
    for line in (
        "Tell me which catalogs to use too.",
        "Let me know which catalogs apply.",
        "Give me the repository path.",
        "Then: which catalogs to use, as well.",
        "Then: which folder to scan next.",
    )
]
SELF_TEST += [
    (
        f"imperative ask before the section: {line!r}",
        GOOD.replace("Intro.", line),
        {"skill_asks_before_first_minute"},
    )
    for line in ("Tell me the repository path first.", "Let us know your catalog.")
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
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n", 1)[0], allow_abbrev=False
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the skill reader over built-in texts",
    )
    args = parser.parse_args()
    need_parser()
    if args.self_test:
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
