"""The adapter parses untrusted gateway logs; malformed or partial entries degrade, never crash."""

from __future__ import annotations

import json
from typing import Any

from agentce_adapters import adapt
from agentce_adapters.mcp_gateway import DEFAULT_PROTOCOL_VERSION


def _entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "timestamp": "2026-05-01T09:00:00.000Z",
        "gateway": "g",
        "trace_id": "0" * 32,
        "span_id": "1" * 16,
    }
    base.update(overrides)
    return base


def _adapt_one(**overrides: Any) -> Any:
    return adapt(json.dumps(_entry(**overrides)), subject="s")


def test_each_missing_envelope_field_skips_the_entry() -> None:
    for field in ("timestamp", "gateway", "trace_id", "span_id"):
        entry = _entry(request={"method": "tools/call", "params": {"name": "t"}})
        del entry[field]
        result = adapt(json.dumps(entry), subject="s")
        assert result.events == []
        assert result.report.skipped[0].reason == "missing_envelope"


def test_protocol_version_defaults_when_absent() -> None:
    result = _adapt_one(request={"method": "tools/call", "params": {"name": "t"}})
    assert result.events[0]["agentceconv"] == f"mcp:{DEFAULT_PROTOCOL_VERSION}"


def test_a_policy_only_entry_yields_a_policy_decision_without_a_request_ref() -> None:
    result = _adapt_one(policy={"engine": "opa", "decision": "allow"})
    assert len(result.events) == 1
    policy = result.events[0]
    assert policy["data"]["@type"] == "PolicyDecision"
    assert "refs" not in policy["data"]


def test_an_entry_can_carry_both_a_call_and_a_delegation() -> None:
    result = _adapt_one(
        request={"method": "tools/call", "params": {"name": "t"}},
        delegation={"token_ref": "tok", "chain": [{"id": "a", "kind": "service"}]},
    )
    kinds = sorted(e["data"]["@type"] for e in result.events)
    assert kinds == ["DelegationIssued", "ToolCall"]


def test_invalid_enum_values_are_dropped() -> None:
    result = _adapt_one(
        request={"method": "tools/call", "params": {"name": "t"}},
        effect={"side_effect": "teleport", "effect_class": "magic"},
        policy={"engine": "astrology", "decision": "maybe"},
    )
    tool = next(e for e in result.events if e["data"]["@type"] == "ToolCall")
    assert "side_effect" not in tool["data"]
    assert "effect_class" not in tool["data"]
    policy = next(e for e in result.events if e["data"]["@type"] == "PolicyDecision")
    assert "engine" not in policy["data"]
    assert "decision" not in policy["data"]


def test_a_principal_needs_an_id_and_a_valid_kind() -> None:
    # No principal member when the kind is unknown or the id is missing.
    no_kind = _adapt_one(
        policy={"decision": "allow", "principal": {"id": "p", "kind": "alien"}}
    )
    assert "principal" not in no_kind.events[0]["data"]
    no_id = _adapt_one(policy={"decision": "allow", "principal": {"kind": "agent"}})
    assert "principal" not in no_id.events[0]["data"]


def test_principal_optional_fields_are_carried() -> None:
    result = _adapt_one(
        policy={
            "decision": "allow",
            "principal": {
                "id": "p",
                "kind": "human",
                "authority_ref": "ref://a",
                "org": "acme",
            },
        }
    )
    assert result.events[0]["data"]["principal"] == {
        "id": "p",
        "kind": "human",
        "authority_ref": "ref://a",
        "org": "acme",
    }


def test_delegation_verification_log_ref_and_invalid_status() -> None:
    good = _adapt_one(
        delegation={
            "token_ref": "t",
            "verification": {"status": "failed", "log_ref": "l"},
        }
    )
    assert good.events[0]["data"]["verification"] == {
        "status": "failed",
        "log_ref": "l",
    }
    bad = _adapt_one(delegation={"token_ref": "t", "verification": {"status": "bogus"}})
    assert "verification" not in bad.events[0]["data"]


def test_delegation_chain_drops_malformed_principals() -> None:
    result = _adapt_one(
        delegation={
            "token_ref": "t",
            "chain": ["not-a-dict", {"id": "a", "kind": "service"}, {"id": "b"}],
        }
    )
    assert result.events[0]["data"]["chain"] == [{"id": "a", "kind": "service"}]


def test_agent_without_an_id_is_omitted() -> None:
    result = _adapt_one(
        agent={"name": "nameless"},
        request={"method": "tools/call", "params": {"name": "t"}},
    )
    assert "agent" not in result.events[0]["data"]


def test_tool_and_resource_names_fall_back_when_absent() -> None:
    tool = _adapt_one(request={"method": "tools/call", "params": {}})
    assert tool.events[0]["data"]["tool"]["name"] == "unknown"
    resource = _adapt_one(request={"method": "resources/read", "params": {}})
    assert resource.events[0]["data"]["resource"]["uri"] == "unknown"


def test_blank_lines_are_ignored() -> None:
    log = (
        "\n\n"
        + json.dumps(_entry(request={"method": "tools/call", "params": {"name": "t"}}))
        + "\n\n"
    )
    result = adapt(log, subject="s")
    assert result.report.entries_seen == 1
    assert len(result.events) == 1


def test_trace_context_parent_and_task_are_carried() -> None:
    result = _adapt_one(
        parent_span_id="2" * 16,
        task_id="task-7",
        request={"method": "tools/call", "params": {"name": "t"}},
    )
    event = result.events[0]
    assert event["agentceparent"] == "2" * 16
    assert event["agentcetask"] == "task-7"


def test_members_of_handles_a_missing_payload() -> None:
    from agentce_adapters.members import members_of

    assert members_of({}) == set()
