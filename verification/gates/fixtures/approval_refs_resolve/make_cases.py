"""Write cases.json for VG-APPROVAL-REFS-RESOLVED (18.148). Run from this folder: python make_cases.py.

The approval bound (CND-02, OVS-08; 18.141's order rule, 18.142's withdrawal rule, 18.144's outcome rule) groups the
ToolCalls, Outcomes and ApprovalDecided events that name a decision. A refs.decision names the held decision when it
is the decision's IRI (agentce:event/<id>) or its bare id, either one padded with whitespace at either end (SPEC §6.3:
event IRIs are agentce:event/<id>); each such name is resolved to the decision's IRI before any grouping, so a
ToolCall or Outcome naming the decision in another form still bounds its approvals, and a reject in another form still
withdraws. A refs.decision that names no held decision (an unheld id, another event type's id, a different case, an
empty string) on an ApprovalDecided reviews and approves no decision; on a ToolCall or Outcome it may be any decision's
effect, so it bounds the approvals of every held decision (fails closed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import (  # noqa: E402
    NC,
    TWO,
    appr,
    case,
    dec,
    dual,
    ev,
    idp,
    ref,
    write,
)


def at(hour):
    return f"2026-01-10T{hour:02d}:00:00Z"


def timed(e, t):
    e["time"] = t
    return e


def named(e, name):
    """The event with its refs.decision written as `name`, verbatim."""
    e["data"]["refs"]["decision"] = name
    return e


def outcome_at(t, name, i="o1"):
    """The decision's Outcome (the record that it took effect), naming it as `name`."""
    return ev(i, "Outcome", at=t, refs={"decision": name})


def run_at(t, name, i="t0"):
    """The enforcement point executed the decision's action, naming it as `name`."""
    return ev(i, "ToolCall", at=t, tool={"name": "x"}, refs={"decision": name})


def login_at(t, i="s1", who="u2"):
    return timed(idp(i, who), t)


def approve_at(t, name=ref("d1"), **k):
    return timed(named(appr("approve", **k), name), t)


def reject_at(t, name=ref("d1"), **k):
    return timed(named(appr("reject", **k), name), t)


def unlinked():
    """An enforcement-point ToolCall that executes no decision: CND-02's minimum evidence."""
    return ev("t9", "ToolCall", tool={"name": "lookup"})


def cnd02(name, events, outcome, failing=()):
    """A CND-02 case: the decision, the unlinked ToolCall, then the events."""
    case(name, "CND-02", [events[0], unlinked(), *events[1:]], outcome, failing)


# The probes (p2, q14, q15): the decision took effect at 01:00, named in another form; the approval came at 05:00.
cnd02(
    "cnd02-approve-after-outcome-bareid",
    [dec(), outcome_at(at(1), "d1"), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
case(
    "cnd02-approve-after-toolcall-bareid",
    "CND-02",
    [dec(), run_at(at(1), "d1"), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-after-outcome-trailing-space",
    [dec(), outcome_at(at(1), "agentce:event/d1 "), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-after-toolcall-leading-space-bareid",
    [dec(), run_at(at(1), "\td1"), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-after-outcome-nbsp",
    [dec(), outcome_at(at(1), " agentce:event/d1"), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
# The approval names the decision in another form; the bound holds whichever form the action uses.
cnd02(
    "cnd02-bareid-approve-after-outcome",
    [
        dec(),
        outcome_at(at(1), "agentce:event/d1"),
        login_at(at(4)),
        approve_at(at(5), "d1"),
    ],
    NC,
    ["d1"],
)
case(
    "cnd02-bareid-approve-before-toolcall",
    "CND-02",
    [
        dec(),
        login_at(at(1)),
        approve_at(at(1), "d1"),
        run_at(at(2), "agentce:event/d1"),
    ],
    "conformant",
)
cnd02(
    "cnd02-padded-approve-before-bareid-outcome",
    [
        dec(),
        login_at(at(1)),
        approve_at(at(1), " agentce:event/d1 "),
        outcome_at(at(2), "d1"),
    ],
    "conformant",
)
# A resolved bare id keeps the bound per decision: another decision's bare-id Outcome does not bound this one, and a
# reject after a bare-id ToolCall of the decision came after the action ran, so it withdraws nothing (18.142).
cnd02(
    "cnd02-other-decision-bareid-outcome-earlier",
    [
        dec(),
        dec("d2"),
        outcome_at(at(1), "d2", i="o2"),
        login_at(at(4)),
        approve_at(at(5)),
    ],
    NC,
    ["d2"],
)
case(
    "cnd02-reject-after-bareid-toolcall-withdraws-nothing",
    "CND-02",
    [
        dec(),
        login_at(at(1)),
        approve_at(at(1)),
        run_at(at(2), "d1"),
        reject_at(at(3), i="a2"),
    ],
    "conformant",
)
# A same human's reject in another form still withdraws the approve (18.142).
case(
    "cnd02-reject-bareid-withdraws",
    "CND-02",
    [
        dec(),
        login_at(at(1)),
        approve_at(at(1)),
        reject_at(at(2), "d1", i="a2"),
        run_at(at(3), "agentce:event/d1"),
    ],
    NC,
    ["d1"],
)
# A name that leads to no held decision approves nothing and bounds nothing.
cnd02(
    "cnd02-approve-unresolved-decision",
    [dec(), login_at(at(1)), approve_at(at(1), "agentce:event/d9")],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-names-another-event-type",
    [dec(), login_at(at(1)), approve_at(at(1), "agentce:event/t9")],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-names-decision-in-another-case",
    [dec(), login_at(at(1)), approve_at(at(1), "AGENTCE:EVENT/D1")],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-empty-name",
    [dec(), login_at(at(1)), approve_at(at(1), "  ")],
    NC,
    ["d1"],
)
# A ToolCall or Outcome whose refs.decision names no held decision may be any decision's effect, so it bounds every
# held decision's approvals (fails closed, as INC-03 holds every decision to its rule when a name leads nowhere).
cnd02(
    "cnd02-unheld-outcome-bounds-every-decision",
    [dec(), outcome_at(at(1), "agentce:event/d9"), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-after-outcome-in-another-case",
    [dec(), outcome_at(at(1), "AGENTCE:EVENT/D1"), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
case(
    "cnd02-approve-after-toolcall-urn",
    "CND-02",
    [dec(), run_at(at(1), "urn:decision:d1"), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
# A bare id resolves against held Decisions only: one naming another held event (the unlinked ToolCall t9, an Outcome
# of no decision) names no held decision and bounds every decision's approvals.
cnd02(
    "cnd02-approve-after-outcome-names-toolcall-bareid",
    [dec(), outcome_at(at(1), "t9"), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-after-toolcall-names-outcome-bareid",
    [
        dec(),
        ev("o9", "Outcome", at=at(0)),
        run_at(at(1), "o9"),
        login_at(at(4)),
        approve_at(at(5)),
    ],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-approve-after-outcome-empty-name",
    [dec(), outcome_at(at(1), ""), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
# It is not known to be the decision's action, so a reject after it still withdraws (18.142 fails closed): the
# withdrawal rule's "after every action" reads only the ToolCalls that name the decision.
case(
    "cnd02-reject-after-unheld-toolcall-still-withdraws",
    "CND-02",
    [
        dec(),
        login_at(at(1)),
        approve_at(at(1)),
        run_at(at(2), "urn:decision:d1"),
        reject_at(at(3), i="a2"),
    ],
    NC,
    ["d1"],
)
cnd02(
    "cnd02-unheld-outcome-after-approve",
    [dec(), login_at(at(1)), approve_at(at(1)), outcome_at(at(2), "agentce:event/d9")],
    "conformant",
)
# OVS-08: the same bound, and dual control counts distinct humans whichever form each names the decision in.
case(
    "ovs08-approve-after-outcome-bareid",
    "OVS-08",
    [dec(), outcome_at(at(1), "d1"), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
case(
    "ovs08-dual-two-forms-both-before",
    "OVS-08",
    [
        dual(),
        *TWO,
        approve_at(at(0)),
        approve_at(at(0), "d1 ", i="a2", who="u3", s="s2"),
        run_at(at(1), "agentce:event/d1"),
    ],
    "conformant",
)
case(
    "ovs08-dual-same-human-two-forms",
    "OVS-08",
    [
        dual(),
        TWO[0],
        approve_at(at(0)),
        approve_at(at(0), "d1", i="a2"),
        run_at(at(1), "agentce:event/d1"),
    ],
    NC,
    ["d1"],
)
# OVS-01: an approval naming the decision by its bare id reviews it, even when it came late.
case(
    "ovs01-bareid-approve-is-a-review",
    "OVS-01",
    [dec(), outcome_at(at(1), "d1"), login_at(at(4)), approve_at(at(5), "d1")],
    "conformant",
)

# OVS-07 (verifier 18.148 r1): an Interrupt's human-actor check still counts the agents of the event its
# refs.decision names when that event is no Decision, so a login from that agent's own session is no human's login.
WORKER = "spiffe://corp/agents/worker"


def interrupt_on(target, login):
    """A ToolCall t2 by the worker agent, and an Interrupt by u2 through `login` whose refs.decision names `target`."""
    run = ev("t2", "ToolCall", tool={"name": "x"}, agent={"id": WORKER})
    stop = ev(
        "x1",
        "Interrupt",
        agent={"id": "spiffe://corp/agents/other"},
        mechanism="kill_switch",
        effect="halted",
        actor={"id": "u2", "kind": "human", "authority_ref": "ref:authority/reviewers"},
        session_ref=ref("s2"),
        refs={"decision": target},
    )
    return [dec(), login, run, stop]


def worker_login():
    login = idp("s2")
    login["data"]["agent"] = {"id": WORKER}
    return login


case(
    "ovs07-interrupt-names-toolcall-login-is-its-agent",
    "OVS-07",
    interrupt_on(ref("t2"), worker_login()),
    NC,
    ["x1"],
)
case(
    "ovs07-interrupt-names-toolcall-bareid-login-is-its-agent",
    "OVS-07",
    interrupt_on("t2", worker_login()),
    NC,
    ["x1"],
)
case(
    "ovs07-interrupt-names-toolcall-padded-login-is-its-agent",
    "OVS-07",
    interrupt_on(" agentce:event/t2\t", worker_login()),
    NC,
    ["x1"],
)
case(
    "ovs07-interrupt-names-toolcall-idp-login",
    "OVS-07",
    interrupt_on(ref("t2"), idp("s2")),
    "conformant",
)

write()
