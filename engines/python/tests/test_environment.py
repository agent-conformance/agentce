"""Tests for the interpreter and toolchain preflight ``agentce doctor`` reports (SPEC §13.4 AX-5)."""

from __future__ import annotations

import json
import sys
from importlib import metadata
from pathlib import Path

import pytest

from agentce import cli, environment
from agentce.environment import inspect_environment, wheel_source

_QUICKSTART = Path(__file__).resolve().parents[3] / "corpus" / "quickstart"


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["cp311-abi3-manylinux_2_28_x86_64"], "prebuilt"),
        (
            ["cp311-abi3-manylinux2014_aarch64", "cp311-abi3-manylinux_2_17_aarch64"],
            "prebuilt",
        ),
        (["cp311-abi3-musllinux_1_2_x86_64"], "prebuilt"),
        (["cp311-abi3-macosx_11_0_arm64"], "prebuilt"),
        (["cp311-abi3-win_amd64"], "prebuilt"),
        # No prebuilt wheel is published for macOS x86_64, so this tag can only be a local build.
        (["cp312-abi3-macosx_10_12_x86_64"], "source-build"),
        (["cp312-abi3-linux_x86_64"], "source-build"),
        (["cp312-abi3-win32"], "source-build"),
        # One prebuilt tag among source-built ones is not enough.
        (
            ["cp311-abi3-macosx_11_0_arm64", "cp311-abi3-macosx_10_12_x86_64"],
            "source-build",
        ),
        ([], "source-build"),
    ],
)
def test_wheel_source_classifies_platform_tags(tags: list[str], expected: str) -> None:
    assert wheel_source(tags) == expected


class _FakeDist:
    version = "50.0.1"

    def __init__(self, wheel_text: str | None) -> None:
        self._wheel_text = wheel_text

    def read_text(self, name: str) -> str | None:
        return self._wheel_text if name == "WHEEL" else None


def _install(monkeypatch: pytest.MonkeyPatch, dist: object) -> None:
    monkeypatch.setattr(metadata, "distribution", lambda name: dist)
    monkeypatch.setattr(environment, "_openssl_version", lambda: "OpenSSL 3.5.8")


def test_source_build_is_a_note_with_the_exact_fix_not_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        _FakeDist("Wheel-Version: 1.0\nTag: cp312-abi3-macosx_10_12_x86_64\n"),
    )
    section, problems = inspect_environment()
    assert problems == []
    assert section["cryptography"]["wheel_source"] == "source-build"
    (note,) = section["notes"]
    assert note["key"] == "environment.cryptography_source_build"
    assert "Rust" in note["fix"] and "OPENSSL_DIR" in note["fix"]


def test_prebuilt_wheel_produces_no_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeDist("Tag: cp311-abi3-manylinux_2_28_x86_64\n"))
    section, problems = inspect_environment()
    assert problems == [] and section["notes"] == []
    assert section["cryptography"]["wheel_source"] == "prebuilt"


def test_missing_cryptography_is_a_problem_naming_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def absent(name: str) -> object:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "distribution", absent)
    section, problems = inspect_environment()
    (problem,) = problems
    assert problem["key"] == "environment.cryptography_unavailable"
    assert "uv sync" in problem["fix"]
    assert section["cryptography"]["version"] is None


def test_installed_but_unimportable_cryptography_is_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        metadata, "distribution", lambda name: _FakeDist("Tag: cp311-abi3-win_amd64\n")
    )
    monkeypatch.setattr(environment, "_openssl_version", lambda: None)
    _, problems = inspect_environment()
    assert [p["key"] for p in problems] == ["environment.cryptography_unavailable"]


def test_interpreter_below_the_minimum_is_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeDist("Tag: cp311-abi3-win_amd64\n"))
    monkeypatch.setattr(environment, "MINIMUM_PYTHON", (99, 0))
    section, problems = inspect_environment()
    assert section["python"]["supported"] is False
    assert [p["key"] for p in problems] == ["environment.python_unsupported"]


def test_doctor_reports_the_running_interpreter_and_the_real_wheel_tag(capsys) -> None:
    code = cli.main(["doctor", "--project", str(_QUICKSTART), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    env = payload["environment"]
    info = sys.version_info
    assert env["python"]["version"] == f"{info.major}.{info.minor}.{info.micro}"
    installed = metadata.distribution("cryptography").read_text("WHEEL") or ""
    assert env["cryptography"]["wheel_tags"] == [
        line.split(":", 1)[1].strip()
        for line in installed.splitlines()
        if line.startswith("Tag:")
    ]
    assert env["cryptography"]["wheel_source"] in ("prebuilt", "source-build")
    assert env["cryptography"]["openssl"]


def test_doctor_prints_the_source_build_advice_in_text_mode(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install(monkeypatch, _FakeDist("Tag: cp312-abi3-macosx_10_12_x86_64\n"))
    cli.main(["doctor", "--project", str(_QUICKSTART)])
    out = capsys.readouterr()
    assert "environment.cryptography_source_build" in out.out + out.err
