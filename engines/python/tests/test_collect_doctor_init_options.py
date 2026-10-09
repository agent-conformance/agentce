"""collect, doctor and init read every option they are given or refuse it with a key (SPEC §8.5,
item 18.112): no option is dropped, and an empty value is never read as the default."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentce import cli

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG = str(_REPO_ROOT / "adapters" / "fixtures" / "collect-dry-run.yaml")


def _run(
    argv: list[str], capsys: pytest.CaptureFixture[str], cwd: Path, monkeypatch
) -> tuple[int, dict]:
    monkeypatch.chdir(cwd)
    code = cli.main([argv[0], "--json", *argv[1:]])
    return code, json.loads(capsys.readouterr().out)


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (["init", "--non-interactive", "--out", "i"], "input.init_unrecognized_flag"),
        (["init", "--non-interactive=1"], "input.init_unrecognized_flag"),
        (["init", "--out", ""], "input.out_dir_unwritable"),
        (["init", "--out="], "input.out_dir_unwritable"),
        (["init", "--subject", "", "--out", "i"], "input.init_value_empty"),
        (["init", "--subject=", "--out", "i"], "input.init_value_empty"),
        (["init", "--framework", "", "--out", "i"], "input.init_value_empty"),
        (["init", "--framework=", "--out", "i"], "input.init_value_empty"),
        (
            ["doctor", "--project", ".", "--write-errors", "e.md"],
            "input.doctor_flag_unused",
        ),
        (
            ["doctor", "--write-errors", "e.md", "--project", "."],
            "input.doctor_flag_unused",
        ),
        (
            ["doctor", "--write-errors", "e.md", "--project", "/nonexistent"],
            "input.doctor_flag_unused",
        ),
        (
            ["doctor", "--project=", "--write-errors", "e.md"],
            "input.doctor_flag_unused",
        ),
        # The pair is checked before the value of --write-errors.
        (
            ["doctor", "--project", ".", "--write-errors", ""],
            "input.doctor_flag_unused",
        ),
        (["doctor", "--write-errors", ""], "input.write_errors_unwritable"),
        (["doctor", "--write-errors="], "input.write_errors_unwritable"),
        (["doctor", "--write-errors", "."], "input.write_errors_unwritable"),
        (
            ["doctor", "--write-errors", "/dev/null/x.md"],
            "input.write_errors_unwritable",
        ),
        (
            ["collect", "--config", _CONFIG, "--dry-run", "--out", ""],
            "input.out_dir_unwritable",
        ),
        (
            ["collect", "--config", _CONFIG, "--dry-run", "--out="],
            "input.out_dir_unwritable",
        ),
        (["collect", "--config", _CONFIG, "--out", ""], "input.out_dir_unwritable"),
        # --out is checked before --adapters-root.
        (
            [
                "collect",
                "--config",
                _CONFIG,
                "--dry-run",
                "--out",
                "",
                "--adapters-root",
                "",
            ],
            "input.out_dir_unwritable",
        ),
        (
            [
                "collect",
                "--config",
                _CONFIG,
                "--dry-run",
                "--adapters-root",
                str(_REPO_ROOT / "adapters"),
            ],
            "input.collect_flag_unused",
        ),
        (
            ["collect", "--config", _CONFIG, "--dry-run", "--adapters-root", ""],
            "input.collect_flag_unused",
        ),
        (
            ["collect", "--config", _CONFIG, "--out", "b", "--adapters-root", ""],
            "input.adapters_root_not_a_directory",
        ),
        (
            ["collect", "--config", _CONFIG, "--out", "b", "--adapters-root="],
            "input.adapters_root_not_a_directory",
        ),
    ],
)
def test_refused_with_a_key_and_nothing_written(
    argv: list[str], key: str, tmp_path: Path, capsys, monkeypatch
) -> None:
    code, env = _run(argv, capsys, tmp_path, monkeypatch)
    assert (code, env["error"]["key"]) == (3, key)
    assert _files(tmp_path) == []


def test_init_without_the_removed_flag_still_writes(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    code, _ = _run(["init", "--out", "i"], capsys, tmp_path, monkeypatch)
    assert code == 0
    assert _files(tmp_path) == [
        "i/agentce/applicability.yaml",
        "i/agentce/domain.linkml.yaml",
    ]


def test_doctor_write_errors_alone_writes_the_catalogue(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    code, _ = _run(
        ["doctor", "--write-errors", "e.md", "--write-errors", "b.md"],
        capsys,
        tmp_path,
        monkeypatch,
    )
    assert code == 0
    assert _files(tmp_path) == ["b.md"]


@pytest.mark.parametrize("out", [["--out", "b"], ["--out=b"]])
def test_dry_run_with_out_writes_the_plan_and_its_manifest(
    out: list[str], tmp_path: Path, capsys, monkeypatch
) -> None:
    code, _ = _run(
        ["collect", "--config", _CONFIG, "--dry-run", *out],
        capsys,
        tmp_path,
        monkeypatch,
    )
    assert code == 0
    assert _files(tmp_path) == ["b/collect-plan.json", "b/manifest.json"]


def test_dry_run_without_out_writes_nothing(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    code, _ = _run(
        ["collect", "--config", _CONFIG, "--dry-run"], capsys, tmp_path, monkeypatch
    )
    assert code == 0
    assert _files(tmp_path) == []


def test_collect_help_says_what_a_dry_run_writes(capsys) -> None:
    assert cli.main(["collect", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "collect-plan.json" in text and "write nothing" not in text
