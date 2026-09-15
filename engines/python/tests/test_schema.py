"""The vendored evidence schema and event validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentce import schema

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VENDORED = (
    _REPO_ROOT
    / "engines"
    / "python"
    / "agentce"
    / "data"
    / "agentce-evidence.schema.json"
)
_GENERATED = (
    _REPO_ROOT
    / "spec"
    / "model"
    / "generated"
    / "json-schema"
    / "agentce-evidence.schema.json"
)


def test_vendored_schema_matches_generated() -> None:
    # The engine vendors the generated schema so it can validate events when installed; a drift would
    # let it accept events the model rejects, so the copy must stay byte-identical.
    assert _VENDORED.read_text(encoding="utf-8") == _GENERATED.read_text(
        encoding="utf-8"
    )


def test_event_types_include_known_types() -> None:
    types = schema.event_types()
    assert {
        "ToolCall",
        "Decision",
        "ApprovalDecided",
        "Instruction",
        "Refusal",
    } <= types


def test_valid_example_event_passes(example_event: dict[str, Any]) -> None:
    assert schema.validate_event(example_event) == []


def test_invalid_event_reports_errors() -> None:
    assert schema.validate_event({"id": "x"})  # missing required envelope fields
