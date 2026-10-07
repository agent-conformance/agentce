"""Doc-accuracy test for `skills/agentce-get-evidence/references/quick-path.md` (18.32 C3).

The reference file makes two checkable factual claims: the vendored `otel-genai` adapter's exact
7-event list, and which of baseline@2026.09's 10 controls land under `blind_spots`/`needed_by` versus
`no_population` on the adapter's own `otel-genai-agent-session` fixture. Both are asserted here against
the real sources (the adapter's own support matrix, and a live `assess` run), so a future adapter
change or catalog change that moves a control between the two groups fails this test rather than
leaving the doc silently wrong.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentce import cli

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC = _REPO_ROOT / "skills" / "agentce-get-evidence" / "references" / "quick-path.md"
_SUPPORT_MATRIX = _REPO_ROOT / "adapters" / "otel-genai" / "support-matrix.yaml"
_FIXTURE = (
    _REPO_ROOT
    / "adapters"
    / "otel-genai"
    / "fixtures"
    / "otel-genai-agent-session"
    / "input.json"
)


def _paragraphs() -> list[str]:
    """Blank-line-delimited blocks with hard line wraps collapsed to single spaces, so a claim that
    spans wrapped markdown lines still reads as one piece of text to search."""
    text = _DOC.read_text(encoding="utf-8")
    return [" ".join(block.splitlines()) for block in text.split("\n\n")]


def _paragraph_containing(needle: str) -> str:
    return next(p for p in _paragraphs() if needle in p)


def test_the_doc_names_exactly_the_adapters_real_seven_events() -> None:
    real_events = set(
        yaml.safe_load(_SUPPORT_MATRIX.read_text(encoding="utf-8"))["events"]
    )
    assert len(real_events) == 7, "the doc's claim of 'exactly seven' depends on this"

    paragraph = _paragraph_containing("maps exactly seven event types")
    before, after = paragraph.split("never", 1)
    doc_events = set(re.findall(r"`([A-Za-z]+)`", before.split(":", 1)[1]))
    assert doc_events == real_events

    never_events = set(re.findall(r"`([A-Za-z]+)`", after))
    assert never_events.isdisjoint(real_events)


def test_the_doc_names_exactly_the_live_blind_spot_grouping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = tmp_path / "records"
    records.mkdir()
    shutil.copy(_FIXTURE, records / "session.json")
    out = tmp_path / "out"
    cli.main(["assess", str(records), "--out", str(out), "--json"])
    capsys.readouterr()

    blind_spots: dict[str, Any] = json.loads(
        (out / "blind-spots.json").read_text(encoding="utf-8")
    )
    needed_by_controls = {
        ref["control"]
        for bs in blind_spots["blind_spots"]
        for ref in bs["needed_by_checks"]
    }
    no_population_controls = {
        entry["control"] for entry in blind_spots["no_population"]
    }

    doc_needed_by = set(
        re.findall(
            r"`([A-Z]+-\d+)`",
            _paragraph_containing("land under `blind_spots`/`needed_by`"),
        )
    )
    doc_no_population = set(
        re.findall(
            r"`([A-Z]+-\d+)`",
            _paragraph_containing("lands under `no_population`"),
        )
    )

    assert (
        doc_needed_by
        == needed_by_controls
        == {
            "INC-01",
            "INC-02",
            "OVS-03",
            "OVS-08",
            "REC-01",
            "REC-04",
            "ROB-02",
            "TRN-03",
        }
    )
    assert doc_no_population == no_population_controls == {"INT-01"}
