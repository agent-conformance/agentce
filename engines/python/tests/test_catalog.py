"""The base catalog lints clean, and lint catches a broken control or fixture."""

from __future__ import annotations

import shutil
from pathlib import Path

from agentce.catalog import lint_catalog, load_catalog

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"


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
