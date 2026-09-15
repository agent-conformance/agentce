"""The three ``verify`` targets: --bundle, --catalog, and --release."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentce import cli


def run(
    argv: Sequence[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_verify_catalog(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["verify", "--catalog", str(tmp_path), "--json"], capsys)
    assert code == 0
    assert env["catalog"] == str(tmp_path)


def test_verify_release(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact = tmp_path / "release.tar.gz"
    artifact.write_text("x", encoding="utf-8")
    code, env = run(["verify", "--release", str(artifact), "--json"], capsys)
    assert code == 0
    assert env["release"] == str(artifact)


def test_verify_release_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(
        ["verify", "--release", str(tmp_path / "nope.tgz"), "--json"], capsys
    )
    assert code == 3
    assert env["error"]["key"] == "input.release_not_a_file"


def test_verify_two_targets_is_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(
        ["verify", "--bundle", str(tmp_path), "--catalog", str(tmp_path), "--json"],
        capsys,
    )
    assert code == 3
    assert env["error"]["key"] == "input.verify_target"


def test_report_default_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "assertions.json"
    src.write_text("{}", encoding="utf-8")
    code, env = run(["report", "--from", str(src), "--json"], capsys)
    assert code == 0
    assert env["format"] == "md"
