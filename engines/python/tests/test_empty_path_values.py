"""An empty path value ('' or --opt=) is refused with the role's key before anything is read or
written, never read as the working directory, the default, or no value (item 18.178)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentce import bundled, cli

_QS = bundled.quickstart_dir()
_BUNDLE = str(_QS / "evidence")
_PROFILE = str(_QS / "applicability.yaml")
_DOMAIN = str(_QS / "domain.linkml.yaml")
# A catalog the vendored trust root verifies, so a dropped --trust-root '' would assess and exit 0 or 2.
_SIGNED = str(bundled.catalogs_dir() / "base" / "eu-ai-act")
_ASSESS_SIGNED = [
    "assess",
    "--bundle",
    _BUNDLE,
    "--profile",
    _PROFILE,
    "--domain",
    _DOMAIN,
    "--catalog-dir",
    _SIGNED,
]


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (["assess", ""], "input.records_not_a_directory"),
        (
            ["assess", "--bundle", "", "--profile", _PROFILE],
            "input.bundle_not_a_directory",
        ),
        (
            ["assess", "--bundle=", "--profile", _PROFILE],
            "input.bundle_not_a_directory",
        ),
        (
            ["assess", "--bundle", _BUNDLE, "--profile", _PROFILE, "--catalog-dir", ""],
            "input.catalog-dir_not_a_directory",
        ),
        (
            ["assess", "--bundle", _BUNDLE, "--profile", _PROFILE, "--out", ""],
            "input.out_dir_unwritable",
        ),
        (
            ["assess", "--bundle", _BUNDLE, "--profile", _PROFILE, "--out="],
            "input.out_dir_unwritable",
        ),
        (
            ["assess", "--bundle", _BUNDLE, "--profile", _PROFILE, "--state", ""],
            "input.state_dir_unwritable",
        ),
        (
            ["assess", "--bundle", _BUNDLE, "--profile", _PROFILE, "--state="],
            "input.state_dir_unwritable",
        ),
        ([*_ASSESS_SIGNED, "--trust-root", ""], "input.trust_root_not_a_file"),
        ([*_ASSESS_SIGNED, "--trust-root="], "input.trust_root_not_a_file"),
        (["quickstart", "--out", ""], "input.out_dir_unwritable"),
        (["sign", ""], "input.report_dir_not_a_directory"),
        (["readiness", ""], "input.report_dir_not_a_directory"),
        (["readiness", "r", "--gaps", ""], "input.gaps_not_a_file"),
        (["readiness", "r", "--deviations="], "input.deviations_not_a_file"),
        (["readiness", "r", "--catalog-dir", ""], "input.catalog-dir_not_a_directory"),
        (["report", "--from", "a.json", "--out", ""], "input.out_file_unwritable"),
        (["catalog", "lint", ""], "input.dir_not_a_directory"),
        (
            ["catalog", "lint", "r", "--support-matrix", ""],
            "input.support_matrix_unwritable",
        ),
        (
            ["catalog", "sign", "r", "--key", "a.json", "--write-trust-root="],
            "input.write_trust_root_unwritable",
        ),
        (["catalog", "init", ""], "catalog.init_dir_unwritable"),
        (["doctor", "--project", ""], "input.project_not_a_directory"),
        (
            [
                "ingest",
                "--in",
                "a.json",
                "--out",
                "b",
                "--adapter",
                "otel-genai",
                "--adapters-root",
                "",
            ],
            "input.adapters_root_not_a_directory",
        ),
    ],
)
def test_empty_path_value_is_refused_with_the_role_key(
    argv: list[str],
    key: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "r").mkdir()
    (tmp_path / "a.json").write_text("[]", encoding="utf-8")
    before = _files(tmp_path)
    monkeypatch.chdir(tmp_path)
    code = cli.main([argv[0], "--json", *argv[1:]])
    error = json.loads(capsys.readouterr().out)["error"]
    assert (code, error["key"]) == (3, key)
    assert _files(tmp_path) == before


def test_signed_catalog_assesses_without_trust_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the --trust-root '' case: the same command with no --trust-root runs the
    assessment, so the refusal above comes from the empty value, not the catalog."""
    monkeypatch.chdir(tmp_path)
    code = cli.main(
        [
            _ASSESS_SIGNED[0],
            "--json",
            *_ASSESS_SIGNED[1:],
            "--out",
            str(tmp_path / "out"),
        ]
    )
    capsys.readouterr()
    assert code in (0, 2)


def test_empty_sign_key_is_not_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """kms `sign --key ''` gets the key an absent key file gets, not the missing-key answer."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["quickstart", "--out", str(tmp_path / "rep")]) == 0
    capsys.readouterr()
    before = _files(tmp_path)
    code = cli.main(
        ["sign", "--json", "rep", "--as", "claimant", "--profile", "kms", "--key", ""]
    )
    error = json.loads(capsys.readouterr().out)["error"]
    assert (code, error["key"]) == (3, "input.key_not_a_file")
    assert _files(tmp_path) == before
