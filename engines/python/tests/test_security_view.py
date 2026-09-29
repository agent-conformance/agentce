"""compute_security_view/render_security_md/render_security_html: the security-framed selection of
already-computed activity/crosswalk facts (18.16). Full contract's remaining C4 test list (schema
validation, cross-engine diff, committed golden, hostile-name escaping in context, determinism over
shuffled assertion order) is a future session's work per the item's journal; this file proves this
session's real code against real inputs."""

from __future__ import annotations

from agentce.assertions import Assertion
from agentce.security_view import compute_security_view
from agentce.report import render_security_html, render_security_md

_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")

_EMPTY_ACTIVITY = {
    "agents": [],
    "models": [],
    "tools": [],
    "actions_by_effect_class": {
        "external_communication": 0,
        "irreversible": 0,
        "physical": 0,
        "read": 0,
        "spend": 0,
        "unspecified": 0,
        "write": 0,
    },
    "approvals_by_recorder": {
        "enforcement_point": 0,
        "independent_system": 0,
        "self_report": 0,
    },
    "denied_or_blocked": {
        "approval_rejected": 0,
        "authz_denied": 0,
        "policy_denied": 0,
        "refused": 0,
    },
    "undeclared": {"models": [], "tools": [], "agents": []},
}


def _assertion(control: str, crosswalk: list[dict[str, object]]) -> Assertion:
    return Assertion(
        control=control,
        control_version="2026.09",
        subject="spiffe://corp/agents/a",
        outcome="conformant",
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=(1, 0),
        severity="high",
        family=control.split("-", 1)[0],
        crosswalk=crosswalk,
    )


def test_compute_security_view_filters_to_three_frameworks_and_reuses_activity() -> (
    None
):
    activity = {
        **_EMPTY_ACTIVITY,
        "tools": [{"name": "shell", "server": "local", "protocol": "mcp"}],
        "actions_by_effect_class": {
            **_EMPTY_ACTIVITY["actions_by_effect_class"],
            "irreversible": 2,
            "read": 5,
        },
    }
    assertions = [
        _assertion(
            "ROB-02",
            [
                {"framework": "mitre-atlas", "clause": "AML.T0051", "verified": False},
                {"framework": "eu-ai-act", "clause": "Art. 9", "verified": False},
            ],
        ),
        _assertion(
            "REC-01",
            [
                {
                    "framework": "owasp-acs",
                    "clause": "Hook_SubagentStart",
                    "verified": True,
                }
            ],
        ),
    ]
    view = compute_security_view(activity, assertions)
    assert view["tool_access"] is activity["tools"]
    assert view["actions_by_effect_class"] is activity["actions_by_effect_class"]
    assert view["enforcement_point_evidence"] == {
        "denied_or_blocked": activity["denied_or_blocked"],
        "approvals_by_recorder": activity["approvals_by_recorder"],
    }
    assert view["drift"] == {"tools": [], "models": []}
    assert view["standards_citations"] == [
        {
            "control": "REC-01",
            "framework": "owasp-acs",
            "clause": "Hook_SubagentStart",
            "verified": True,
        },
        {
            "control": "ROB-02",
            "framework": "mitre-atlas",
            "clause": "AML.T0051",
            "verified": False,
        },
    ]


def test_compute_security_view_deduplicates_and_sorts_regardless_of_input_order() -> (
    None
):
    dup = {"framework": "mitre-atlas", "clause": "AML.T0101", "verified": False}
    assertions_a = [_assertion("INT-01", [dup]), _assertion("OVS-03", [dup])]
    assertions_b = list(reversed(assertions_a))
    view_a = compute_security_view(_EMPTY_ACTIVITY, assertions_a)
    view_b = compute_security_view(_EMPTY_ACTIVITY, assertions_b)
    assert view_a["standards_citations"] == view_b["standards_citations"]
    assert (
        len(view_a["standards_citations"]) == 2
    )  # one per distinct control, not deduplicated away


def test_render_security_md_never_empty_on_a_quiet_run() -> None:
    view = compute_security_view(_EMPTY_ACTIVITY, [])
    text = render_security_md(view)
    assert "no tool calls recorded" in text
    assert "no standards citations on the controls this run touched" in text
    assert "AgentCE doesn't block anything." in text


def test_render_security_escapes_hostile_tool_name() -> None:
    activity = {
        **_EMPTY_ACTIVITY,
        "tools": [
            {"name": "<script>alert(1)</script>", "server": "s", "protocol": "mcp"}
        ],
    }
    view = compute_security_view(activity, [])
    md = render_security_md(view)
    html_out = render_security_html(view)
    assert "<script>alert(1)</script>" not in md
    assert "<script>alert(1)</script>" not in html_out


def test_render_security_shows_citation_with_framework_version_and_unverified_label() -> (
    None
):
    view = compute_security_view(
        _EMPTY_ACTIVITY,
        [
            _assertion(
                "ROB-02",
                [
                    {
                        "framework": "mitre-atlas",
                        "clause": "AML.T0051",
                        "verified": False,
                    }
                ],
            )
        ],
    )
    md = render_security_md(view)
    assert "mitre-atlas 2026.09 AML.T0051" in md
    assert "(clause reference unverified)" in md
