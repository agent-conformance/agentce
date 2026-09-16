"""Catalog provenance block and the ``--require-provenance`` lint (SPEC §14.5 CP-3, item 5.9)."""

from __future__ import annotations

from pathlib import Path

from agentce import catalog
from agentce.catalog import catalog_provenance_digest, lint_catalog

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"


def test_base_catalog_provenance_is_valid() -> None:
    assert lint_catalog(_BASE, require_provenance=True) == []


def test_missing_provenance_only_matters_when_required(tmp_path: Path) -> None:
    (tmp_path / "controls").mkdir()
    (tmp_path / "controls" / "A.yaml").write_text("x: 1\n", encoding="utf-8")
    problems = catalog._provenance_problems(tmp_path, {"version": "1"})
    assert problems == ["catalog.yaml: missing provenance block (SPEC §14.5 CP-3)"]


def test_digest_excludes_signature_and_catalog_yaml(tmp_path: Path) -> None:
    (tmp_path / "controls").mkdir()
    (tmp_path / "controls" / "A.yaml").write_text("x: 1\n", encoding="utf-8")
    before = catalog_provenance_digest(tmp_path)
    # Neither the signature nor catalog.yaml is part of the provenance digest.
    (tmp_path / "catalog.yaml").write_text("id: whatever\n", encoding="utf-8")
    (tmp_path / "catalog.sig.json").write_text("{}\n", encoding="utf-8")
    assert catalog_provenance_digest(tmp_path) == before
    # A change to a rule does move the digest.
    (tmp_path / "controls" / "B.yaml").write_text("y: 2\n", encoding="utf-8")
    assert catalog_provenance_digest(tmp_path) != before


def test_wrong_provenance_values_are_flagged(tmp_path: Path) -> None:
    (tmp_path / "controls").mkdir()
    (tmp_path / "controls" / "A.yaml").write_text("x: 1\n", encoding="utf-8")
    good = catalog_provenance_digest(tmp_path)
    meta_bad = {
        "version": "1",
        "provenance": {"source": "  ", "version": "2", "digest": "sha256:00"},
    }
    problems = catalog._provenance_problems(tmp_path, meta_bad)
    assert any("source" in p for p in problems)
    assert any("version" in p for p in problems)
    assert any("digest" in p for p in problems)
    meta_ok = {
        "version": "1",
        "provenance": {"source": "https://example/x", "version": "1", "digest": good},
    }
    assert catalog._provenance_problems(tmp_path, meta_ok) == []
