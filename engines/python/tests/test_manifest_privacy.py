"""manifest.json names neither the invoking OS user nor a local filesystem path by default
(SPEC §8.4: ``run.operator`` is "a string the deployer controls"; ``run.invocation`` records
paths, never a person or their home directory).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agentce import cli

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _scan_for_leaks(out_dir: Path, needles: list[str]) -> list[tuple[Path, str]]:
    hits: list[tuple[Path, str]] = []
    for path in out_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for needle in needles:
            if needle and needle in text:
                hits.append((path, needle))
    return hits


def test_quickstart_manifest_has_no_username_or_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare `agentce quickstart --out <dir>` — the first command a newcomer runs — must not
    leak the operator's OS username or home directory into the shared report bundle."""
    out = tmp_path / "out"
    code = cli.main(["quickstart", "--out", str(out)])
    assert code == 0

    username = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    home = str(Path.home())
    hits = _scan_for_leaks(out, [username, home])
    assert hits == [], f"leaked operator identity into shared artifacts: {hits}"

    manifest: dict[str, Any] = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["run"]["operator"] == "unset"
    for arg in manifest["run"]["invocation"]:
        assert home not in arg
        assert not username or username not in arg


def test_assess_with_operator_supplied_absolute_path_under_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not only quickstart's internally-resolved corpus path: an operator who passes their own
    absolute --bundle/--profile path (the real-world case) gets the same scrubbing."""
    fake_home = tmp_path / "home" / "someoperator"
    fake_home.mkdir(parents=True)
    quickstart_link = fake_home / "quickstart"
    quickstart_link.symlink_to(_REPO_ROOT / "corpus" / "quickstart")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    bundle = quickstart_link / "evidence"
    profile = quickstart_link / "applicability.yaml"
    domain = quickstart_link / "domain.linkml.yaml"
    catalog_dir = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
    out = tmp_path / "out"

    code = cli.main(
        [
            "assess",
            "--bundle",
            str(bundle),
            "--profile",
            str(profile),
            "--domain",
            str(domain),
            "--catalog",
            "eu-ai-act@2026.09",
            "--catalog-dir",
            str(catalog_dir),
            "--out",
            str(out),
        ]
    )
    assert code in (0, 1)  # 1 is a real non-conformant finding, not a run failure

    hits = _scan_for_leaks(out, [str(fake_home)])
    assert hits == [], f"leaked the operator-supplied home directory: {hits}"

    manifest: dict[str, Any] = json.loads((out / "manifest.json").read_text("utf-8"))
    for arg in manifest["run"]["invocation"]:
        assert str(fake_home) not in arg
    assert manifest["run"]["invocation"][1].startswith("~/")
    assert manifest["run"]["invocation"][2].startswith("~/")


def test_assess_bundle_outside_home_and_cwd_has_no_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle that lives neither under the home directory nor under the current directory —
    the devcontainer/`/opt`/`/workspaces` checkout case — must still not leak an absolute local
    path into `invocation`; only the final path component is kept."""
    fake_home = tmp_path / "home" / "root"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    workdir = tmp_path / "workspaces" / "agentce"
    workdir.mkdir(parents=True)
    monkeypatch.chdir(workdir)

    elsewhere = tmp_path / "opt" / "checkout"
    quickstart_link = elsewhere / "quickstart"
    quickstart_link.parent.mkdir(parents=True)
    quickstart_link.symlink_to(_REPO_ROOT / "corpus" / "quickstart")

    bundle = quickstart_link / "evidence"
    profile = quickstart_link / "applicability.yaml"
    domain = quickstart_link / "domain.linkml.yaml"
    catalog_dir = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
    out = tmp_path / "out"

    code = cli.main(
        [
            "assess",
            "--bundle",
            str(bundle),
            "--profile",
            str(profile),
            "--domain",
            str(domain),
            "--catalog",
            "eu-ai-act@2026.09",
            "--catalog-dir",
            str(catalog_dir),
            "--out",
            str(out),
        ]
    )
    assert code in (0, 1)

    hits = _scan_for_leaks(out, [str(elsewhere), str(fake_home)])
    assert hits == [], f"leaked a local filesystem path: {hits}"

    manifest: dict[str, Any] = json.loads((out / "manifest.json").read_text("utf-8"))
    for arg in manifest["run"]["invocation"]:
        assert not arg.startswith("/"), f"absolute path leaked into invocation: {arg}"
    assert manifest["run"]["invocation"][1] == "evidence"
    assert manifest["run"]["invocation"][2] == "applicability.yaml"


def test_operator_config_round_trips_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deployer can still set `run.operator` explicitly (SPEC §8.4's own worked example is
    a CI identity string) — it just is not the bare OS user by default."""
    monkeypatch.setenv("AGENTCE_OPERATOR", "ci:github-actions:corp/assessments/assess.yml@refs/heads/main")
    out = tmp_path / "out"
    code = cli.main(["quickstart", "--out", str(out)])
    assert code == 0
    manifest: dict[str, Any] = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["run"]["operator"] == "ci:github-actions:corp/assessments/assess.yml@refs/heads/main"
