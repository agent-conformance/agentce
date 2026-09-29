"""compute_security_view/render_security_md/render_security_html: the security-framed selection of
already-computed activity/crosswalk facts (18.16). Cross-engine diff and the committed golden are C5's
build-gate work (verification/gates/security_view.sh); this file proves this session's real code
against real inputs, including a full `assess --for security` run against the quickstart fixture."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from agentce import cli
from agentce.assertions import Assertion
from agentce.report import render_security_html, render_security_md, validate_report
from agentce.security_view import compute_security_view

_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
_GATE_FIXTURE = _REPO_ROOT / "verification" / "gates" / "fixtures" / "security_view"
_GATE_GOLDEN = _REPO_ROOT / "verification" / "gates" / "security_view_golden.json"
_GATE_GOLDEN_MD = _REPO_ROOT / "verification" / "gates" / "security_view_golden.md"

_EMPTY_ACTIVITY: dict[str, Any] = {
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


def test_compute_security_view_is_order_independent_over_many_assertions() -> None:
    """As `test_activity.py::test_activity_is_order_independent`: a real determinism proof over more
    than two assertions (contract C4's own named "shuffled assertion order" test), distinct from the
    two-assertion dedup case above."""
    assertions = [
        _assertion(
            control,
            [
                {
                    "framework": "mitre-atlas",
                    "clause": f"AML.T0{n}",
                    "verified": n % 2 == 0,
                }
            ],
        )
        for n, control in enumerate(
            ["ROB-02", "OVS-03", "INT-01", "REC-01", "REC-04"], start=51
        )
    ]
    forward = compute_security_view(_EMPTY_ACTIVITY, assertions)
    reversed_view = compute_security_view(_EMPTY_ACTIVITY, list(reversed(assertions)))
    shuffled = list(assertions)
    random.Random(0).shuffle(shuffled)
    shuffled_view = compute_security_view(_EMPTY_ACTIVITY, shuffled)
    assert reversed_view == forward
    assert shuffled_view == forward
    assert len(forward["standards_citations"]) == 5


def _assess_security(out: Path) -> int:
    return cli.main(
        [
            "assess",
            "--bundle",
            str(_QUICKSTART / "evidence"),
            "--profile",
            str(_QUICKSTART / "applicability.yaml"),
            "--domain",
            str(_QUICKSTART / "domain.linkml.yaml"),
            "--out",
            str(out),
            "--for",
            "security",
        ]
    )


def test_write_report_for_security_preset_writes_security_artifacts(
    tmp_path: Path,
) -> None:
    out_with = tmp_path / "with"
    assert _assess_security(out_with) == 0
    for name in ("security.md", "security.html", "security.json"):
        assert (out_with / name).is_file(), name

    out_without = tmp_path / "without"
    assert (
        cli.main(
            [
                "assess",
                "--bundle",
                str(_QUICKSTART / "evidence"),
                "--profile",
                str(_QUICKSTART / "applicability.yaml"),
                "--domain",
                str(_QUICKSTART / "domain.linkml.yaml"),
                "--out",
                str(out_without),
            ]
        )
        == 0
    )
    for name in ("security.md", "security.html", "security.json"):
        assert not (out_without / name).exists(), name


def test_security_json_validates_against_its_schema(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert _assess_security(out) == 0
    assert validate_report(out) == []


def test_security_json_matches_activity_json_for_shared_fields(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert _assess_security(out) == 0
    security = json.loads((out / "security.json").read_text(encoding="utf-8"))
    activity = json.loads((out / "activity.json").read_text(encoding="utf-8"))
    assert security["actions_by_effect_class"] == activity["actions_by_effect_class"]
    assert security["drift"]["tools"] == activity["undeclared"]["tools"]
    assert security["drift"]["models"] == activity["undeclared"]["models"]


def test_render_security_md_with_language_de_does_not_crash_and_falls_back() -> None:
    """As `test_rendering.py::test_catalogue_falls_back_to_english`: no German translation exists yet
    for the security view's own keys (18.14's own precedent for a per-view language smoke test does
    not exist under any name -- confirmed by search, so this is a fresh test, not a port), so the
    German-language render must fall back to the English text rather than raising or leaving an
    unresolved message key in the output."""
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
    md = render_security_md(view, language="de")
    html_out = render_security_html(view, language="de")
    assert "AgentCE security view" in md
    assert "AgentCE doesn't block anything." in md
    assert "AgentCE security view" in html_out


def test_security_json_matches_committed_golden(tmp_path: Path) -> None:
    """`verification/gates/security_view.sh` (C5) captures this same fixture's real `security.json`
    as the committed golden; this test proves the C4 code that gate script exercises produces that
    exact output on a fresh run, byte-for-byte, so a code change that silently drifts the view is
    caught here too, not only by the build gate. The fixture's own real verdict is non-conformant
    (ROB-02 fails its shape's `prov:used` property), so `assess` exits 1 (ExitCode.FINDINGS), not 0."""
    out = tmp_path / "o"
    exit_code = cli.main(
        [
            "assess",
            "--bundle",
            str(_GATE_FIXTURE / "evidence"),
            "--profile",
            str(_GATE_FIXTURE / "applicability.yaml"),
            "--domain",
            str(_GATE_FIXTURE / "domain.linkml.yaml"),
            "--out",
            str(out),
            "--for",
            "security",
        ]
    )
    assert exit_code == 1
    security = json.loads((out / "security.json").read_text(encoding="utf-8"))
    golden = json.loads(_GATE_GOLDEN.read_text(encoding="utf-8"))
    assert security == golden
    assert (out / "security.md").read_text(
        encoding="utf-8"
    ) == _GATE_GOLDEN_MD.read_text(encoding="utf-8")
