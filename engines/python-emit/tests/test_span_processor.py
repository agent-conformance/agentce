"""AgentCESpanProcessor and instrument() (SPEC 13.1, 13.4 AX-3): hooking a framework's own
OTel-shaped instrumentation and turning it into canonical AgentCE evidence."""

from __future__ import annotations

from pathlib import Path

import pytest
from agentce.bundle import load_bundle
from agentce.ingest import ingest

import agentce_emit
from agentce_emit import AgentCESpanProcessor, Span


def _emitter(tmp_path: Path) -> agentce_emit.Emitter:
    return agentce_emit.Emitter(
        out=tmp_path / "bundle", subject="spiffe://corp/agents/f27", source="urn:agentce:emit:test"
    )


def test_on_end_translates_a_gen_ai_span_into_a_model_call(tmp_path: Path) -> None:
    em = _emitter(tmp_path)
    processor = AgentCESpanProcessor(em)
    span = Span(
        name="chat gpt-4o",
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "openai",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.input_tokens": 42,
            "gen_ai.usage.output_tokens": 7,
        },
        start_time=1_000_000_000,
        end_time=1_500_000_000,
    )
    processor.on_end(span)
    assert em.events == []  # buffered, not yet translated
    processor.force_flush()
    assert len(em.events) == 1
    event = em.events[0]
    assert event["data"]["@type"] == "ModelCall"
    assert event["data"]["model"] == {"provider": "openai", "name": "gpt-4o"}
    assert event["data"]["usage"] == {"input_tokens": 42, "output_tokens": 7}
    assert event["agentcesourceclass"] == "self_report"


def test_on_end_translates_a_tool_span(tmp_path: Path) -> None:
    em = _emitter(tmp_path)
    processor = AgentCESpanProcessor(em)
    span = Span(
        name="execute_tool credit.record_decision",
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "credit.record_decision",
        },
    )
    processor.on_end(span)
    processor.force_flush()
    assert len(em.events) == 1
    assert em.events[0]["data"]["@type"] == "ToolCall"
    assert em.events[0]["data"]["tool"]["name"] == "credit.record_decision"


def test_an_unrecognised_span_is_skipped_not_invented(tmp_path: Path) -> None:
    em = _emitter(tmp_path)
    processor = AgentCESpanProcessor(em)
    processor.on_end(Span(name="internal housekeeping", attributes={}))
    processor.force_flush()
    assert em.events == []


def test_inactive_emitter_buffers_nothing(tmp_path: Path) -> None:
    em = agentce_emit.Emitter(
        out=tmp_path / "b", subject="s", source="urn:x", active=False
    )
    processor = AgentCESpanProcessor(em)
    processor.on_end(Span(name="chat", attributes={"gen_ai.operation.name": "chat"}))
    processor.force_flush()
    assert em.events == []


def test_shutdown_flushes_a_valid_bundle(tmp_path: Path) -> None:
    em = _emitter(tmp_path)
    processor = AgentCESpanProcessor(em)
    processor.on_end(
        Span(
            name="chat",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": "claude",
            },
        )
    )
    processor.shutdown()
    em.flush()
    ingested = ingest(load_bundle(tmp_path / "bundle"))
    assert ingested.quarantined == []
    assert len(ingested.accepted) == 1


def test_instrument_returns_a_processor_bound_to_the_given_emitter(tmp_path: Path) -> None:
    em = _emitter(tmp_path)
    processor = agentce_emit.instrument(em)
    assert isinstance(processor, AgentCESpanProcessor)
    processor.on_end(Span(name="chat", attributes={"gen_ai.operation.name": "chat"}))
    processor.force_flush()
    assert len(em.events) == 1


def test_instrument_defaults_to_auto(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTCE_EMIT", "1")
    monkeypatch.setenv("AGENTCE_EMIT_OUT", str(tmp_path / "b"))
    processor = agentce_emit.instrument()
    assert isinstance(processor, AgentCESpanProcessor)


def test_auto_with_zero_manual_calls_still_declares_a_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exact SPEC 13.4 AX-3 outcome (F27 C3): the one-line integration alone, with zero
    ``emit_*`` calls, captures at least one real event -- 'declare once' actually declares."""
    monkeypatch.setenv("AGENTCE_EMIT", "1")
    monkeypatch.setenv("AGENTCE_EMIT_OUT", str(tmp_path / "b"))
    em = agentce_emit.auto()
    assert em.active is True
    bundle = em.flush()
    assert bundle is not None
    assert em.events
    types = {e["data"]["@type"] for e in em.events}
    assert types == {"SessionStart", "SessionEnd"}
    ingested = ingest(load_bundle(tmp_path / "b"))
    assert ingested.quarantined == []
    assert len(ingested.accepted) == len(em.events)


def test_a_real_manual_session_is_never_touched_by_the_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENTCE_EMIT", "1")
    monkeypatch.setenv("AGENTCE_EMIT_OUT", str(tmp_path / "b"))
    em = agentce_emit.auto()
    em.emit_decision(decision_type="dom:X")
    em.flush()
    assert len(em.events) == 1
    assert em.events[0]["data"]["@type"] == "Decision"
