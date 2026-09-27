"""summarize_activity: the counted facts a run's records show (18.4, Hill 1)."""

from __future__ import annotations

from typing import Any

from agentce.activity import summarize_activity
from agentce.profile import Profile

SUBJECT = "spiffe://corp/agents/a"


def _event(
    event_type: str, *, source_class: str = "self_report", **data: Any
) -> dict[str, Any]:
    return {
        "id": f"e-{event_type}-{len(data)}-{data.get('name', '')}",
        "subject": SUBJECT,
        "agentcesourceclass": source_class,
        "data": {"@type": event_type, "agent": {"id": SUBJECT}, **data},
    }


def _profile(declared_tools: list[str] | None = None) -> Profile:
    return Profile.from_dict(
        {
            "catalogs": ["eu-ai-act@2026.09"],
            "subjects": [
                {
                    "id": SUBJECT,
                    "role": "both",
                    **(
                        {"declared_tools": declared_tools}
                        if declared_tools is not None
                        else {}
                    ),
                }
            ],
        }
    )


def test_empty_run_reports_all_zero_counts() -> None:
    activity = summarize_activity([], Profile())
    assert activity["agents"] == []
    assert activity["models"] == []
    assert activity["tools"] == []
    assert all(n == 0 for n in activity["actions_by_effect_class"].values())
    assert all(n == 0 for n in activity["approvals_by_recorder"].values())
    assert all(n == 0 for n in activity["denied_or_blocked"].values())
    assert activity["undeclared"] == {"models": [], "tools": []}


def test_counts_agents_models_and_tools() -> None:
    events = [
        _event(
            "ModelCall",
            model={"provider": "openai", "name": "gpt-x", "version_or_digest": "1"},
        ),
        _event(
            "ToolCall",
            tool={"name": "search", "server": "mcp://s", "protocol": "mcp"},
            effect_class="read",
        ),
        _event(
            "ToolCall",
            tool={"name": "transfer_funds", "server": "mcp://s", "protocol": "mcp"},
            effect_class="irreversible",
        ),
        # A ToolCall naming no effect_class falls into the "unspecified" bucket.
        _event(
            "ToolCall", tool={"name": "search", "server": "mcp://s", "protocol": "mcp"}
        ),
    ]
    activity = summarize_activity(events, Profile())
    assert activity["agents"] == [SUBJECT]
    assert [m["name"] for m in activity["models"]] == ["gpt-x"]
    assert sorted(t["name"] for t in activity["tools"]) == ["search", "transfer_funds"]
    assert activity["actions_by_effect_class"]["read"] == 1
    assert activity["actions_by_effect_class"]["irreversible"] == 1
    assert activity["actions_by_effect_class"]["unspecified"] == 1
    assert activity["actions_by_effect_class"]["write"] == 0


def test_approvals_counted_by_who_recorded_them() -> None:
    events = [
        _event("ApprovalDecided", source_class="self_report", outcome="approve"),
        _event("ApprovalDecided", source_class="independent_system", outcome="approve"),
        _event("ApprovalDecided", source_class="independent_system", outcome="reject"),
    ]
    activity = summarize_activity(events, Profile())
    assert activity["approvals_by_recorder"] == {
        "enforcement_point": 0,
        "independent_system": 2,
        "self_report": 1,
    }
    assert activity["denied_or_blocked"]["approval_rejected"] == 1


def test_denied_or_blocked_covers_the_four_authority_signals() -> None:
    events = [
        _event("PolicyDecision", decision="deny"),
        _event("PolicyDecision", decision="allow"),
        _event("AuthzCheck", allowed=False),
        _event("AuthzCheck", allowed=True),
        _event("Refusal", reason_class="policy"),
    ]
    activity = summarize_activity(events, Profile())
    assert activity["denied_or_blocked"] == {
        "approval_rejected": 0,
        "authz_denied": 1,
        "policy_denied": 1,
        "refused": 1,
    }


def test_undeclared_tool_and_model_are_honest_not_yet_declared() -> None:
    # No profile declares anything: the honest first-run answer is everything observed.
    events = [
        _event("ToolCall", tool={"name": "search", "server": "s", "protocol": "mcp"}),
        _event(
            "ModelCall",
            model={"provider": "openai", "name": "gpt-x", "version_or_digest": "1"},
        ),
    ]
    activity = summarize_activity(events, Profile())
    assert activity["undeclared"] == {"models": ["gpt-x"], "tools": ["search"]}


def test_declaring_a_tool_removes_it_from_undeclared() -> None:
    events = [
        _event("ToolCall", tool={"name": "search", "server": "s", "protocol": "mcp"}),
        _event(
            "ToolCall",
            tool={"name": "transfer_funds", "server": "s", "protocol": "mcp"},
        ),
    ]
    activity = summarize_activity(events, _profile(declared_tools=["search"]))
    assert activity["undeclared"]["tools"] == ["transfer_funds"]


def test_declaring_every_tool_leaves_nothing_undeclared() -> None:
    events = [
        _event("ToolCall", tool={"name": "search", "server": "s", "protocol": "mcp"})
    ]
    activity = summarize_activity(events, _profile(declared_tools=["search"]))
    assert activity["undeclared"]["tools"] == []


def test_non_dict_data_and_unrelated_event_types_are_ignored() -> None:
    events = [
        {"id": "e1", "subject": SUBJECT, "data": "not-a-dict"},
        _event("SessionStart"),
    ]
    activity = summarize_activity(events, Profile())
    assert activity["agents"] == [SUBJECT]
    assert activity["tools"] == []
    assert activity["models"] == []
