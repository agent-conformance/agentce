"""What your agents did (Hill 1): counted facts about a run, built only from the accepted events.

``summarize_activity`` is the one place the agents, models, tools, actions by effect class,
approvals by who recorded them, actions denied or blocked, and tools/models the records show but
the profile never declared are computed; ``report.md``, ``report.html``, the ``--json`` envelope, and
the terminal all render that one dictionary (the pattern :func:`agentce.verdict.summarize`
established), so they cannot disagree. Nothing here depends on event order, the clock, or the locale.
"""

from __future__ import annotations

from typing import Any

from .profile import Profile

#: Every ``ToolCall.effect_class`` this view counts, plus the bucket for a call that names none.
EFFECT_CLASSES = (
    "external_communication",
    "irreversible",
    "physical",
    "read",
    "spend",
    "unspecified",
    "write",
)
#: The three evidence-source trust classes (SPEC §6.4) an ``ApprovalDecided`` event was recorded by.
RECORDER_CLASSES = ("enforcement_point", "independent_system", "self_report")
#: The four ways SPEC's authority events say no to an action.
DENIED_KINDS = ("approval_rejected", "authz_denied", "policy_denied", "refused")


def _event_type(event: dict[str, Any]) -> str:
    data = event.get("data")
    return (
        str(data["@type"])
        if isinstance(data, dict) and isinstance(data.get("@type"), str)
        else ""
    )


def _data(event: dict[str, Any]) -> dict[str, Any] | None:
    data = event.get("data")
    return data if isinstance(data, dict) else None


def _declared(profile: Profile, field: str) -> set[str]:
    declared: set[str] = set()
    for subject in profile.subjects:
        declared.update(getattr(subject, field))
    return declared


def summarize_activity(
    events: list[dict[str, Any]],
    profile: Profile,
    declared_subject_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Return the counted facts ``events`` show, compared against what ``profile`` declares.

    ``undeclared`` names every distinct tool and model name the events show that no subject's
    ``declared_tools``/``declared_models`` names -- honestly "not declared yet", never "suspicious":
    a profile that declares neither leaves every tool and model in that list, which is the correct
    first-run answer, not a false positive. ``undeclared.agents`` is the same idea for agent ids: by
    default (``declared_subject_ids`` omitted) every subject in ``profile`` counts as declared, exactly
    as today; a caller that discovers subjects rather than being told them (the records-folder scan)
    passes its own, independently-derived ``declared_subject_ids`` instead.
    """
    agents: set[str] = set()
    models: set[tuple[str, str, str]] = set()
    tools: set[tuple[str, str, str]] = set()
    observed_tools: set[str] = set()
    observed_models: set[str] = set()
    actions_by_effect_class: dict[str, int] = {c: 0 for c in EFFECT_CLASSES}
    approvals_by_recorder: dict[str, int] = {c: 0 for c in RECORDER_CLASSES}
    denied_or_blocked: dict[str, int] = {k: 0 for k in DENIED_KINDS}

    for event in events:
        data = _data(event)
        if data is None:
            continue
        agent = data.get("agent")
        if isinstance(agent, dict) and isinstance(agent.get("id"), str):
            agents.add(str(agent["id"]))
        etype = _event_type(event)
        if etype == "ModelCall":
            model = data.get("model")
            if isinstance(model, dict) and isinstance(model.get("name"), str):
                name = str(model["name"])
                observed_models.add(name)
                models.add(
                    (
                        str(model.get("provider") or ""),
                        name,
                        str(model.get("version_or_digest") or ""),
                    )
                )
        elif etype == "ToolCall":
            tool = data.get("tool")
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                name = str(tool["name"])
                observed_tools.add(name)
                tools.add(
                    (
                        name,
                        str(tool.get("server") or ""),
                        str(tool.get("protocol") or ""),
                    )
                )
            effect_class = data.get("effect_class")
            key = (
                str(effect_class)
                if isinstance(effect_class, str)
                and effect_class in actions_by_effect_class
                else "unspecified"
            )
            actions_by_effect_class[key] += 1
        elif etype == "ApprovalDecided":
            source_class = str(event.get("agentcesourceclass") or "")
            if source_class in approvals_by_recorder:
                approvals_by_recorder[source_class] += 1
            if data.get("outcome") == "reject":
                denied_or_blocked["approval_rejected"] += 1
        elif etype == "PolicyDecision" and data.get("decision") == "deny":
            denied_or_blocked["policy_denied"] += 1
        elif etype == "AuthzCheck" and data.get("allowed") is False:
            denied_or_blocked["authz_denied"] += 1
        elif etype == "Refusal":
            denied_or_blocked["refused"] += 1

    declared_tools = _declared(profile, "declared_tools")
    declared_models = _declared(profile, "declared_models")
    declared_agents = (
        declared_subject_ids
        if declared_subject_ids is not None
        else {s.id for s in profile.subjects}
    )
    return {
        "agents": sorted(agents),
        "models": [
            {"provider": provider, "name": name, "version_or_digest": digest}
            for provider, name, digest in sorted(models)
        ],
        "tools": [
            {"name": name, "server": server, "protocol": protocol}
            for name, server, protocol in sorted(tools)
        ],
        "actions_by_effect_class": actions_by_effect_class,
        "approvals_by_recorder": approvals_by_recorder,
        "denied_or_blocked": denied_or_blocked,
        "undeclared": {
            "models": sorted(observed_models - declared_models),
            "tools": sorted(observed_tools - declared_tools),
            "agents": sorted(agents - declared_agents),
        },
    }
