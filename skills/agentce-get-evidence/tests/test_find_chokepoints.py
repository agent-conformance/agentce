"""find_chokepoints: golden inventories across the six corpus styles (SPEC 13.3.3, 13.3.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import find_chokepoints

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
GOLDENS = ROOT / "tests" / "goldens"
STYLES = [
    "claude-agent-sdk",
    "crewai",
    "custom-loop",
    "google-adk",
    "langgraph",
    "openai-agents",
]


def test_the_six_styles_have_fixtures_and_goldens() -> None:
    assert sorted(p.name for p in FIXTURES.iterdir() if p.is_dir()) == STYLES
    assert sorted(p.stem for p in GOLDENS.glob("*.json")) == STYLES


@pytest.mark.parametrize("style", STYLES)
def test_chokepoints_match_the_golden(style: str) -> None:
    golden = json.loads((GOLDENS / f"{style}.json").read_text(encoding="utf-8"))
    produced = find_chokepoints.scan(FIXTURES / style)
    assert produced == golden["chokepoints"], style


@pytest.mark.parametrize("style", STYLES)
def test_inventory_is_sorted_and_nonempty(style: str) -> None:
    entries = find_chokepoints.scan(FIXTURES / style)
    assert entries
    assert entries == sorted(entries, key=lambda e: (e["path"], e["line"], e["kind"]))


def test_output_minimises_content() -> None:
    # SPEC S-9: entries carry only path, line, kind, confidence, enforcement flag -- never code content.
    allowed = {"path", "line", "kind", "confidence", "enforcement_point_present"}
    for style in STYLES:
        for entry in find_chokepoints.scan(FIXTURES / style):
            assert set(entry) == allowed


def test_enforcement_point_detected_where_a_gateway_is_present() -> None:
    # openai-agents routes tools through the MCP gateway; the tool chokepoints are enforcement-aware.
    tools = [
        e
        for e in find_chokepoints.scan(FIXTURES / "openai-agents")
        if e["kind"] == "tool_call"
    ]
    assert tools and all(e["enforcement_point_present"] for e in tools)


def test_a_repo_with_no_chokepoints_is_a_finding(tmp_path: Path) -> None:
    (tmp_path / "empty.py").write_text("x = 1\n", encoding="utf-8")
    assert (
        find_chokepoints.main(["--repo", str(tmp_path), "--json"])
        == find_chokepoints.FINDINGS
    )


def test_missing_repo_is_an_input_error(tmp_path: Path) -> None:
    assert (
        find_chokepoints.main(["--repo", str(tmp_path / "nope")])
        == find_chokepoints.INPUT_ERROR
    )
