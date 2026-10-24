"""A run leaves --out as it found it when it is refused for a bad state.json or for a link in --out that
leads outside it (SPEC 8.7, ruling R7; census odd-inputs lines 85, 87 and 89, 18.184s)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agentce import cli

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"


def _run(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = cli.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def _assess(out: Path, *extra: str) -> list[str]:
    return [
        "assess",
        "--bundle",
        str(_QUICKSTART / "evidence"),
        "--profile",
        str(_QUICKSTART / "applicability.yaml"),
        "--domain",
        str(_QUICKSTART / "domain.linkml.yaml"),
        "--out",
        str(out),
        *extra,
    ]


def _files(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


@pytest.mark.parametrize(
    ("content", "key"),
    [
        (b'{"x": "\xff\xfe"}', "input.state_unreadable"),
        (b"{not json", "input.state_unreadable"),
        (b"", "input.state_unreadable"),
        (b"[]", "input.state_unreadable"),
        (b'{"state_version": 1, "state_version": 1}', "input.state_unreadable"),
        (b"{}", "input.state_version_incompatible"),
    ],
    ids=["nonutf8", "malformed", "empty", "array", "duplicate_key", "no_version"],
)
def test_a_bad_state_json_is_refused_before_out_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: bytes, key: str
) -> None:
    state = tmp_path / "st"
    state.mkdir()
    (state / "state.json").write_bytes(content)
    out = tmp_path / "out"
    code, env = _run(_assess(out, "--state", str(state)), capsys)
    assert (code, env["error"]["key"]) == (3, key)
    assert not out.exists() or _files(out) == []


def test_a_state_json_with_a_bom_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "st"
    code, _ = _run(_assess(tmp_path / "o1", "--state", str(first)), capsys)
    assert code == 0
    state_file = first / "state.json"
    state_file.write_bytes(b"\xef\xbb\xbf" + state_file.read_bytes())
    code, _ = _run(_assess(tmp_path / "o2", "--state", str(first)), capsys)
    assert code == 0


def test_the_state_key_wins_over_a_missing_deviations_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "st"
    state.mkdir()
    (state / "state.json").write_bytes(b"{not json")
    argv = _assess(
        tmp_path / "out",
        "--state",
        str(state),
        "--deviations",
        str(tmp_path / "absent.yaml"),
    )
    code, env = _run(argv, capsys)
    assert (code, env["error"]["key"]) == (3, "input.state_unreadable")


_ENTRIES = [
    ("integrity.jsonl", False),
    ("manifest.json", False),
    ("report.md", False),
    ("quarantine.jsonl", False),
    ("claim.json", False),
    ("results.sarif", False),
    ("not-written-by-the-run.txt", False),
    ("packs", True),
    ("skill", True),
]


@pytest.mark.parametrize("command", ["assess", "quickstart"])
@pytest.mark.parametrize(("entry", "folder"), _ENTRIES)
def test_a_link_in_out_leading_outside_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
    entry: str,
    folder: bool,
) -> None:
    outside = tmp_path / "outside"
    if folder:
        outside.mkdir()
        (outside / "kept.txt").write_text("kept\n", encoding="utf-8")
    else:
        outside.write_text("kept\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / entry).symlink_to(outside, target_is_directory=folder)
    argv = _assess(out) if command == "assess" else ["quickstart", "--out", str(out)]
    code, env = _run(argv, capsys)
    assert (code, env["error"]["key"]) == (3, "input.out_link_outside")
    assert entry in env["error"]["cause"]
    assert _files(out) == [entry]
    if folder:
        assert _files(outside) == ["kept.txt"]
    else:
        assert outside.read_text(encoding="utf-8") == "kept\n"


def test_a_dangling_link_pointing_outside_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "integrity.jsonl").symlink_to(tmp_path / "missing.jsonl")
    code, env = _run(["quickstart", "--out", str(out)], capsys)
    assert (code, env["error"]["key"]) == (3, "input.out_link_outside")
    assert not (tmp_path / "missing.jsonl").exists()


def test_a_link_inside_out_is_followed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes.txt").write_text("mine\n", encoding="utf-8")
    (out / "notes-link.txt").symlink_to(out / "notes.txt")
    code, _ = _run(["quickstart", "--out", str(out)], capsys)
    assert code == 0
    assert (out / "notes-link.txt").is_symlink()


@pytest.mark.parametrize("command", ["assess", "quickstart"])
@pytest.mark.parametrize("where", ["unenterable_parent", "name_too_long", "unlistable"])
def test_an_out_that_cannot_be_looked_at_is_unwritable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str, where: str
) -> None:
    """18.184zd: the link guard leaves such an --out to the first write; never internal.unexpected."""
    shut = tmp_path / "shut"
    shut.mkdir()
    if where != "name_too_long" and os.geteuid() == 0:
        pytest.skip("root enters and lists any folder")
    if where == "unenterable_parent":
        out = shut / "out"
    elif where == "unlistable":
        out = shut
        (tmp_path / "outside").write_text("kept\n", encoding="utf-8")
        (out / "integrity.jsonl").symlink_to(tmp_path / "outside")
    else:
        out = tmp_path / ("n" * 300)
    argv = _assess(out) if command == "assess" else ["quickstart", "--out", str(out)]
    shut.chmod(0o300 if where == "unlistable" else 0)
    try:
        code, env = _run(argv, capsys)
    finally:
        shut.chmod(0o755)
    assert (code, env["error"]["key"]) == (3, "input.out_dir_unwritable")
    if where == "unlistable":
        assert (tmp_path / "outside").read_text(encoding="utf-8") == "kept\n"
