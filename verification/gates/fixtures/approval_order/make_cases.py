"""Write cases.json for VG-APPROVAL-ORDER (18.141). Run from this folder: python make_cases.py.

An approval gates an action only when it is recorded at or before the action: CND-02 (high-effect actions are gated by a
recorded approval) and OVS-08 (a recorded approval decision, two under dual control) count an approve only when its
time is at or before the time of every ToolCall that executes the decision. Times compare as instants (an offset, a
fraction of a second), never as text; a time that cannot be compared with the action's gates nothing. A decision no
ToolCall executes is gated by an approval at any time, and OVS-01 and RSK-02 still count a late approval as a review.
Event times stay non-decreasing within each stream (one per source class), so ingest quarantines nothing for order,
except in the case named for it: quarantine never gives a better outcome (18.119), so when d1's own ToolCall reaches
the bundle after a later ToolCall of the enforcement point's stream and is quarantined for time_order, the approve
recorded after it does not leave CND-02 conformant."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import (  # noqa: E402
    NC,
    appr,
    case,
    dec,
    dual,
    idp,
    ran,
    write,
)


def at(hour, rest=":00:00Z"):
    return f"2026-01-10T{hour:02d}{rest}"


def run_at(t, d="d1", i="t0", **k):
    e = ran(d, i)
    e["time"] = t
    if "cls" in k:
        e["agentcesourceclass"] = k["cls"]
        e["source"] = f"urn:src:{k['cls']}"
    return e


def login_at(t, i="s1", who="u2"):
    e = idp(i, who)
    e["time"] = t
    return e


def approve_at(t, **k):
    e = appr("approve", **k)
    e["time"] = t
    return e


# The probe: the action ran at 00:00, the login and the approval came hours later.
LATE = [dec(), run_at(at(0)), login_at(at(4)), approve_at(at(5))]
case("cnd02-approve-after-action-ran", "CND-02", LATE, NC, ["d1"])
case("ovs08-approve-after-action-ran", "OVS-08", LATE, NC, ["d1"])
case(
    "ovs08-nist-approve-after-action-ran",
    "OVS-08",
    LATE,
    NC,
    ["d1"],
    profile="nist",
    exit=1,
)
case(
    "cnd02-approve-before-action-ran",
    "CND-02",
    [dec(), login_at(at(1)), approve_at(at(2)), run_at(at(3))],
    "conformant",
)
case(
    "cnd02-approve-same-time-as-action",
    "CND-02",
    [dec(), run_at(at(0)), login_at(at(0)), approve_at(at(0))],
    "conformant",
)
# One second, or one millisecond, after the action is after it.
case(
    "cnd02-approve-one-second-after",
    "CND-02",
    [dec(), run_at(at(0)), login_at(at(0)), approve_at(at(0, ":00:01Z"))],
    NC,
    ["d1"],
)
case(
    "cnd02-approve-a-millisecond-after",
    "CND-02",
    [
        dec(),
        run_at(at(0)),
        login_at(at(0, ":00:00.001Z")),
        approve_at(at(0, ":00:00.001Z")),
    ],
    NC,
    ["d1"],
)
case(
    "cnd02-approve-same-instant-trailing-zeros",
    "CND-02",
    [
        dec(),
        run_at(at(0, ":00:00.5Z")),
        login_at(at(0, ":00:00.500Z")),
        approve_at(at(0, ":00:00.500Z")),
    ],
    "conformant",
)
# Instants, not text: 00:30-05:00 is 05:30Z, after a 01:00Z action though it sorts first as text; 05:00+04:00 is
# 01:00Z, before a 03:00Z action though it sorts last; 00:00-05:00 is the same instant as 05:00Z.
case(
    "cnd02-approve-after-in-another-offset",
    "CND-02",
    [
        dec(),
        run_at(at(1)),
        login_at(at(0, ":30:00-05:00")),
        approve_at(at(0, ":30:00-05:00")),
    ],
    NC,
    ["d1"],
)
case(
    "cnd02-approve-before-in-another-offset",
    "CND-02",
    [
        dec(),
        run_at(at(3)),
        login_at(at(5, ":00:00+04:00")),
        approve_at(at(5, ":00:00+04:00")),
    ],
    "conformant",
)
case(
    "cnd02-approve-same-instant-another-offset",
    "CND-02",
    [
        dec(),
        run_at(at(5)),
        login_at(at(0, ":00:00-05:00")),
        approve_at(at(0, ":00:00-05:00")),
    ],
    "conformant",
)
# Several actions: the approval must come before the first one.
case(
    "cnd02-approve-between-two-actions",
    "CND-02",
    [dec(), run_at(at(0)), login_at(at(2)), approve_at(at(2)), run_at(at(4), i="t1")],
    NC,
    ["d1"],
)
# A self-reported record of the action running before the approval is still a record that it ran ungated.
case(
    "cnd02-self-reported-action-before-approval",
    "CND-02",
    [
        dec(),
        run_at(at(0), i="t9", cls="self_report"),
        login_at(at(2)),
        approve_at(at(2)),
        run_at(at(3)),
    ],
    NC,
    ["d1"],
)
# Several approvals: one in force when the action ran is enough outside dual control.
case(
    "cnd02-one-approval-before-one-after",
    "CND-02",
    [
        dec(),
        login_at(at(1)),
        login_at(at(1), i="s2", who="u3"),
        approve_at(at(1)),
        run_at(at(2)),
        approve_at(at(3), i="a2", who="u3", s="s2"),
    ],
    "conformant",
)
# Another decision's action and approval do not move this one's order.
case(
    "cnd02-other-decision-action-earlier",
    "CND-02",
    [
        dec(),
        dec("d2"),
        run_at(at(0), d="d2", i="t2"),
        login_at(at(0)),
        approve_at(at(0), i="a2", d="d2"),
        approve_at(at(2)),
        run_at(at(3)),
    ],
    "conformant",
)
# Dual control: both approvers must be in force when the action runs.
TWO_LOGINS = [login_at(at(1)), login_at(at(1), i="s2", who="u3")]
case(
    "ovs08-dual-one-before-one-after",
    "OVS-08",
    [
        dual(),
        *TWO_LOGINS,
        approve_at(at(1)),
        run_at(at(2)),
        approve_at(at(3), i="a2", who="u3", s="s2"),
    ],
    NC,
    ["d1"],
)
case(
    "ovs08-dual-both-before",
    "OVS-08",
    [
        dual(),
        *TWO_LOGINS,
        approve_at(at(1)),
        approve_at(at(2), i="a2", who="u3", s="s2"),
        run_at(at(2)),
    ],
    "conformant",
)
# No action ran: an approval at any time covers the decision.
case(
    "ovs08-approve-no-action",
    "OVS-08",
    [dec(), login_at(at(4)), approve_at(at(5))],
    "conformant",
)
# A review is a review whenever it comes.
case("ovs01-approve-after-action-is-a-review", "OVS-01", LATE, "conformant")
case("rsk02-approve-after-action-is-a-review", "RSK-02", LATE, "conformant")
# A time with no offset cannot be placed against the action's: it gates nothing.
case(
    "cnd02-approve-time-without-offset",
    "CND-02",
    [dec(), run_at(at(5)), login_at(at(0)), approve_at(at(0, ":00:01"))],
    NC,
    ["d1"],
)
# d1's ToolCall (01:00) arrives after d2's (03:00) in the enforcement point's stream: ingest quarantines it for
# time_order (with its CND-02 gate, recorded at its time), and judged with it the approve at 02:00 came after the
# action (18.119).
case(
    "cnd02-approve-after-action-quarantined-time-order",
    "CND-02",
    [
        dec(),
        dec("d2"),
        run_at(at(3), d="d2", i="t2"),
        run_at(at(1)),
        login_at(at(0)),
        approve_at(at(2), i="a2", d="d2"),
        approve_at(at(2)),
    ],
    "insufficient_evidence",
    quarantined=2,
)

write()
