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
lines that continue its paragraph, and every "?" in a Question paragraph is a question, as is a sentence
in it that asks without one. Neither may name
an action beyond reading and writing into the output folder, in any form of the verb. An instruction
to ask (ask, confirm with, request from, check with, prompt the person for, tell me, let me know, in
any tense) inside the section is a question with no default, and so is a request such as "which
catalogs to use". Outside the section it may only come after it, under a heading that names a later
step; a Question or Default marker outside the section is an instruction to ask, and a marker is read
wherever it sits in its line (mid-sentence or in a table cell). A setext heading (underlined with --- or ===) is
refused, so the section's bounds are the ones a reader of the raw file sees.

The First minute fails closed. A Question is one sentence that ends in its only "?" and holds no other
ask. The section's raw source, whitespace collapsed, must be one of the texts in REVIEWED: a person reads a
changed first minute for a second question and adds its new text there, so an ask in words no phrase list
knows is refused too. A skill has one First minute heading.

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
import unicodedata
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

SECTION_NAME = "First minute"
SECTION = "## " + SECTION_NAME
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
# A sentence and the quote or emphasis marks that close it.
SENTENCE = re.compile(r"[^.?!]+[.?!]*[\"'*`)\]]*")

# Each approved skill's First minute as reviewed for a second question: its raw source between the heading and
# the next top-level heading, whitespace collapsed (section_text). Reading prose for asks by phrase list passed
# whatever the list did not know (six verifier FAILs on one premise), so the section fails closed: a changed
# first minute is refused until a person has read it for a second question and pasted the section here as
# written in the skill.
REVIEWED = frozenset(
    " ".join(section.split())
    for section in (
        """
Show the person their first findings before any question. In the repository you were opened
in, find a folder of trace exports (OpenTelemetry GenAI or OpenInference, OTLP/JSON in `.json` or
`.jsonl` files) and run `agentce assess <folder> --out agentce-out`. If the repository holds none, run
`agentce quickstart --out agentce-out`, which assesses the bundled sample. Show the verdict line and
the top gaps from the terminal output.

After that you may put one question to the person, in the terminal, and no other until the first minute is over:

**Question:** "Is this the folder your agent exports its traces to, or should I read another one?"
**Default:** keep the folder you found, or the bundled sample. Either run only reads files, writes only into `agentce-out` and uses no network.
""",
        """
Show the person where the report stands before any question. Run
`agentce assess --bundle <bundle> --profile agentce/applicability.yaml --out agentce-out` on the
bundle and profile the repository already holds (the `agentce-get-evidence` skill writes them), then
`agentce readiness agentce-out`. Show the assessment's verdict line and the readiness verdict from the
terminal output.

After that you may put one question to the person, in the terminal, and no other until the first minute is over:

**Question:** "Who will read this report: your developers, compliance, security, an auditor or a buyer?"
**Default:** every format, written into `agentce-out`; nothing leaves this machine.
""",
    )
)


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
        # A heading is read as one line, its ATX form.
        lines = (
            [(block.start, atx(block))]
            if block.kind == "heading"
            else [(line.number, line.text) for line in block.text_lines]
        )
        piece: list[str] = []
        for number, text in lines:
            for n, part in enumerate(MARKER_AT.split(text.strip())):
                words = part.strip()
                if not words:
                    continue
                if n == 0 and piece:
                    piece.append(words)
                    out[-1] = (out[-1][0], " ".join(piece))
                else:
                    piece = [words]
                    out.append((number, words))
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


def as_read(block: Block) -> str:
    """A block's text as a reader sees it: markup and HTML tags gone, format characters (zero-width
    spaces) dropped, any run of whitespace (tab, no-break space) one space, case folded."""
    text = " ".join(line.text for line in block.visible)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(text.split()).casefold()


def first_minute_bounds(
    found: list[Block], last: int
) -> tuple[int | None, int, list[int]]:
    """The First minute's heading line (None if there is none), the line of the next top-level `## `
    heading after it (or `last` + 1), and every other block a reader takes for a First minute heading: a
    heading or HTML block whose text names the first minute other than as "after the first minute", or a
    paragraph that reads "first minute" and nothing else (a bold line standing in for a heading)."""
    headings = section_headings(found)
    start = next((n for n, h in headings.items() if h.rstrip() == SECTION), None)
    end = next(
        (
            n
            for n, h in headings.items()
            if start is not None and n > start and h.startswith("## ")
        ),
        last + 1,
    )
    name = SECTION_NAME.casefold()
    named = [
        block.start
        for block in found
        if block.start != start
        and (
            block.kind in ("heading", "html")
            and name in as_read(block).replace("after the " + name, "")
            or block.kind == "paragraph"
            and as_read(block) == name
        )
    ]
    return start, end, named


def section_text(raw: list[str], start: int, end: int) -> str:
    """The section's raw source lines after its heading and before line `end`, whitespace collapsed: every
    character a reader of the file sees, including what no parsed block carries (a link reference
    definition, a fence's info string, an HTML comment)."""
    return " ".join(" ".join(raw[start : end - 1]).split())


def question_shape(question: str) -> bool:
    """A Question's text is one sentence that ends in its only "?" and tells the assistant to ask nothing more."""
    sentences = [s for s in SENTENCE.findall(question) if s.strip()]
    return (
        len(sentences) == 1
        and len(QUESTION_MARK.findall(question)) == 1
        and bool(ENDS_IN_QUESTION.search(question))
        and not ASK.search(question)
    )


def read_skill(
    rel: str,
    text: str,
    errors: list[str],
    reviewed: frozenset[str] | None = REVIEWED,
) -> int:
    """Check one SKILL.md's first minute; return how many questions it asks there. `reviewed` is the set of
    section texts that pass (None skips that check, for the self-test of the other rules)."""
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
    found_start, end, repeated = first_minute_bounds(found, len(raw))
    for n in repeated:
        errors.append(
            f"ux.one_question.skill_first_minute_repeated: {rel}:{n} reads as a second First minute "
            f"heading; a skill has one '{SECTION}' section"
        )
    if (
        found_start is None
    ):  # with no first minute, every ask comes before the first findings
        errors.append(
            f"ux.one_question.skill_no_first_minute: {rel} has no '{SECTION}' section"
        )
        start = end
    else:
        start = found_start
    body = [(n, line) for n, line in lines if start < n < end]
    if reviewed is not None and found_start is not None:
        here = section_text(raw, start, end)
        if here not in reviewed:
            errors.append(
                f"ux.one_question.skill_unreviewed: {rel}:{start}-{end - 1} '{SECTION}' is not text the "
                "gate has reviewed for a second question. Read the new text for any ask beyond its one "
                f"'{QUESTION}', then paste the section as written into REVIEWED in "
                f"verification/gates/ux_one_question.py; it now reads: {here!r}"
            )

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
    # Any other paragraph that asks or tells the assistant to ask holds a question with no default of
    # its own. A Question paragraph asks once per "?" in it, and a sentence in it that asks without a
    # "?" ("Tell me which catalogs to use too.") is one more question with no default.
    unmarked = [
        (n, line) for n, line in body if not line.startswith(QUESTION) and is_ask(line)
    ]
    marked = 0
    for n, line in asks:
        if not question_shape(line.removeprefix(QUESTION).strip()):
            errors.append(
                f"ux.one_question.skill_question_shape: {rel}:{n} '{QUESTION}' must be one sentence ending "
                f"in its only '?', with nothing else in its paragraph and no other ask inside it: {line!r}"
            )
        marks = len(QUESTION_MARK.findall(line))
        marked += max(1, marks)
        extra = [
            (n, sentence.strip())
            for sentence in SENTENCE.findall(line.removeprefix(QUESTION))
            if not QUESTION_MARK.search(sentence) and is_ask(sentence)
        ]
        # The Question counts once even with no "?" (and is refused by its shape), so every asking
        # sentence beside it is one more.
        unmarked += extra
    count = len(unmarked) + marked
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
        {"skill_question_shape", "skill_too_many_questions"},
    ),
    (
        "a second question on the Question's wrapped line",
        GOOD.replace(GOOD_QUESTION, GOOD_QUESTION + "\nIf not, which one?"),
        {"skill_question_shape", "skill_too_many_questions"},
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
        {"skill_setext_heading", "skill_asks_outside_later_step"},
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
        # The mid-line Question has no "?" and asks for "which catalogs to assess against".
        want | extra if where == "in the section" else want,
    )
    for shape, pair, extra in (
        (
            "mid-line",
            MID_LINE_PAIR,
            {"skill_question_shape", "skill_question_unmarked"},
        ),
        ("in a table cell", TABLE_PAIR, set()),
    )
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

# An ask written inside the Question paragraph after its "?" is a second question (verdicts/P18-18.40a-5c1244e1.md
# F1), and a marker inside a heading is read like any other.
SELF_TEST += [
    (
        f"an ask in the Question paragraph: {where}",
        GOOD.replace(GOOD_QUESTION, GOOD_QUESTION + sep + line),
        {"skill_too_many_questions", "skill_question_unmarked", "skill_question_shape"},
    )
    for where, sep, line in (
        ("same line", " ", "Tell me which catalogs to use too."),
        (
            "wrapped line",
            "\n",
            "Let me know which catalogs to assess against, as well.",
        ),
    )
] + [
    (
        "a Question and Default in a heading in the section",
        GOOD.replace(
            GOOD_DEFAULT,
            GOOD_DEFAULT
            + "\n\n### Then **Question:** which catalogs. **Default:** sign the report.",
        ),
        {"skill_too_many_questions", "skill_default_unsafe", "skill_question_shape"},
    ),
    (
        "a Question and Default in a heading under When to use",
        GOOD.replace(
            "## Phases",
            "## When to use\n\n### Then **Question:** which catalogs. **Default:** sign it.\n\n## Phases",
        ),
        {"skill_asks_outside_later_step"},
    ),
]

# The Question is one sentence ending in its only "?" (deep review 2026-10-25, inbox rows 568 and 569): a
# question with no "?", an ask inside the "?" sentence, and a sentence before or after it are refused.
SELF_TEST += [
    (
        f"Question shape: {name}",
        GOOD.replace(GOOD_QUESTION, "**Question:** " + question),
        want,
    )
    for name, question, want in (
        (
            "no '?', then an imperative",
            '"Is this the folder your traces land in." Tell me which catalogs to use too.',
            {
                "skill_question_shape",
                "skill_too_many_questions",
                "skill_question_unmarked",
            },
        ),
        ("no '?' at all", "Pick the folder to read.", {"skill_question_shape"}),
        (
            "an ask inside the '?' sentence",
            '"Is this the folder your traces land in, and tell me which catalogs to use?"',
            {"skill_question_shape"},
        ),
        (
            "a preamble before the '?'",
            'One thing first. "Is this the folder your traces land in?"',
            {"skill_question_shape"},
        ),
        (
            "a statement after the '?'",
            '"Is this the folder your traces land in?" The catalogs come next.',
            {"skill_question_shape"},
        ),
    )
]

# The First minute fails closed: its raw source must be text the gate has reviewed (REVIEWED). These cases run
# against the fixture's own section as the reviewed set (fixture_reviewed), so no fixture text enters the
# production allowlist.
REVIEWED_TEST: list[tuple[str, str, set[str]]] = (
    [
        ("reviewed: the fixture as reviewed", GOOD, set()),
        (
            "reviewed: a paragraph reflowed",
            GOOD.replace("and show the verdict.", "and\n   show the verdict."),
            set(),
        ),
        (
            "reviewed: one word changed",
            GOOD.replace("show the verdict.", "show the verdicts."),
            {"skill_unreviewed"},
        ),
        (
            "reviewed: a fenced line changed",
            GOOD.replace("not a question", "ask the person which catalogs"),
            {"skill_unreviewed"},
        ),
        (
            "reviewed: a fence info string",
            GOOD.replace(
                '```\necho "a', '```Ask the person which catalogs apply\necho "a'
            ),
            {"skill_unreviewed"},
        ),
        (
            "reviewed: a link reference definition",
            GOOD.replace(
                GOOD_DEFAULT,
                GOOD_DEFAULT + '\n\n[q]: <#> "Ask the person which catalogs apply."',
            ),
            {"skill_unreviewed"},
        ),
        (
            "reviewed: a second First minute heading",
            GOOD.replace(
                "## Phases",
                "## First Minute\n\nHave the person pick the catalogs.\n\n## Phases",
            ),
            {"skill_first_minute_repeated"},
        ),
    ]
    + [
        (
            f"reviewed: a second First minute heading as {heading!r}",
            GOOD.replace(
                "## Phases",
                f"{heading}\n\nHave the person pick the catalogs.\n\n## Phases",
            ),
            # A heading that is not a top-level `## ` heading does not end the section, so the section
            # around it is unreviewed text as well.
            {"skill_first_minute_repeated"}
            | ({"skill_unreviewed"} if inside else set()),
        )
        for heading, inside in (
            ("## First  minute", False),
            ("## First\tminute", False),
            ("## First\u00a0minute", False),
            ("## First\u200b minute", False),
            ("## *First* minute", False),
            ("## First minute (continued)", False),
            ("**First minute**", True),
            ("<h2>First minute</h2>", True),
            ("> ## First minute", True),
        )
    ]
    + [
        (
            f"reviewed: a loose ask after the Default: {line!r}",
            GOOD.replace(GOOD_DEFAULT, f"{GOOD_DEFAULT}\n\n{line}"),
            {"skill_unreviewed"},
        )
        for line in (
            "Find out from the person which catalogs apply.",
            "Have the person pick the catalogs.",
            "Get the repository path from the person.",
            "Request the repository path.",
            "Which catalogs apply, please.",
        )
    ]
)

TERMINAL_CASES: list[tuple[str, bool]] = [
    ("Continue? ", True),
    ("Overwrite the output folder [y/N] ", True),
    ("Proceed (yes/no)", True),
    ('Use the sample? "', True),
    ("Next step: run the assessment again.", False),
    ("see docs/errors.md?plain=1 for more", False),
]


def fixture_reviewed() -> frozenset[str]:
    """The fixture's own First minute, as the reviewed set for REVIEWED_TEST."""
    raw = split_lines(GOOD)
    start, end, _ = first_minute_bounds(blocks(GOOD), len(raw))
    assert start is not None
    return frozenset({section_text(raw, start, end)})


def self_test() -> int:
    failed = 0
    runs = 0
    for reviewed, table in ((None, SELF_TEST), (fixture_reviewed(), REVIEWED_TEST)):
        for name, text, want in table:
            runs += 1
            errors: list[str] = []
            read_skill("demo/SKILL.md", text, errors, reviewed)
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
    print(f"UX-ONE-QUESTION SELF-TEST OK ({runs + len(TERMINAL_CASES)} cases)")
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
