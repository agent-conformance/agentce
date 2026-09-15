"""The agentce-emit one-line emitter (SPEC §13.1, §13.4 AX-3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agentce.bundle import load_bundle
from agentce.ingest import ingest
from agentce.schema import validate_event

import agentce_emit


def _emit_a_session(em: agentce_emit.Emitter) -> None:
    em.emit_session_start(environment="production", session_id="s1")
    em.emit_model_call(
        operation="chat",
        provider="openai",
        model="gpt-4o",
        input_tokens=100,
        output_tokens=20,
    )
    em.emit_tool_call(
        name="credit.record_decision",
        server="mcp://core",
        protocol="mcp",
        args={"pii": "SECRET"},
        result={"ok": True},
        side_effect="write",
        effect_class="write",
    )
    em.emit_decision(
        decision_type="dom:CreditDecision",
        affects_natural_person=True,
        oversight_modality="review_before",
        chosen="approve",
    )
    em.emit_incident(incident_class="fundamental_rights", incident_id="inc-1")
    em.emit_session_end(end_reason="completed", session_id="s1")


def test_auto_one_line_produces_a_valid_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTCE_EMIT", "1")
    monkeypatch.setenv("AGENTCE_EMIT_OUT", str(tmp_path / "bundle"))
    monkeypatch.setenv("AGENTCE_EMIT_SUBJECT", "spiffe://corp/agents/credit")
    em = agentce_emit.auto()
    assert em.active is True
    _emit_a_session(em)
    bundle_dir = em.flush()
    assert bundle_dir == tmp_path / "bundle"

    ingested = ingest(load_bundle(tmp_path / "bundle"))
    assert ingested.quarantined == []
    assert len(ingested.accepted) == 6
    for event in ingested.accepted:
        assert validate_event(event) == []
        assert event["agentcesourceclass"] == "self_report"


def test_auto_one_line_emits_the_minimum_families(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTCE_EMIT", "1")
    monkeypatch.setenv("AGENTCE_EMIT_OUT", str(tmp_path / "b"))
    em = agentce_emit.auto()
    _emit_a_session(em)
    em.flush()
    ingested = ingest(load_bundle(tmp_path / "b"))
    types = {e["data"]["@type"] for e in ingested.accepted}
    # REC (SessionStart/End, Decision, ModelCall, ToolCall) and INC (Incident) minimum.
    assert {
        "SessionStart",
        "SessionEnd",
        "Decision",
        "ModelCall",
        "ToolCall",
        "Incident",
    } <= types


def test_auto_is_a_noop_without_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTCE_EMIT", raising=False)
    monkeypatch.setenv("AGENTCE_EMIT_OUT", str(tmp_path / "b"))
    em = agentce_emit.auto()
    assert em.active is False
    assert em.emit_decision(decision_type="dom:X") is None
    assert em.flush() is None
    assert not (tmp_path / "b").exists()


def test_content_is_referenced_by_hash_never_captured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTCE_EMIT", "1")
    monkeypatch.setenv("AGENTCE_EMIT_OUT", str(tmp_path / "b"))
    em = agentce_emit.auto()
    em.emit_tool_call(
        name="t", args={"pii": "SECRET-VALUE"}, result={"x": "ALSO-SECRET"}
    )
    em.flush()
    blob = (tmp_path / "b" / "events" / "stream.jsonl").read_text(encoding="utf-8")
    assert "SECRET-VALUE" not in blob and "ALSO-SECRET" not in blob
    event = ingest(load_bundle(tmp_path / "b")).accepted[0]
    assert event["data"]["args_ref"].startswith("sha256:")


def test_context_manager_flushes(tmp_path: Path) -> None:
    with agentce_emit.Emitter(
        out=tmp_path / "b", subject="s", source="urn:emit:x"
    ) as em:
        em.emit_session_start()
    assert (tmp_path / "b" / "manifest.json").is_file()


def test_explicit_class_override_is_honoured(tmp_path: Path) -> None:
    em = agentce_emit.Emitter(
        out=tmp_path / "b",
        subject="s",
        source="urn:emit:x",
        source_class="totally_trusted",
    )
    # An invalid class falls back to self_report (agent-side emission is a self-report, S-2).
    assert em.source_class == "self_report"
