"""Write cases.json for VG-APPROVAL-WITHDRAWAL (18.142). Run from this folder: python make_cases.py.

Each human's own most recent decision on a decision governs whether that human approved it for CND-02 and OVS-08: a
human's later reject (or edit) withdraws that human's earlier approve, and a later approve after that human's reject
still gates. Only a decision recorded at or before the action counts: a reject recorded after every ToolCall that
executes the decision cannot withdraw the approval the action ran under, while one between two actions can (the
second action ran without it). One human's approve and reject at the same instant, or at times that cannot be placed
against each other, is no approval (fail closed). Times compare as instants, never as text. Two distinct humans who
each decide once are unaffected: approve(A) plus reject(B) stays conformant for CND-02 outside dual control, and
OVS-08 dual control still needs two approvals. Event times stay non-decreasing within each stream (one per source
class), so ingest quarantines nothing for order.
Quarantine never gives a better outcome (18.119): the same human's reject spelled "Reject" (quarantined
schema_invalid) or sharing the approve's id (quarantined duplicate_id) leaves CND-02 insufficient_evidence, not
conformant."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import (  # noqa: E402
    MISSING,
    NC,
    TWO,
    appr,
    case,
    dec,
    dual,
    ran,
    write,
)


def at(hour, rest=":00:00Z"):
    return f"2026-01-10T{hour:02d}{rest}"


def run_at(t, i="t0", d="d1"):
    e = ran(d, i)
    e["time"] = t
    return e


def decide(outcome, t, i, who="u2", s="s1", d="d1"):
    e = appr(outcome, i=i, d=d, who=who, s=s)
    e["time"] = t
    return e


def u3(outcome, t, i, d="d1"):
    return decide(outcome, t, i, who="u3", s="s2", d=d)


def streamed(e, cls):
    """Move a record into the stream of source class cls."""
    e["source"] = f"urn:src:{cls}"
    e["agentcesourceclass"] = cls
    return e


def no_login(e):
    del e["data"]["session_ref"]
    return e


# One human, two decisions before the action: the later one governs.
case(
    "cnd02-same-human-approve-then-reject",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("reject", at(2), "a2"), run_at(at(3))],
    NC,
    ["d1"],
)
case(
    "cnd02-same-human-reject-then-approve",
    "CND-02",
    [dec(), *TWO, decide("reject", at(1), "a1"), decide("approve", at(2), "a2"), run_at(at(3))],
    "conformant",
)
case(
    "cnd02-same-human-approve-then-edit",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("edit", at(2), "a2"), run_at(at(3))],
    NC,
    ["d1"],
)
case(
    "cnd02-same-human-approve-reject-approve",
    "CND-02",
    [
        dec(),
        *TWO,
        decide("approve", at(1), "a1"),
        decide("reject", at(2), "a2"),
        decide("approve", at(3), "a3"),
        run_at(at(4)),
    ],
    "conformant",
)
# A reject after the action ran does not withdraw the approval it ran under; one at the action's own instant does.
case(
    "cnd02-same-human-approve-action-then-reject",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), run_at(at(2)), decide("reject", at(3), "a2")],
    "conformant",
)
case(
    "cnd02-same-human-reject-same-time-as-action",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), run_at(at(2)), decide("reject", at(2), "a2")],
    NC,
    ["d1"],
)
# A reject between two actions: the second ran after the approval was withdrawn.
case(
    "cnd02-same-human-reject-between-two-actions",
    "CND-02",
    [
        dec(),
        *TWO,
        decide("approve", at(1), "a1"),
        run_at(at(2)),
        decide("reject", at(3), "a2"),
        run_at(at(4), i="t1"),
    ],
    NC,
    ["d1"],
)
# Same instant, or times that cannot be placed against each other: no approval.
case(
    "cnd02-same-human-approve-reject-same-time",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("reject", at(1), "a2"), run_at(at(2))],
    NC,
    ["d1"],
)
case(
    "cnd02-same-human-reject-time-without-offset",
    "CND-02",
    [
        dec(),
        *TWO,
        decide("approve", at(1), "a1"),
        decide("reject", at(2, ":00:00"), "a2"),
        run_at(at(3)),
    ],
    NC,
    ["d1"],
)
# Instants, not text: 03:00+02:00 is 01:00Z. The two records sit in two streams (the reject at the enforcement point),
# so each stream stays in order as instants and as text, while their text order is the reverse of their instants.
case(
    "cnd02-same-human-reject-later-in-another-offset",
    "CND-02",
    [
        dec(),
        *TWO,
        decide("approve", at(3, ":00:00+02:00"), "a1"),
        streamed(decide("reject", at(2, ":30:00Z"), "a2"), "enforcement_point"),
        run_at(at(4)),
    ],
    NC,
    ["d1"],
)
case(
    "cnd02-same-human-approve-later-in-another-offset",
    "CND-02",
    [
        dec(),
        *TWO,
        decide("approve", at(2, ":30:00Z"), "a1"),
        streamed(decide("reject", at(3, ":00:00+02:00"), "a2"), "enforcement_point"),
        run_at(at(4)),
    ],
    "conformant",
)
# The human's own later no withdraws the approve even when that record fails the SPEC §10.4 rule itself: it names no
# login, cites another human's login, or carries the human's id with padding. Honouring it can only make a verdict
# stricter (fail closed). A self-reported no with a genuine login, and a decision with no outcome, withdraw as well.
case(
    "cnd02-same-human-reject-without-login",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), no_login(decide("reject", at(2), "a2")), run_at(at(3))],
    NC,
    ["d1"],
)
case(
    "cnd02-same-human-reject-citing-another-login",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("reject", at(2), "a2", s="s2"), run_at(at(3))],
    NC,
    ["d1"],
)
case(
    "cnd02-same-human-reject-self-reported",
    "CND-02",
    [
        dec(),
        *TWO,
        decide("approve", at(1), "a1"),
        streamed(decide("reject", at(2), "a2"), "self_report"),
        run_at(at(3)),
    ],
    NC,
    ["d1"],
)
case(
    "cnd02-same-human-reject-padded-id",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("reject", at(2), "a2", who=" u2 "), run_at(at(3))],
    NC,
    ["d1"],
)
case(
    "cnd02-same-human-approve-then-no-outcome",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide(MISSING, at(2), "a2"), run_at(at(3))],
    NC,
    ["d1"],
)
# An approve that fails the rule restores nothing: the human's verified reject still governs.
case(
    "cnd02-same-human-reject-then-approve-without-login",
    "CND-02",
    [
        dec(),
        *TWO,
        decide("approve", at(1), "a1"),
        decide("reject", at(2), "a2"),
        no_login(decide("approve", at(3), "a3")),
        run_at(at(4)),
    ],
    NC,
    ["d1"],
)
# Two distinct humans who each decide once: one approve is enough outside dual control.
case(
    "cnd02-approve-then-reject",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), u3("reject", at(2), "a2"), run_at(at(3))],
    "conformant",
)
case(
    "cnd02-reject-then-approve",
    "CND-02",
    [dec(), *TWO, u3("reject", at(1), "a1"), decide("approve", at(2), "a2"), run_at(at(3))],
    "conformant",
)
# One human's reject on another decision does not withdraw this one.
case(
    "cnd02-same-human-reject-other-decision",
    "CND-02",
    [
        dec(),
        dec("d2"),
        *TWO,
        decide("approve", at(1), "a1"),
        u3("approve", at(1), "a3", d="d2"),
        decide("reject", at(2), "a2", d="d2"),
        run_at(at(3)),
        run_at(at(3), d="d2", i="t2"),
    ],
    "conformant",
)
# OVS-08: dual control still needs two approvers in force; a withdrawn one does not count.
case(
    "ovs08-dual-approve-and-reject",
    "OVS-08",
    [dual(), *TWO, decide("approve", at(1), "a1"), u3("reject", at(2), "a2"), run_at(at(3))],
    NC,
    ["d1"],
)
case(
    "ovs08-dual-one-approver-withdraws",
    "OVS-08",
    [
        dual(),
        *TWO,
        decide("approve", at(1), "a1"),
        u3("approve", at(1), "a2"),
        u3("reject", at(2), "a3"),
        run_at(at(3)),
    ],
    NC,
    ["d1"],
)
case(
    "ovs08-dual-one-approver-withdraws-and-restores",
    "OVS-08",
    [
        dual(),
        *TWO,
        decide("approve", at(1), "a1"),
        u3("approve", at(1), "a2"),
        u3("reject", at(2), "a3"),
        u3("approve", at(2, ":30:00Z"), "a4"),
        run_at(at(3)),
    ],
    "conformant",
)
case(
    "ovs08-same-human-approve-then-reject",
    "OVS-08",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("reject", at(2), "a2"), run_at(at(3))],
    NC,
    ["d1"],
)
case(
    "ovs08-nist-same-human-approve-then-reject",
    "OVS-08",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("reject", at(2), "a2"), run_at(at(3))],
    NC,
    ["d1"],
    profile="nist",
    exit=1,
)
# No action ran: the human's most recent decision governs.
case(
    "ovs08-same-human-approve-then-reject-no-action",
    "OVS-08",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("reject", at(2), "a2")],
    NC,
    ["d1"],
)
case(
    "ovs08-same-human-reject-then-approve-no-action",
    "OVS-08",
    [dec(), *TWO, decide("reject", at(1), "a1"), decide("approve", at(2), "a2")],
    "conformant",
)
# A withdrawn approval is still a review.
case(
    "ovs01-withdrawn-approval-is-a-review",
    "OVS-01",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("reject", at(2), "a2"), run_at(at(3))],
    "conformant",
)

# The same human's reject is quarantined, so the withdrawal it records is out of sight; the control does not pass.
case(
    "cnd02-same-human-reject-spelled-Reject-quarantined",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("Reject", at(2), "a2"), run_at(at(3))],
    "insufficient_evidence",
    quarantined=1,
)
case(
    "cnd02-same-human-reject-shares-the-approve-id-quarantined",
    "CND-02",
    [dec(), *TWO, decide("approve", at(1), "a1"), decide("reject", at(2), "a1"), run_at(at(3))],
    "insufficient_evidence",
    quarantined=1,
)

write()
