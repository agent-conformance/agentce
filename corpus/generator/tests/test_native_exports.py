"""Native-format exports per style adapt to canonical events (SPEC §11.2, §12.3, item 2.10).

The generator writes an OTLP/JSON GenAI export for each OpenTelemetry-instrumented style. This test
adapts each with the real otel-genai adapter and confirms it yields schema-valid agent-runtime evidence
(``SessionStart``/``SessionEnd``, ``ModelCall``, ``ToolCall``), deterministically. The projects are
written directly (no full-corpus build, so no 200k-event filler) for speed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentce.canonical import canonical_string
from agentce.schema import validate_event
from agentce_adapters.otel_genai import adapt

import generate

OTEL_STYLES = [
    style for style, _desc, conv in generate.STYLES if conv.startswith("otel-genai:")
]
NON_OTEL_STYLES = [
    style
    for style, _desc, conv in generate.STYLES
    if not conv.startswith("otel-genai:")
]


def _project(tmp_path: Path, style: str, index: int) -> Path:
    generate._write_project(tmp_path, style, "known-pass", index)
    return tmp_path / "projects" / generate.DOMAIN / style / "known-pass"


def test_there_are_four_otel_styles_and_two_others() -> None:
    assert len(OTEL_STYLES) == 4
    assert sorted(NON_OTEL_STYLES) == ["claude-agent-sdk", "custom-loop"]


@pytest.mark.parametrize("style", OTEL_STYLES)
def test_native_exports_adapt_to_agent_runtime_evidence(
    tmp_path: Path, style: str
) -> None:
    proj = _project(tmp_path, style, index=1)
    native = proj / "native" / "otel-genai.otlp.json"
    assert native.is_file(), f"{style} has no native OTLP export"

    result = adapt(native.read_bytes(), subject=f"spiffe://corp/agents/credit-{style}")
    types = sorted(e["data"]["@type"] for e in result.events)
    assert types == ["ModelCall", "SessionEnd", "SessionStart", "ToolCall"]
    for event in result.events:
        assert validate_event(event) == [], event["id"]
    # The tool call the otel export carries is the consequential credit decision tool.
    tool = next(e for e in result.events if e["data"]["@type"] == "ToolCall")
    assert tool["data"]["tool"]["name"] == "credit.record_decision"
    # The recorded convention matches the style's declared version.
    conv = next(c for s, _d, c in generate.STYLES if s == style)
    assert result.report.conventions == (
        f"otel-genai:{conv.split(':', 1)[1].rsplit('.', 1)[0]}",
    )


@pytest.mark.parametrize("style", NON_OTEL_STYLES)
def test_non_otel_styles_have_no_otlp_export(tmp_path: Path, style: str) -> None:
    proj = _project(tmp_path, style, index=2)
    assert not (proj / "native" / "otel-genai.otlp.json").exists()


def test_native_export_is_deterministic(tmp_path: Path) -> None:
    first = (
        _project(tmp_path / "a", "langgraph", index=1)
        / "native"
        / "otel-genai.otlp.json"
    )
    second = (
        _project(tmp_path / "b", "langgraph", index=1)
        / "native"
        / "otel-genai.otlp.json"
    )
    assert first.read_bytes() == second.read_bytes()
    # And adapting twice yields identical canonical events.
    events_a = adapt(first.read_bytes(), subject="s").events
    events_b = adapt(second.read_bytes(), subject="s").events
    assert [canonical_string(e) for e in events_a] == [
        canonical_string(e) for e in events_b
    ]
