"""Write cases.json for VG-APPROVAL-OUTCOME-BOUND (18.144). Run from this folder: python make_cases.py.

An Outcome that names a decision shows the decision had already taken effect, so CND-02 (high-effect actions are gated
by a recorded approval) and OVS-08 (a recorded approval decision, two under dual control) count an approve only when
its time is at or before the earliest time the decision took effect: every ToolCall that executes it and every Outcome
that names it. Times compare as instants, never as text; a time that cannot be compared gates nothing. A decision with
neither is gated by an approval at any time, and OVS-01 still counts a late approval as a review. A reject still
withdraws an approve only against the ToolCalls (18.142, unchanged). Event times stay non-decreasing within each stream
(one per source class); the offset cases record the Outcome in the enforcement point's stream so that the approval's
stream holds one offset only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import (  # noqa: E402
    NC,
    acted_last,
    appr,
    case,
    dec,
    dual,
    ev,
    idp,
    ran,
    ref,
    write,
)


def at(hour, rest=":00:00Z"):
    return f"2026-01-10T{hour:02d}{rest}"


def timed(e, t):
    e["time"] = t
    return e


def outcome_at(t, d="d1", i="o1", cls="independent_system"):
    """The decision's Outcome: the record that it took effect."""
    return ev(i, "Outcome", at=t, cls=cls, refs={"decision": ref(d)})


def run_at(t, d="d1", i="t0"):
    return timed(ran(d, i), t)


def login_at(t, i="s1", who="u2"):
    return timed(idp(i, who), t)


def approve_at(t, **k):
    return timed(appr("approve", **k), t)


def reject_at(t, **k):
    return timed(appr("reject", **k), t)


def cnd02(name, events, outcome, failing=()):
    """A CND-02 case whose decisions' own actions run last (approval_events.acted_last)."""
    case(name, "CND-02", acted_last(events, run_at), outcome, failing)


# The probe: the Outcome at 01:00 shows the decision took effect; the login and the approval came hours later.
LATE = [dec(), outcome_at(at(1)), login_at(at(4)), approve_at(at(5))]
cnd02("cnd02-approve-after-outcome-no-toolcall", LATE, NC, ["d1"])
case("ovs08-approve-after-outcome-no-toolcall", "OVS-08", LATE, NC, ["d1"])
case(
    "ovs08-nist-approve-after-outcome-no-toolcall",
    "OVS-08",
    LATE,
    NC,
    ["d1"],
    profile="nist",
    exit=1,
)
cnd02(
    "cnd02-approve-before-outcome-no-toolcall",
    [dec(), login_at(at(1)), approve_at(at(2)), outcome_at(at(3))],
    "conformant",
)
cnd02(
    "cnd02-approve-same-instant-as-outcome",
    [dec(), login_at(at(1)), outcome_at(at(1)), approve_at(at(1))],
    "conformant",
)
cnd02(
    "cnd02-approve-a-second-after-outcome",
    [dec(), login_at(at(1)), outcome_at(at(1)), approve_at(at(1, ":00:01Z"))],
    NC,
    ["d1"],
)
# With a ToolCall too, the earliest of the ToolCalls and the Outcomes bounds the approval.
case(
    "cnd02-approve-before-toolcall-after-outcome",
    "CND-02",
    [dec(), login_at(at(1)), approve_at(at(1)), run_at(at(2)), outcome_at(at(3))],
    "conformant",
)
case(
    "cnd02-outcome-before-toolcall-approve-between",
    "CND-02",
    [dec(), outcome_at(at(1)), login_at(at(2)), approve_at(at(2)), run_at(at(3))],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-between-two-outcomes",
    [
        dec(),
        outcome_at(at(1)),
        login_at(at(2)),
        approve_at(at(2)),
        outcome_at(at(3), i="o2"),
    ],
    NC,
    ["d1"],
)
# Instants, not text: 00:30-05:00 is 05:30Z, after a 01:00Z Outcome though it sorts first as text; 05:00+04:00 is
# 01:00Z, before a 03:00Z Outcome though it sorts last.
cnd02(
    "cnd02-approve-after-outcome-in-another-offset",
    [
        dec(),
        outcome_at(at(1), cls="enforcement_point"),
        login_at(at(0, ":30:00-05:00")),
        approve_at(at(0, ":30:00-05:00")),
    ],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-before-outcome-in-another-offset",
    [
        dec(),
        login_at(at(5, ":00:00+04:00")),
        approve_at(at(5, ":00:00+04:00")),
        outcome_at(at(3), cls="enforcement_point"),
    ],
    "conformant",
)
cnd02(
    "cnd02-approve-time-without-offset-vs-outcome",
    [
        dec(),
        outcome_at(at(3), cls="enforcement_point"),
        login_at("2026-01-10T01:00:00"),
        approve_at("2026-01-10T01:00:00"),
    ],
    NC,
    ["d1"],
)
# An agent's own record that the decision took effect still shows it took effect before the approval.
cnd02(
    "cnd02-self-reported-outcome-before-approval",
    [dec(), outcome_at(at(1), cls="self_report"), login_at(at(2)), approve_at(at(2))],
    NC,
    ["d1"],
)
# Another decision's Outcome does not move this one's bound.
cnd02(
    "cnd02-other-decision-outcome-earlier",
    [
        dec(),
        dec("d2"),
        login_at(at(0)),
        approve_at(at(0), i="a2", d="d2"),
        outcome_at(at(0), d="d2", i="o2"),
        approve_at(at(1)),
        outcome_at(at(2)),
    ],
    "conformant",
)
# Dual control: both approvers must be in force when the decision took effect.
TWO_LOGINS = [login_at(at(1)), login_at(at(1), i="s2", who="u3")]
case(
    "ovs08-dual-one-before-one-after-outcome",
    "OVS-08",
    [
        dual(),
        *TWO_LOGINS,
        approve_at(at(1)),
        outcome_at(at(2)),
        approve_at(at(3), i="a2", who="u3", s="s2"),
    ],
    NC,
    ["d1"],
)
case(
    "ovs08-dual-both-before-outcome",
    "OVS-08",
    [
        dual(),
        *TWO_LOGINS,
        approve_at(at(1)),
        approve_at(at(1), i="a2", who="u3", s="s2"),
        outcome_at(at(2)),
    ],
    "conformant",
)
# Arrival order: an Outcome listed after the approval (in another stream) still bounds it by its time.
ARRIVES_LATE = [
    dec(),
    login_at(at(4)),
    approve_at(at(5)),
    outcome_at(at(1), cls="enforcement_point"),
]
cnd02("cnd02-outcome-arrives-after-approval", ARRIVES_LATE, NC, ["d1"])
case("ovs08-outcome-arrives-after-approval", "OVS-08", ARRIVES_LATE, NC, ["d1"])
# A late approval is still a review.
case("ovs01-approve-after-outcome-is-a-review", "OVS-01", LATE, "conformant")
# A reject still withdraws an approve against the ToolCalls only (18.142): with no ToolCall, the same human's reject
# after the Outcome withdraws the approve.
cnd02(
    "cnd02-reject-after-outcome-no-toolcall",
    [
        dec(),
        login_at(at(1)),
        approve_at(at(1)),
        outcome_at(at(2)),
        reject_at(at(3), i="a2"),
    ],
    NC,
    ["d1"],
)

write()
