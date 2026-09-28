"""The base catalog lints clean, and lint catches a broken control or fixture.

Also covers ``agentce catalog init`` (18.9 C1): a from-scratch, lint-clean, ``mode: automated``
scaffold, the guided path's first step.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from agentce import cli, commands
from agentce.catalog import lint_catalog, load_catalog
from agentce.domain import DomainBinding
from agentce.graph import build_graph
from agentce.structural import evaluate_control

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"


def _run(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def _scaffold_files(directory: Path, family: str) -> list[Path]:
    return [
        directory / "catalog.yaml",
        directory / "controls" / f"{family}-01.yaml",
        directory / "shapes" / f"{family}-01.ttl",
        directory / "test" / "domain.yaml",
        directory / "test" / f"{family}-01" / "passed.jsonl",
        directory / "test" / f"{family}-01" / "failed.jsonl",
        directory / "test" / f"{family}-01" / "inapplicable.jsonl",
    ]


def test_base_catalog_lints_clean() -> None:
    assert lint_catalog(_BASE) == []


def test_load_base_catalog() -> None:
    catalog = load_catalog(_BASE)
    ids = {control.id for control in catalog.controls}
    assert {"OVS-03", "REC-04", "INT-01", "INC-02"} <= ids


def test_vendored_control_schema_matches_spec() -> None:
    vendored = (
        _REPO_ROOT / "engines/python/agentce/data/schemas/control.schema.json"
    ).read_text(encoding="utf-8")
    spec = (_REPO_ROOT / "spec/rules/control.schema.json").read_text(encoding="utf-8")
    assert vendored == spec


def test_lint_detects_broken_fixture(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "cat"
    shutil.copytree(_BASE, catalog_dir)
    fixture = catalog_dir / "test" / "OVS-03" / "passed.jsonl"
    # Break the human chain terminus so the "passed" fixture no longer passes.
    fixture.write_text(
        fixture.read_text(encoding="utf-8")
        .replace('"kind": "human"', '"kind": "service"')
        .replace('"kind":"human"', '"kind":"service"'),
        encoding="utf-8",
    )
    problems = lint_catalog(catalog_dir)
    assert any("OVS-03" in problem for problem in problems)


def test_lint_detects_schema_violation(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "cat"
    shutil.copytree(_BASE, catalog_dir)
    control = catalog_dir / "controls" / "INT-01.yaml"
    control.write_text(
        control.read_text(encoding="utf-8").replace(
            "severity: high", "severity: catastrophic"
        ),
        encoding="utf-8",
    )
    problems = lint_catalog(catalog_dir)
    assert any("INT-01" in problem and "schema" in problem for problem in problems)


# --- 18.9 C1: `agentce catalog init` (contracts/P18-18.9.md). ---


def test_catalog_init_writes_a_lint_clean_scaffold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "own-rules-cat"
    code, env = _run(["catalog", "init", str(directory), "--json"], capsys)
    assert code == 0, env
    assert env["family"] == "OWNRULESCAT"[:4]
    for path in _scaffold_files(directory, env["family"]):
        assert path.is_file(), path
    assert sorted(env["files"]) == sorted(
        str(p) for p in _scaffold_files(directory, env["family"])
    )
    assert lint_catalog(directory) == []


def test_catalog_init_fixtures_reproduce_the_claimed_outcomes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The three written fixtures reproduce conformant/non-conformant/conformant(applicable=0)
    exactly as ``_lint_case`` checks -- not merely "lint found no problem", the exact outcome class
    each fixture is meant to demonstrate."""
    directory = tmp_path / "cat"
    code, env = _run(["catalog", "init", str(directory), "--json"], capsys)
    assert code == 0, env
    family = env["family"]
    catalog = load_catalog(directory)
    control = catalog.controls[0]
    shape = catalog.shape_for(control)
    assert shape is not None
    domain = DomainBinding.load(directory / "test" / "domain.yaml")

    def _outcome(fixture_name: str) -> tuple[str, int]:
        fixture = directory / "test" / f"{family}-01" / fixture_name
        events = [
            json.loads(line)
            for line in fixture.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        store = build_graph(events, domain=domain)
        result = evaluate_control(
            store,
            shape,
            catalog.shapes,
            control_id=control.id,
            tolerance=control.tolerance,
        )
        return result.outcome, result.applicable

    passed_outcome, passed_applicable = _outcome("passed.jsonl")
    assert passed_outcome == "conformant" and passed_applicable > 0
    failed_outcome, _ = _outcome("failed.jsonl")
    assert failed_outcome == "non-conformant"
    inapplicable_outcome, inapplicable_applicable = _outcome("inapplicable.jsonl")
    assert inapplicable_outcome == "conformant" and inapplicable_applicable == 0


def test_catalog_init_default_id_for_dot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "MyOwnProject"
    project.mkdir()
    monkeypatch.chdir(project)
    code, env = _run(["catalog", "init", ".", "--json"], capsys)
    assert code == 0, env
    assert env["family"] == "MYOW"


def test_catalog_init_default_id_falls_back_for_punctuation_only_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "___"
    code, env = _run(["catalog", "init", str(directory), "--json"], capsys)
    assert code == 0, env
    assert env["family"] == "GEN"


def test_catalog_init_rejects_an_invalid_family(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for bad_id in ("ab", "TOOLONG", "A1"):
        directory = tmp_path / f"cat-{bad_id}"
        code, env = _run(
            ["catalog", "init", str(directory), "--id", bad_id, "--json"], capsys
        )
        assert code == 3, (bad_id, env)
        assert env["error"]["key"] == "catalog.init_family_invalid"
        assert not directory.exists()


@pytest.mark.parametrize(
    "family",
    ["REC", "CND"],
    ids=["base-catalog-family", "overlay-catalog-family"],
)
def test_catalog_init_rejects_a_vendored_family_collision(
    family: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REC comes from a base catalog, CND from an overlay -- both must collide, since
    `_vendored_control_families` reads every vendored catalog `bundled.vendored_catalogs()` resolves,
    not just the base three."""
    directory = tmp_path / "cat"
    code, env = _run(
        ["catalog", "init", str(directory), "--id", family, "--json"], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.init_family_collision"
    assert not directory.exists()


def test_catalog_init_exists_guard_and_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "cat"
    code, env = _run(
        ["catalog", "init", str(directory), "--id", "GEN", "--json"], capsys
    )
    assert code == 0, env
    before = (directory / "catalog.yaml").read_bytes()

    code, env = _run(
        ["catalog", "init", str(directory), "--id", "GEN", "--json"], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.init_exists"
    assert (directory / "catalog.yaml").read_bytes() == before

    code, env = _run(
        ["catalog", "init", str(directory), "--id", "GEN", "--force", "--json"], capsys
    )
    assert code == 0, env
    assert lint_catalog(directory) == []


def test_catalog_init_title_and_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as _datetime

    import yaml

    class _FrozenDate(_datetime.date):
        @classmethod
        def today(cls) -> "_FrozenDate":
            return cls(2031, 3, 7)

    monkeypatch.setattr(commands, "date", _FrozenDate)
    directory = tmp_path / "cat"
    code, env = _run(
        [
            "catalog",
            "init",
            str(directory),
            "--id",
            "GEN",
            "--title",
            "My Own Rules",
            "--json",
        ],
        capsys,
    )
    assert code == 0, env
    assert env["version"] == "2031.03"
    assert env["title"] == "My Own Rules"
    catalog_doc = yaml.safe_load(
        (directory / "catalog.yaml").read_text(encoding="utf-8")
    )
    assert catalog_doc["version"] == "2031.03"
    assert catalog_doc["title"] == "My Own Rules"
    control_doc = yaml.safe_load(
        (directory / "controls" / "GEN-01.yaml").read_text(encoding="utf-8")
    )
    assert control_doc["version"] == "2031.03"
