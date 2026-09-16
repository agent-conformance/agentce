"""Tests for the crosswalk verification-flag lint (SPEC §7.3 B14, item 4.8)."""

from __future__ import annotations

from pathlib import Path

from agentce.catalog import lint_catalog

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"


def _catalog(tmp_path: Path, *, with_flag: bool) -> Path:
    directory = tmp_path / "cat"
    (directory / "controls").mkdir(parents=True)
    (directory / "catalog.yaml").write_text(
        'id: c\nversion: "2026.09"\nfamilies: [ZZZ]\ncontrols: []\n', encoding="utf-8"
    )
    (directory / "test").mkdir()
    (directory / "test" / "p.jsonl").write_text("", encoding="utf-8")
    flag = ", verified_against_text: false" if with_flag else ""
    (directory / "controls" / "ZZZ-01.yaml").write_text(
        f'id: ZZZ-01\nversion: "2026.09"\ntitle: t\n'
        f"crosswalk:\n  - {{framework: eu-ai-act, clause: X, relation: implements{flag}}}\n"
        "applicability:\n  applies_to_roles: [both]\n"
        "evaluation:\n  mode: manual\n  rung: 3\n  min_source_class: self_report\n"
        "  minimum_evidence:\n    - {event: Decision, class: self_report}\n"
        "expectations:\n  - {id: S1, text: t}\nseverity: medium\nevidence_strength: partial\n"
        "test_cases:\n  - {id: p, expected: not_assessed, fixture: test/p.jsonl}\n",
        encoding="utf-8",
    )
    return directory


def test_missing_flag_is_a_problem_only_when_required(tmp_path: Path) -> None:
    directory = _catalog(tmp_path, with_flag=False)
    assert lint_catalog(directory) == []  # not required by default
    problems = lint_catalog(directory, require_verification_flags=True)
    assert any("verified_against_text" in p for p in problems)


def test_present_flag_lints_clean_when_required(tmp_path: Path) -> None:
    directory = _catalog(tmp_path, with_flag=True)
    assert lint_catalog(directory, require_verification_flags=True) == []


def test_base_catalog_carries_every_flag() -> None:
    assert lint_catalog(_BASE, require_verification_flags=True) == []
