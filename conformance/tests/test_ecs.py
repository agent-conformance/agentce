"""Two-engine byte-identity over the full corpus (SPEC §11.5, P3.4): both engines claim full and every
project's assertions.json is identical. Skipped where the TypeScript engine (pnpm) is unavailable."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import corpus.generator.generate as generate
import ecs

pytestmark = pytest.mark.skipif(
    shutil.which("pnpm") is None, reason="the TypeScript engine (pnpm) is not available"
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def small_filler(monkeypatch: pytest.MonkeyPatch) -> None:
    # The high-volume project dominates the run; a small filler keeps the two-engine comparison quick.
    monkeypatch.setattr(generate, "FILLER_EVENTS", 30)


def test_two_engines_are_byte_identical(small_filler: None) -> None:
    result = ecs.run_two_engine(REPO_ROOT / "corpus")
    assert result["python"] == "full"
    assert result["ts"] == "full"
    assert result["projects_total"] == 150
    assert result["projects_identical"] == 150, (
        f"differing projects: {result['different']}"
    )
    assert result["different"] == []
