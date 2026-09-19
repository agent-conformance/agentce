"""Tests for agentce doctor and the message-key catalogue (SPEC §13.4, item 4.11)."""

from __future__ import annotations

import json
from pathlib import Path

from agentce import cli
from agentce.error_catalogue import MESSAGE_KEYS, catalogue_gaps, render_errors_md
from agentce.quarantine import QuarantineReason

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"


def test_catalogue_is_complete() -> None:
    assert catalogue_gaps() == []
    for reason in QuarantineReason:
        assert reason.value in MESSAGE_KEYS
    for entry in MESSAGE_KEYS.values():
        assert entry.cause.strip() and entry.fix.strip()


def test_errors_md_lists_every_key() -> None:
    md = render_errors_md()
    for key in MESSAGE_KEYS:
        assert f"`{key}`" in md


def test_committed_errors_md_is_current() -> None:
    committed = (_REPO_ROOT / "docs" / "errors.md").read_text(encoding="utf-8")
    assert committed == render_errors_md()


def test_doctor_healthy_project(capsys) -> None:
    code = cli.main(["doctor", "--project", str(_QUICKSTART), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["healthy"] is True and payload["problems"] == []


def test_doctor_broken_project_names_the_fix(tmp_path: Path, capsys) -> None:
    code = cli.main(["doctor", "--project", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    keys = {p["key"] for p in payload["problems"]}
    assert "input.profile_missing" in keys
    assert all(p["fix"] for p in payload["problems"])


def test_doctor_writes_error_catalogue(tmp_path: Path, capsys) -> None:
    out = tmp_path / "errors.md"
    code = cli.main(["doctor", "--write-errors", str(out), "--json"])
    assert code == 0
    assert out.read_text(encoding="utf-8") == render_errors_md()


def test_failing_command_has_no_traceback(capsys) -> None:
    code = cli.main(["assess", "--bundle", "/no/such/bundle", "--catalog", "x"])
    err = capsys.readouterr()
    assert code == 3
    assert "Traceback (most recent call last)" not in (err.out + err.err)


def test_doctor_agrees_with_init_layout(tmp_path: Path, capsys) -> None:
    project = tmp_path / "my-assessment"
    assert (
        cli.main(
            [
                "init",
                "--non-interactive",
                "--framework",
                "custom-loop",
                "--subject",
                "spiffe://example/agents/t",
                "--role",
                "deployer",
                "--out",
                str(project),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    cli.main(["doctor", "--project", str(project), "--json"])
    keys = {p["key"] for p in json.loads(capsys.readouterr().out)["problems"]}
    # Only the not-yet-collected evidence remains; init wrote every declaration doctor reads.
    assert keys == {"input.bundle_manifest_missing"}


def test_doctor_still_reads_the_flat_quickstart_layout(capsys) -> None:
    cli.main(["doctor", "--project", str(_QUICKSTART), "--json"])
    assert json.loads(capsys.readouterr().out)["healthy"] is True


def test_docs_and_skills_name_the_one_applicability_file() -> None:
    import re

    files = [
        "engines/python/agentce/commands/__init__.py",
        *(
            str(p.relative_to(_REPO_ROOT))
            for tree in (
                "docs",
                "website/src/content/docs",
                "skills/agentce-onboard",
            )
            for p in (_REPO_ROOT / tree).rglob("*")
            if p.is_file() and p.suffix in {".md", ".mdx", ".yaml"}
        ),
    ]
    names = {
        name
        for rel in files
        for name in re.findall(
            r"applicability[-a-zA-Z]*\.yaml", (_REPO_ROOT / rel).read_text("utf-8")
        )
    }
    assert names == {"applicability.yaml"}
