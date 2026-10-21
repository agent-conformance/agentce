"""The vendored evidence schema and event validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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


_MODEL_CALL = (
    '{"agentcesourceclass": "self_report", "data": {"@context": "https://agent-conformance.org/contexts/evidence/v1", "@type": "ModelCall", '
    '"agent": {"id": "spiffe://corp/agents/a"}, "model": {"name": "m", "provider": "p", "version_or_digest": "1"}, '
    '"operation": "chat", "usage": {"input_tokens": USAGE, "output_tokens": 10}}, '
    '"datacontenttype": "application/ld+json", "id": "mc1", "source": "urn:src:agent", "specversion": "1.0", '
    '"subject": "spiffe://corp/agents/a", "time": "2026-01-02T00:00:01Z", '
    '"type": "org.agent-conformance.evidence.ModelCall.v1"}'
)


@pytest.mark.parametrize(
    "usage",
    [
        "5000.0",
        "1.0",
        "5e3",
        "5E+3",
        "5000.5",
        "-0.0",
        "1e400",
        "9007199254740992",
        "-9007199254740992",
    ],
)
def test_usage_count_that_is_not_a_whole_number_token_is_invalid(usage: str) -> None:
    # 18.184j: as TypeScript reads it, so a record holding one is quarantined schema_invalid in every engine.
    errors = schema.validate_event(json.loads(_MODEL_CALL.replace("USAGE", usage)))
    assert errors


@pytest.mark.parametrize("usage", ["5000", "0", "-0", "null", "9007199254740991"])
def test_usage_count_written_as_a_whole_number_is_valid(usage: str) -> None:
    assert schema.validate_event(json.loads(_MODEL_CALL.replace("USAGE", usage))) == []
