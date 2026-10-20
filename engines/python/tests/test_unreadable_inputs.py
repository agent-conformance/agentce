"""A path the user names that cannot be read, or whose name is too long, is refused with the role's
key, never internal.unexpected (item 18.179)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agentce import bundled, cli

_QS = bundled.quickstart_dir()
_BUNDLE = str(_QS / "evidence")
_PROFILE = str(_QS / "applicability.yaml")

pytestmark = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads a mode 000 file"
)


def _refusal(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = cli.main([argv[0], "--json", *argv[1:]])
    return code, json.loads(capsys.readouterr().out)["error"]["key"]


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (
            ["assess", "--bundle", _BUNDLE, "--profile", "{f}"],
            "input.profile_unreadable",
        ),
        (["readiness", "{r}", "--gaps", "{f}"], "input.gaps_unreadable"),
        (["diff", "{f}", "{f}"], "input.report_a_unreadable"),
        (["report", "--from", "{f}"], "input.from_unreadable"),
        (
            [
                "assess",
                "--bundle",
                _BUNDLE,
                "--profile",
                _PROFILE,
                "--trust-root",
                "{f}",
            ],
            "input.trust_root_unreadable",
        ),
    ],
)
def test_unreadable_file_is_refused_with_the_role_key(
    argv: list[str],
    key: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    locked = tmp_path / "locked.json"
    locked.write_text("[]", encoding="utf-8")
    locked.chmod(0)
    (tmp_path / "r").mkdir()
    filled = [a.format(f=locked, r=tmp_path / "r") for a in argv]
    try:
        assert _refusal(filled, capsys) == (3, key)
    finally:
        locked.chmod(0o600)


def test_overlong_name_is_not_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    long = str(tmp_path / ("a" * 300))
    assert _refusal(["assess", "--bundle", _BUNDLE, "--profile", long], capsys) == (
        3,
        "input.profile_not_a_file",
    )


def test_unenterable_adapters_folder_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    adapters.chmod(0o600)
    root = Path(__file__).resolve().parents[3]
    argv = [
        "conformance",
        "run",
        "--engine",
        str(root / "engines" / "python"),
        "--corpus",
        str(root / "corpus"),
        "--adapters",
        str(adapters),
    ]
    try:
        assert _refusal(argv, capsys) == (3, "input.adapters_unreadable")
    finally:
        adapters.chmod(0o700)


@pytest.mark.parametrize(
    ("target", "key"),
    [
        ("inside", "catalog.sign_key_inside_catalog"),
        ("loop", "input.key_not_a_file"),
    ],
)
def test_catalog_sign_key_link_is_refused_on_every_python(
    target: str, key: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = tmp_path / "cat"
    assert cli.main(["catalog", "init", str(catalog)]) == 0
    capsys.readouterr()
    link = tmp_path / "key.pem"
    if target == "inside":
        link.symlink_to(next(p for p in sorted(catalog.rglob("*")) if p.is_file()))
    else:
        link.symlink_to(link)
    argv = ["catalog", "sign", str(catalog), "--key", str(link)]
    assert _refusal(argv, capsys) == (3, key)


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (
            ["assess", "--bundle", _BUNDLE, "--profile", "{f}/"],
            "input.profile_not_a_file",
        ),
        (
            [
                "assess",
                "--bundle",
                _BUNDLE,
                "--profile",
                _PROFILE,
                "--trust-root",
                "{f}/",
            ],
            "input.trust_root_not_a_file",
        ),
        (["readiness", "{r}", "--gaps", "{f}/"], "input.gaps_not_a_file"),
        (["diff", "{f}/", "{f}"], "input.report_a_not_a_file"),
        (["diff", "{f}", "{f}/"], "input.report_b_not_a_file"),
        (["report", "--from", "{f}/"], "input.from_not_a_file"),
        (["report", "--from", "{f}/."], "input.from_not_a_file"),
    ],
)
def test_file_named_with_a_trailing_slash_is_not_a_file(
    argv: list[str],
    key: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``name/`` resolves only to a directory (POSIX ENOTDIR), so it is refused, never read as the file
    ``name`` that ``Path`` would normalise it to (18.184b)."""
    readable = tmp_path / "file.json"
    readable.write_text("[]", encoding="utf-8")
    (tmp_path / "r").mkdir()
    filled = [a.format(f=readable, r=tmp_path / "r") for a in argv]
    assert _refusal(filled, capsys) == (3, key)


def test_adapters_root_through_a_link_runs_the_folder_it_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``link/../adapters`` names the adapters folder beside the link's target (POSIX), which the engine
    checks; uv reads ``--project`` lexically, so it must get that folder's real path, not the decoy
    beside the link (18.184b)."""
    from agentce import commands

    far = tmp_path / "far"
    (far / "adapters" / "otel-genai").mkdir(parents=True)
    (far / "deep").mkdir()
    (tmp_path / "lnk").symlink_to(far / "deep", target_is_directory=True)
    (tmp_path / "adapters").write_text("a decoy, not a folder\n", encoding="utf-8")
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"events": []}', stderr="")

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    root = tmp_path / "lnk" / ".." / "adapters"
    export = tmp_path / "export.json"
    export.write_text("{}", encoding="utf-8")
    commands._adapt_export(
        root, "otel-genai", export, subject="s", source_class="self_report"
    )
    real = os.path.realpath(far / "adapters")
    assert seen[0][seen[0].index("--project") + 1] == os.path.join(real, "otel-genai")
    assert os.path.join(real, "_ingest.py") in seen[0]
