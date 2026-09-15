"""Shared emit scenarios for the runnable examples (SPEC §13.4 AX-4).

Each example agent calls one of these with an ``agentce_emit`` emitter. They emit a coherent, minimal
credit-decisioning session — enough to produce a bundle that ``agentce validate`` accepts with zero
quarantines and to show the REC/OVS/INT/INC-family minimum events. The examples are small on purpose:
they are the documentation's source of truth for code snippets, so they read as an adopter would write
them, one ``agentce_emit`` call per chokepoint.
"""

from __future__ import annotations

from typing import Any

CONSEQUENTIAL = "dom:CreditDecision"


def emit_framework_session(em: Any, *, style: str) -> None:
    """A single credit decision as an OTel-instrumented framework agent would emit it."""
    em.emit_session_start(environment="production", session_id=f"{style}-1")
    em.emit_model_call(
        operation="chat",
        provider="openai",
        model="gpt-4o",
        input_tokens=1024,
        output_tokens=128,
    )
    tool = em.emit_tool_call(
        name="credit.record_decision",
        server="mcp://credit-core.internal",
        protocol="mcp",
        args={"applicant": "fictional-0001"},
        result={"status": "recorded"},
        side_effect="write",
        effect_class="write",
    )
    em.emit_decision(
        decision_type=CONSEQUENTIAL,
        affects_natural_person=True,
        ai_role="recommendation",
        oversight_modality="review_before",
        chosen="approve",
        refs={"executed_by": f"agentce:event/{tool}"} if tool else None,
    )
    em.emit_session_end(end_reason="completed", session_id=f"{style}-1")


def emit_mcp_server(em: Any) -> None:
    """An MCP server serving a tool and a resource to the agent."""
    em.emit_session_start(environment="production", session_id="mcp-1")
    em.emit_tool_call(
        name="credit.record_decision",
        server="mcp://credit-core.internal",
        protocol="mcp",
        args={"x": 1},
        result={"ok": True},
        side_effect="write",
        effect_class="write",
    )
    em.emit_resource_access(
        uri="mcp://credit-core.internal/policies", operation="read", kind="document"
    )
    em.emit_decision(
        decision_type=CONSEQUENTIAL,
        affects_natural_person=True,
        oversight_modality="review_before",
        chosen="approve",
    )
    em.emit_session_end(end_reason="completed", session_id="mcp-1")


def emit_a2a_mesh(em: Any) -> None:
    """A two-agent A2A mesh: an orchestrator delegates the decision to an underwriter."""
    em.emit_session_start(environment="production", session_id="a2a-orchestrator")
    em.emit_model_call(
        operation="chat", provider="anthropic", model="claude-3-5-sonnet"
    )
    em.emit_tool_call(
        name="credit.record_decision",
        server="a2a://underwriter",
        protocol="a2a",
        args={"task": "underwrite"},
        result={"status": "approved"},
        side_effect="write",
        effect_class="write",
    )
    em.emit_decision(
        decision_type=CONSEQUENTIAL,
        affects_natural_person=True,
        oversight_modality="review_before",
        chosen="approve",
    )
    em.emit_session_end(end_reason="completed", session_id="a2a-orchestrator")
