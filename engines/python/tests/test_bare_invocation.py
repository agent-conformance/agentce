"""The first commands a newcomer types work with no flags, and doctor's pointer at a bundle works.

``init``, ``doctor`` and ``quickstart`` run bare from an empty directory, an existing profile is never
overwritten silently, and the command ``doctor`` names for a missing evidence bundle is run and
validated for real (SPEC §13.4 AX-1, AX-2, AX-5).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentce import cli

from test_installed_wheel import _build_dists, _clean_env

_REPO_ROOT = Path(__file__).resolve().parents[3]
#: The layout ``init`` writes and ``doctor`` and ``assess`` read: ``<project>/agentce/<file>``.
DECLARATIONS_DIR = "agentce"
PROFILE_FILE = "applicability.yaml"
DOMAIN_FILE = "domain.linkml.yaml"


def _run(
    argv: Sequence[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = cli.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_bare_init_writes_the_starter_project_into_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    code, env = _run(["init"], capsys)
    assert code == 0
    assert (tmp_path / DECLARATIONS_DIR / PROFILE_FILE).is_file()
    assert (tmp_path / DECLARATIONS_DIR / DOMAIN_FILE).is_file()
    assert (env["framework"], env["role"]) == ("custom-loop", "deployer")


def test_init_still_accepts_the_flags_it_used_to_require(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = _run(
        ["init", "--non-interactive", "--role", "provider", "--out", str(tmp_path)],
        capsys,
    )
    assert code == 0 and env["role"] == "provider"
    assert cli.main(["init", "--role", "nobody", "--out", str(tmp_path / "x")]) == 3
    assert not (tmp_path / "x").exists()


def test_init_refuses_to_overwrite_an_edited_profile_unless_forced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(["init", "--out", str(tmp_path)], capsys)[0] == 0
    profile = tmp_path / DECLARATIONS_DIR / PROFILE_FILE
    profile.write_text("# my edits\n", encoding="utf-8")

    code, env = _run(["init", "--out", str(tmp_path)], capsys)
    assert code == 3 and env["error"]["key"] == "input.init_exists"
    assert profile.read_text(encoding="utf-8") == "# my edits\n"

    assert _run(["init", "--force", "--out", str(tmp_path)], capsys)[0] == 0
    assert profile.read_text(encoding="utf-8") != "# my edits\n"


def test_bare_doctor_diagnoses_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _run(["init"], capsys)[0] == 0
    code, env = _run(["doctor"], capsys)
    keys = {problem["key"] for problem in env["problems"]}
    assert Path(env["project"]) == Path(".")
    assert "input.profile_missing" not in keys
    assert "input.catalog_not_found" not in keys
    assert keys == {"input.bundle_manifest_missing"} and code == 1


def test_bare_quickstart_writes_a_report_under_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    code, env = _run(["quickstart"], capsys)
    assert code == 0 and env["quickstart"] == "ok"
    assert (tmp_path / "out" / "assertions.json").is_file()


def test_doctors_bundle_fix_names_a_command_that_builds_a_valid_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = _run(["doctor", "--project", str(tmp_path)], capsys)
    fix = next(
        p["fix"] for p in env["problems"] if p["key"] == "input.bundle_manifest_missing"
    )
    bundle = tmp_path / "evidence"
    assert str(bundle) in fix
    # Run the example command the fix names, aimed at a fresh directory, exactly as printed.
    example = re.search(r"`(examples/[\w./-]+) [^`]*`", fix)
    assert example is not None, fix
    built = tmp_path / "built"
    subprocess.run(
        [str(_REPO_ROOT / example.group(1)), str(built)],
        cwd=tmp_path,
        env=_clean_env(),
        check=True,
        capture_output=True,
    )
    assert cli.main(["validate", "--bundle", str(built)]) == 0


def test_built_wheel_runs_the_first_commands_bare_in_an_empty_directory(
    tmp_path: Path,
) -> None:
    wheel, _ = _build_dists(tmp_path / "dist")
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    workdir = tmp_path / "empty"
    workdir.mkdir()
    env = {**_clean_env(), "PYTHONPATH": str(site)}

    def agentce(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "agentce.cli", *args, "--json"],
            cwd=workdir,
            capture_output=True,
            text=True,
            env=env,
        )

    probe = subprocess.run(
        [sys.executable, "-c", "import agentce; print(agentce.__file__)"],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=env,
    )
    assert Path(probe.stdout.strip()).is_relative_to(site), probe.stdout + probe.stderr

    init = agentce("init")
    assert init.returncode == 0, init.stderr[-600:]
    assert (workdir / DECLARATIONS_DIR / PROFILE_FILE).is_file()

    doctor = agentce("doctor")
    problems = {p["key"] for p in json.loads(doctor.stdout)["problems"]}
    assert problems == {"input.bundle_manifest_missing"}, doctor.stderr[-600:]

    quickstart = agentce("quickstart")
    assert quickstart.returncode == 0, quickstart.stderr[-600:]
    assert (workdir / "out" / "assertions.json").is_file()
