"""summarize_activity: the counted facts a run's records show (18.4, Hill 1)."""

from __future__ import annotations

import locale
import os
import random
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
    assert activity["undeclared"] == {"models": [], "tools": [], "agents": []}


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
    assert activity["undeclared"] == {
        "models": ["gpt-x"],
        "tools": ["search"],
        "agents": [SUBJECT],
    }


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


def _mixed_events() -> list[dict[str, Any]]:
    return [
        _event(
            "ModelCall",
            model={"provider": "openai", "name": "gpt-x", "version_or_digest": "1"},
        ),
        _event(
            "ModelCall",
            model={"provider": "anthropic", "name": "claude", "version_or_digest": "2"},
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
        _event("ApprovalDecided", source_class="independent_system", outcome="reject"),
        _event("PolicyDecision", decision="deny"),
        _event("AuthzCheck", allowed=False),
        _event("Refusal"),
    ]


def test_a_nul_byte_in_one_field_cannot_collide_with_the_next_field() -> None:
    """Two distinct tools must never merge because a naive join-on-NUL key treats
    "a\\0b" + "c" the same as "a" + "b\\0c". Python's real tuples never had this bug (no
    join happens at all), but the same fixture is shared with the TypeScript and Java ports,
    where a prior version of the dedup key did have exactly this collision."""
    events = [
        _event("ToolCall", tool={"name": "a\x00b", "server": "c", "protocol": "p"}),
        _event("ToolCall", tool={"name": "a", "server": "b\x00c", "protocol": "p"}),
    ]
    activity = summarize_activity(events, Profile())
    assert len(activity["tools"]) == 2


def _agent_event(agent_id: str, name: str) -> dict[str, Any]:
    return {
        "id": f"e-{agent_id}-{name}",
        "subject": SUBJECT,
        "data": {
            "@type": "ToolCall",
            "agent": {"id": agent_id},
            "tool": {"name": name, "server": "s", "protocol": "mcp"},
        },
    }


def _two_subject_profile() -> Profile:
    return Profile.from_dict(
        {
            "catalogs": ["eu-ai-act@2026.09"],
            "subjects": [
                {"id": "A", "role": "both"},
                {"id": "B", "role": "both"},
            ],
        }
    )


def test_undeclared_agents_defaults_to_every_subject_the_profile_declares() -> None:
    # Profile declares {A, B}; events show {A, C}. Omitting declared_subject_ids falls back
    # to the profile's own subjects, so only C (never declared at all) is undeclared.
    events = [_agent_event("A", "t1"), _agent_event("C", "t2")]
    activity = summarize_activity(events, _two_subject_profile())
    assert activity["undeclared"]["agents"] == ["C"]


def test_explicit_declared_subject_ids_overrides_the_profile_not_supplements_it() -> None:
    # The SAME events and profile, but an explicit, empty declared_subject_ids: nothing was
    # declared on this path, so every observed agent -- including A, which the profile itself
    # names -- is undeclared. Proves the parameter drives the result once given, not the profile.
    events = [_agent_event("A", "t1"), _agent_event("C", "t2")]
    activity = summarize_activity(
        events, _two_subject_profile(), declared_subject_ids=frozenset()
    )
    assert activity["undeclared"]["agents"] == ["A", "C"]


def test_activity_is_order_independent() -> None:
    events = _mixed_events()
    profile = _profile(declared_tools=["search"])
    forward = summarize_activity(events, profile)
    reversed_events = list(reversed(events))
    shuffled_events = list(events)
    random.Random(0).shuffle(shuffled_events)
    assert summarize_activity(reversed_events, profile) == forward
    assert summarize_activity(shuffled_events, profile) == forward


def test_activity_is_locale_and_clock_independent() -> None:
    events = _mixed_events()
    profile = _profile(declared_tools=["search"])
    baseline = summarize_activity(events, profile)

    old_tz = os.environ.get("TZ")
    old_locale = locale.setlocale(locale.LC_ALL)
    try:
        os.environ["TZ"] = "Pacific/Kiritimati"
        for candidate in ("de_DE.UTF-8", "de_DE", "C"):
            try:
                locale.setlocale(locale.LC_ALL, candidate)
                break
            except locale.Error:
                continue
        assert summarize_activity(events, profile) == baseline
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        locale.setlocale(locale.LC_ALL, old_locale)
