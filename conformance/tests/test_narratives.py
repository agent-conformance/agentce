"""The Phase-3 explanation-narrative golden check (P3.3)."""

from __future__ import annotations

import narratives


def test_golden_matches() -> None:
    assert narratives.main(["--check"]) == 0


def test_render_is_deterministic() -> None:
    assert narratives.render_golden() == narratives.render_golden()
