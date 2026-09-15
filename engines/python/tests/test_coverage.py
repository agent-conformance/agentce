"""Coverage with independent denominators, including the coverage-gap variant (SPEC §6.5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentce.coverage import (
    STATUS_BELOW,
    STATUS_COVERED,
    STATUS_UNKNOWN,
    compute_coverage,
)
from agentce.profile import Profile

SUBJECT = "spiffe://corp/agents/a"


def event(
    index: int, source: str, event_type: str, subject: str = SUBJECT
) -> dict[str, Any]:
    return {
        "id": f"e{index}",
        "subject": subject,
        "source": source,
        "time": "2026-01-01T00:00:00Z",
        "data": {"@type": event_type},
    }


def profile_with_denominator(covers: list[str] | None = None) -> Profile:
    return Profile.from_dict(
        {
            "subjects": [
                {
                    "id": SUBJECT,
                    "evidence_sources": [
                        {
                            "adapter": "gw",
                            "source": "urn:src:gw",
                            "class": "enforcement_point",
                        }
                    ],
                    "coverage_denominators": [
                        {
                            "kind": "egress_proxy_log",
                            "source": "urn:src:egress",
                            "covers": covers if covers is not None else ["ToolCall"],
                        }
                    ],
                }
            ]
        }
    )


def test_covered_when_observed_meets_expected() -> None:
    events = [event(i, "urn:src:gw", "ToolCall") for i in range(3)]
    events += [event(10 + i, "urn:src:egress", "ToolCall") for i in range(3)]
    cov = compute_coverage(events, profile_with_denominator())
    tool = cov["subjects"][SUBJECT]["event_types"]["ToolCall"]
    assert tool["observed"] == 3
    assert tool["expected"] == 3
    assert tool["status"] == STATUS_COVERED
    assert tool["denominators"] == ["urn:src:egress"]
    assert cov["subjects"][SUBJECT]["coverage_status"] == "ok"


def test_coverage_gap_is_below_threshold() -> None:
    events = [event(i, "urn:src:gw", "ToolCall") for i in range(3)]
    events += [event(10 + i, "urn:src:egress", "ToolCall") for i in range(5)]
    cov = compute_coverage(events, profile_with_denominator())
    tool = cov["subjects"][SUBJECT]["event_types"]["ToolCall"]
    assert tool["observed"] == 3
    assert tool["expected"] == 5
    assert tool["ratio"] == "0.6000"
    assert tool["status"] == STATUS_BELOW
    assert cov["subjects"][SUBJECT]["coverage_status"] == "gap"


def test_unknown_without_any_denominator() -> None:
    profile = Profile.from_dict(
        {
            "subjects": [
                {
                    "id": SUBJECT,
                    "evidence_sources": [
                        {
                            "adapter": "gw",
                            "source": "urn:src:gw",
                            "class": "self_report",
                        }
                    ],
                }
            ]
        }
    )
    events = [event(i, "urn:src:gw", "ModelCall") for i in range(2)]
    subject = compute_coverage(events, profile)["subjects"][SUBJECT]
    assert subject["coverage_status"] == STATUS_UNKNOWN
    assert subject["has_independent_denominator"] is False
    assert subject["event_types"]["ModelCall"]["status"] == STATUS_UNKNOWN


def test_type_not_covered_by_denominator_is_unknown() -> None:
    events = [event(i, "urn:src:gw", "ModelCall") for i in range(2)]
    events += [event(10, "urn:src:egress", "ToolCall")]
    cov = compute_coverage(events, profile_with_denominator(covers=["ToolCall"]))
    types = cov["subjects"][SUBJECT]["event_types"]
    assert types["ModelCall"]["status"] == STATUS_UNKNOWN
    assert cov["subjects"][SUBJECT]["coverage_status"] == STATUS_UNKNOWN


def test_expected_from_source_manifest(tmp_path: Path) -> None:
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference" / "egress.json").write_text(
        json.dumps({"counts": {"ToolCall": 4}}), encoding="utf-8"
    )
    profile = Profile.from_dict(
        {
            "subjects": [
                {
                    "id": SUBJECT,
                    "evidence_sources": [
                        {
                            "adapter": "gw",
                            "source": "urn:src:gw",
                            "class": "enforcement_point",
                        }
                    ],
                    "coverage_denominators": [
                        {
                            "kind": "egress_proxy_log",
                            "source": "urn:src:egress",
                            "covers": ["ToolCall"],
                            "manifest": "reference/egress.json",
                        }
                    ],
                }
            ]
        }
    )
    events = [event(i, "urn:src:gw", "ToolCall") for i in range(4)]
    tool = compute_coverage(events, profile, tmp_path)["subjects"][SUBJECT][
        "event_types"
    ]["ToolCall"]
    assert tool["expected"] == 4
    assert tool["status"] == STATUS_COVERED
