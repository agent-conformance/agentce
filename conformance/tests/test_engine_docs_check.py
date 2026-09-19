"""Engine quickstart docs check: the check must fail a page that prescribes a command the engine does not
implement, and an engine that writes fewer assertions than it reports, while passing a page that matches.

The engines are small launcher scripts standing in for a CLI, so each test controls exactly what the
"engine" answers; the real engines are exercised by the CI step that runs the check over both of them.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import engine_docs_check as check
import pytest

FAKE_CLI = textwrap.dedent(
    """
    import json, pathlib, sys

    args = sys.argv[1:]
    mode = pathlib.Path(__file__).with_name("mode").read_text().strip()
    if args[:1] == ["version"]:
        print("agentce 0.0.0")
    elif args[:1] == ["conformance"] and "run" in args:
        out = pathlib.Path(args[args.index("--out") + 1])
        written = 0 if mode == "short" else 3
        for i in range(written):
            target = out / "projects" / f"p{i}" / "assertions.json"
            target.parent.mkdir(parents=True)
            target.write_text("{}" if mode != "empty" else "")
        claim = "partial" if mode == "partial" else "full"
        print(json.dumps({"claim": claim, "projects": {"total": 3, "identical": 3}}))
    elif args[:1] == ["conformance"]:
        print(json.dumps({"error": {"message_key": "input.conformance_action"}}))
        sys.exit(3)
    else:
        print(json.dumps({"error": {"message_key": "cli.not_implemented"}}))
        sys.exit(3)
    """
)

GOOD_DOC = "# Quickstart\n\n```bash\nnode bin/agentce.js conformance run --engine . --corpus c --out o\n```\n"
BAD_DOC = GOOD_DOC + "\n```bash\nnode bin/agentce.js assess --bundle b --out o\n```\n"


KNOWN = frozenset({"assess", "conformance", "report", "version"})


def _engine(tmp_path: Path, doc: str, mode: str = "ok") -> check.Engine:
    (tmp_path / "cli.py").write_text(FAKE_CLI)
    (tmp_path / "mode").write_text(mode)
    page = tmp_path / "quickstart.md"
    page.write_text(doc)
    return check.Engine(
        name="fake",
        doc=page,
        cwd=tmp_path,
        launcher=(sys.executable, str(tmp_path / "cli.py")),
        setup_hint="none",
    )


def test_prescribed_commands_reads_both_launcher_spellings() -> None:
    doc = "node bin/agentce.js conformance run\n build/install/agentce/bin/agentce assess --x\n"
    assert check.prescribed_commands(doc, KNOWN) == ["assess", "conformance"]


@pytest.mark.parametrize(
    "block",
    [
        "node bin/agentce.js \\\n  assess --bundle b --out o",
        "npx @agent-conformance/cli assess --bundle b",
        './gradlew run --args="assess --bundle b --out o"',
        "cd engines/java && build/install/agentce/bin/agentce assess",
        "AGENTCE_X=1 node bin/agentce.js\n  --json\n  report ./out",
    ],
)
def test_a_command_in_a_code_block_is_found_however_it_is_written(block: str) -> None:
    doc = f"# Quickstart\n\n```bash\n{block}\n```\n"
    assert set(check.prescribed_commands(doc, KNOWN)) & {"assess", "report"}


@pytest.mark.parametrize(
    "doc",
    [
        "1. Run it:\n\n   ```bash\n   npx @agent-conformance/cli assess --bundle b\n   ```\n",
        "Run it:\n\n    npx @agent-conformance/cli assess --bundle b\n",
        'Run it:\n\n\t./gradlew run --args="assess --bundle b"\n',
        '- step\n\n  ~~~sh\n  ./gradlew run --args="assess --bundle b"\n  ~~~\n',
        "Then run `npx @agent-conformance/cli assess` on the bundle.\n",
        "````md\n```bash\nnpx @agent-conformance/cli assess\n```\n````\n",
        "```bash\nnpx @agent-conformance/cli assess\n",
    ],
)
def test_a_command_in_an_indented_or_nested_or_unclosed_block_is_found(
    doc: str,
) -> None:
    assert check.prescribed_commands(doc, KNOWN) == ["assess"]


def test_text_after_a_closed_fence_is_prose_again() -> None:
    doc = "```bash\nnode bin/agentce.js conformance run\n```\nThe assess step is planned.\n"
    assert check.prescribed_commands(doc, KNOWN) == ["conformance"]


def test_a_tilde_fence_is_read_like_a_backtick_fence() -> None:
    doc = "~~~sh\nnpx @agent-conformance/cli assess\n~~~\n"
    assert check.prescribed_commands(doc, KNOWN) == ["assess"]


def test_prose_that_only_mentions_command_names_prescribes_nothing() -> None:
    doc = "The `assess`, `report`, and `version` commands are planned; the agentce engine differs.\n"
    assert check.prescribed_commands(doc, KNOWN) == []


def test_a_word_that_merely_contains_a_command_name_is_not_a_command() -> None:
    doc = "```bash\ncat ./out/implementation-report.json ./reports/assess.log\n```\n"
    assert check.prescribed_commands(doc, KNOWN) == []


def test_the_vocabulary_comes_from_the_reference_parser() -> None:
    assert {
        "assess",
        "conformance",
        "report",
        "validate",
        "quickstart",
    } <= check.reference_commands()


def test_a_page_that_matches_the_cli_passes(tmp_path: Path) -> None:
    assert check.check_engine(_engine(tmp_path, GOOD_DOC), tmp_path, KNOWN) == []


def test_a_page_prescribing_a_command_the_engine_rejects_fails(tmp_path: Path) -> None:
    problems = check.check_engine(_engine(tmp_path, BAD_DOC), tmp_path, KNOWN)
    assert len(problems) == 1
    assert "`assess`" in problems[0]
    assert check.NOT_IMPLEMENTED_KEY in problems[0]


def test_a_page_with_no_command_fails(tmp_path: Path) -> None:
    problems = check.check_engine(
        _engine(tmp_path, "# Quickstart\n\nnothing to run\n"), tmp_path, KNOWN
    )
    assert problems == ["fake: the quickstart page prescribes no command to run."]


def test_a_missing_page_fails(tmp_path: Path) -> None:
    engine = _engine(tmp_path, GOOD_DOC)
    engine.doc.unlink()
    assert "does not exist" in check.check_engine(engine, tmp_path, KNOWN)[0]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("short", "0 assertions.json files written"),
        ("empty", "3 assertions.json files are empty"),
        ("partial", "claim is 'partial'"),
    ],
)
def test_conformance_run_output_is_held_to_what_it_reports(
    tmp_path: Path, mode: str, expected: str
) -> None:
    problems = check.check_engine(_engine(tmp_path, GOOD_DOC, mode), tmp_path, KNOWN)
    assert any(expected in p for p in problems), problems


def test_a_launcher_that_cannot_start_is_a_setup_error_not_a_pass(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, GOOD_DOC)
    broken = check.Engine(
        name="fake",
        doc=engine.doc,
        cwd=tmp_path,
        launcher=(str(tmp_path / "does-not-exist"),),
        setup_hint="build it",
    )
    with pytest.raises(check.SetupError):
        check.check_engine(broken, tmp_path, KNOWN)


def test_the_repository_pages_prescribe_commands_for_both_engines() -> None:
    for engine in check.ENGINES.values():
        page = engine.doc.read_text(encoding="utf-8")
        assert check.prescribed_commands(page, check.reference_commands()), engine.name


def test_main_checks_the_page_given_with_doc_and_exits_1_on_a_bad_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path, GOOD_DOC)
    monkeypatch.setitem(check.ENGINES, "typescript", engine)
    bad = tmp_path / "bad.md"
    bad.write_text(BAD_DOC)
    argv = ["--engines", "typescript", "--corpus", str(tmp_path)]
    assert check.main([*argv, "--doc", str(engine.doc)]) == 0
    assert check.main([*argv, "--doc", str(bad)]) == 1


def test_main_refuses_doc_for_several_engines() -> None:
    assert check.main(["--engines", "typescript,java", "--doc", "x.md"]) == 2
