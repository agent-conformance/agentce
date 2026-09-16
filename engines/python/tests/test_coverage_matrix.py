"""Tests for the automation coverage matrix (SPEC §7.5)."""

from __future__ import annotations

from pathlib import Path

from agentce import cli
from agentce.coverage_matrix import automation_matrix, check_matrix, write_matrix

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"


def _tiny_catalog(tmp_path: Path) -> Path:
    directory = tmp_path / "cat"
    (directory / "controls").mkdir(parents=True)
    (directory / "catalog.yaml").write_text(
        'id: tiny\nversion: "2026.09"\nfamilies: [AAA, BBB]\ncontrols: []\n',
        encoding="utf-8",
    )

    def control(cid: str, mode: str) -> str:
        return (
            f'id: {cid}\nversion: "2026.09"\ntitle: t\n'
            "crosswalk:\n  - {framework: eu-ai-act, clause: X, relation: implements}\n"
            "applicability:\n  applies_to_roles: [both]\n"
            f"evaluation:\n  mode: {mode}\n  rung: 3\n  min_source_class: self_report\n"
            "  minimum_evidence:\n    - {event: Decision, class: self_report}\n"
            "expectations:\n  - {id: S1, text: t}\nseverity: medium\nevidence_strength: partial\n"
            "test_cases:\n  - {id: p, expected: not_assessed, fixture: x}\n"
        )

    (directory / "controls" / "AAA-01.yaml").write_text(
        control("AAA-01", "automated"), encoding="utf-8"
    )
    (directory / "controls" / "AAA-02.yaml").write_text(
        control("AAA-02", "manual"), encoding="utf-8"
    )
    (directory / "controls" / "BBB-01.yaml").write_text(
        control("BBB-01", "semi-automated"), encoding="utf-8"
    )
    return directory


def test_matrix_counts_by_family_and_mode(tmp_path: Path) -> None:
    matrix = automation_matrix(_tiny_catalog(tmp_path))
    assert "| AAA | 2 | 1 | 0 | 1 |" in matrix
    assert "| BBB | 1 | 0 | 1 | 0 |" in matrix
    assert "| **Total** | 3 | 1 | 1 | 1 |" in matrix


def test_check_matrix_detects_stale_and_missing(tmp_path: Path) -> None:
    directory = _tiny_catalog(tmp_path)
    ok, message = check_matrix(directory)
    assert not ok and "no committed matrix" in message
    write_matrix(directory)
    ok, message = check_matrix(directory)
    assert ok and message == "MATRIX OK"
    (directory / "automation-coverage.md").write_text("stale\n", encoding="utf-8")
    ok, _ = check_matrix(directory)
    assert not ok


def test_committed_base_matrix_is_current() -> None:
    ok, message = check_matrix(_BASE)
    assert ok, message


def test_cli_coverage_matrix_check(capsys) -> None:
    import json

    code = cli.main(["catalog", "coverage-matrix", str(_BASE), "--check", "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["matrix_ok"] is True
