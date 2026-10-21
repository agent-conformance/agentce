"""A user path the engine stats inside a folder that can be listed but not entered (18.169).

Mode 600: ``Path.is_file()``/``is_dir()``/``exists()``/``is_symlink()`` inside such a folder raise
PermissionError on Python 3.12 and 3.13 and return False on 3.14, so each command crashed on one and
read "unreadable" as "absent" on the other. Every site the VG-PATHLIB-PREDICATES baseline grandfathered
on a user path now goes through the ``signing`` stat helpers, and each test below holds the one answer
every Python gives, with the absent path keeping its old answer beside it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from agentce import bundled, cli, signing
from agentce.tools import validate_profile

REPO = Path(__file__).resolve().parents[3]
QS = bundled.quickstart_dir()

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="root enters a mode-600 folder anyway"
)


@contextmanager
def _locked(folder: Path, content: str = "x") -> Iterator[Path]:
    """``folder/f`` holding ``content``, with ``folder`` at mode 600 (listable, not enterable)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "f").write_text(content, encoding="utf-8")
    folder.chmod(0o600)
    try:
        yield folder / "f"
    finally:
        folder.chmod(0o700)


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, Any]:
    code = cli.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def _key(env: dict[str, Any]) -> str:
    return str(env["error"]["key"])


# --- The helpers. ---


def test_path_exists_and_is_link_raise_or_read_absent_by_choice(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to(tmp_path / "nowhere")
    assert signing.is_link(tmp_path / "link") and not signing.path_exists(
        tmp_path / "link"
    )
    assert not signing.path_exists(tmp_path / "absent")
    assert not signing.is_link(tmp_path / "absent")
    with _locked(tmp_path / "L") as inside:
        for helper in (signing.path_exists, signing.is_link):
            with pytest.raises(PermissionError):
                helper(inside)
            assert helper(inside, unreadable_is_absent=True) is False


# --- doctor --project (row 1). ---


def test_doctor_names_unreadable_declarations_and_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "p"
    project.mkdir()
    (project / "agentce").mkdir()
    (project / "evidence").mkdir()
    project.chmod(0o600)
    try:
        code, env = _run(["doctor", "--project", str(project)], capsys)
    finally:
        project.chmod(0o700)
    assert code == 1
    assert {p["key"] for p in env["problems"]} == {
        "input.profile_unreadable",
        "input.bundle_unreadable",
        "input.domain_unreadable",
    }
    assert all("could not be read" in p["problem"] for p in env["problems"])


def test_doctor_absent_declarations_still_read_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = _run(["doctor", "--project", str(tmp_path)], capsys)
    assert code == 1
    assert {p["key"] for p in env["problems"]} == {
        "input.profile_missing",
        "input.bundle_manifest_missing",
        "input.catalog_not_found",
    }


# --- conformance run --corpus (row 2). ---

_CONFORMANCE = ["conformance", "run", "--engine", str(REPO / "engines" / "python")]


@pytest.mark.parametrize("with_generator", [False, True])
def test_conformance_refuses_an_unreadable_corpus_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], with_generator: bool
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    if with_generator:
        (corpus / "generator").symlink_to(REPO / "corpus" / "generator")
    with _locked(tmp_path / "L", "{}") as inside:
        (corpus / "corpus-manifest.json").symlink_to(inside)
        code, env = _run(
            [*_CONFORMANCE, "--corpus", str(corpus), "--out", str(tmp_path / "o")],
            capsys,
        )
    assert (code, _key(env)) == (3, "input.corpus_unreadable")
    assert "corpus-manifest.json" in env["error"]["cause"]


def test_conformance_absent_corpus_is_still_not_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = _run(
        [*_CONFORMANCE, "--corpus", str(tmp_path), "--out", str(tmp_path / "o")],
        capsys,
    )
    assert (code, _key(env)) == (3, "input.corpus_not_found")


# --- agentce.toml in the working folder (row 3). ---


@pytest.mark.parametrize("command", ["config", "assess"])
def test_unreadable_agentce_toml_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    argv = (
        ["config", "show"]
        if command == "config"
        else [
            "assess",
            "--bundle",
            str(QS / "evidence"),
            "--profile",
            str(QS / "applicability.yaml"),
            "--domain",
            str(QS / "domain.linkml.yaml"),
            "--out",
            str(tmp_path / "o"),
        ]
    )
    monkeypatch.chdir(tmp_path)
    with _locked(tmp_path / "L", "[config]\n") as inside:
        (tmp_path / "agentce.toml").symlink_to(inside)
        code, env = _run(argv, capsys)
    assert (code, _key(env)) == (3, "input.config_unreadable")


def test_absent_agentce_toml_still_reads_defaults(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    code, _env = _run(["config", "show"], capsys)
    assert code == 0


# --- catalog lint: a test fixture (row 4). ---


def test_catalog_lint_names_an_unreadable_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cat = tmp_path / "cat"
    assert cli.main(["catalog", "init", str(cat), "--id", "ZZ"]) == 0
    capsys.readouterr()
    passed = (cat / "test" / "ZZ-01" / "passed.jsonl").read_text(encoding="utf-8")
    control = cat / "controls" / "ZZ-01.yaml"
    with _locked(tmp_path / "L", passed):
        control.write_text(
            control.read_text(encoding="utf-8").replace(
                "test/ZZ-01/passed.jsonl", "../L/f"
            ),
            encoding="utf-8",
        )
        assert cli.main(["catalog", "lint", str(cat)]) == 1
    out = capsys.readouterr().out
    assert "fixture ../L/f could not be read" in out
    control.write_text(
        control.read_text(encoding="utf-8").replace("../L/f", "../absent.jsonl"),
        encoding="utf-8",
    )
    assert cli.main(["catalog", "lint", str(cat)]) == 1
    assert "fixture ../absent.jsonl not found" in capsys.readouterr().out


# --- collect: a source's export (row 6). ---


def _collect(tmp_path: Path, export: Path) -> dict[str, Any]:
    config = tmp_path / "c.yaml"
    config.write_text(
        "job:\n  id: j\n  principal: spiffe://corp/jobs/c\nsources:\n"
        f"  - id: urn:otel:x\n    adapter: otel-genai\n    export: {export}\n",
        encoding="utf-8",
    )
    out = tmp_path / "o"
    code = cli.main(
        [
            "collect",
            "--config",
            str(config),
            "--out",
            str(out),
            "--adapters-root",
            str(REPO / "adapters"),
        ]
    )
    assert code == 1
    report = json.loads((out / "collect-report.json").read_text(encoding="utf-8"))
    return dict(report["sources"][0])


def test_collect_reads_an_unreadable_export_as_unreadable(tmp_path: Path) -> None:
    with _locked(tmp_path / "L", "{}") as inside:
        source = _collect(tmp_path, inside)
    assert source["completeness"] == "incomplete"
    assert source["reason"] == f"export could not be read: {inside}"
    absent = _collect(tmp_path, tmp_path / "absent.json")
    assert absent["reason"].startswith("export not found: ")


# --- init --out and catalog init <dir> (row 7). ---


def test_init_out_that_cannot_be_entered_is_unwritable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "o" / "agentce").mkdir(parents=True)
    (tmp_path / "o" / "agentce").chmod(0o600)
    try:
        code, env = _run(["init", "--out", str(tmp_path / "o")], capsys)
    finally:
        (tmp_path / "o" / "agentce").chmod(0o700)
    assert (code, _key(env)) == (3, "input.out_dir_unwritable")


def test_catalog_init_dir_that_cannot_be_entered_is_unwritable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "cat" / "test").mkdir(parents=True)
    (tmp_path / "cat" / "test").chmod(0o600)
    try:
        code, env = _run(
            ["catalog", "init", str(tmp_path / "cat"), "--id", "ZZ"], capsys
        )
    finally:
        (tmp_path / "cat" / "test").chmod(0o700)
    assert (code, _key(env)) == (3, "catalog.init_dir_unwritable")


# --- assess <records folder> --out (row 9). ---


def test_records_out_that_cannot_be_entered_is_unwritable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = tmp_path / "recs"
    records.mkdir()
    (records / "input.json").write_bytes(
        (
            REPO / "adapters" / "otel-genai" / "fixtures" / "langfuse" / "input.json"
        ).read_bytes()
    )
    out = tmp_path / "o"
    out.mkdir()
    out.chmod(0o600)
    try:
        code, env = _run(["assess", str(records), "--out", str(out)], capsys)
    finally:
        out.chmod(0o700)
    assert (code, _key(env)) == (3, "input.out_dir_unwritable")


# --- assess --state (row 11). ---


def test_state_file_that_cannot_be_reached_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "s"
    state.mkdir()
    argv = [
        "assess",
        "--bundle",
        str(QS / "evidence"),
        "--profile",
        str(QS / "applicability.yaml"),
        "--domain",
        str(QS / "domain.linkml.yaml"),
        "--out",
        str(tmp_path / "o"),
        "--state",
        str(state),
    ]
    with _locked(tmp_path / "L", '{"state_version": 1}') as inside:
        (state / "state.json").symlink_to(inside)
        code, env = _run(argv, capsys)
    assert (code, _key(env)) == (3, "input.state_dir_unwritable")


# --- python -m agentce.tools.validate_profile (row 15). ---


def test_validate_profile_tool_names_an_unreadable_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _locked(tmp_path / "L") as inside:
        assert validate_profile.main([str(inside)]) == 1
    assert f"PROFILE INVALID: {inside} could not be read" in capsys.readouterr().err
    assert validate_profile.main([str(tmp_path / "absent.yaml")]) == 1
    assert "is not a file" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text",
    ["{x", "null", "[]", '{"projects": 3}', '{"projects": null}', '{"projects": [3]}'],
)
def test_conformance_refuses_a_corpus_manifest_that_is_not_a_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    # Not JSON, or not an object with a projects array: keyed, never a crash (watch 2026-10-21, 18.184h).
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "corpus-manifest.json").write_text(text, encoding="utf-8")
    code, env = _run(
        [*_CONFORMANCE, "--corpus", str(corpus), "--out", str(tmp_path / "o")], capsys
    )
    assert (code, _key(env)) == (3, "input.corpus_unreadable")
    assert "corpus-manifest.json" in env["error"]["cause"]
