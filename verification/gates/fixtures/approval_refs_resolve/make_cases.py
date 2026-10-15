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


def cnd02(name, events, outcome, failing=()):
    """A CND-02 case whose decisions' own actions run last, after every other record: t9 executes d1 and t8 executes d2
    at 23:00. CND-02 judges actions (SPEC §7.7.4; 18.187), so a decision needs one to be judged at all; run last, the
    action's time never bounds the approval and the bound under test is the Outcome's or the
    approval's name."""
    acts = {"d1": "t9", "d2": "t8"}
    held = [e["id"] for e in events if e["data"]["@type"] == "Decision"]
    last = [run_at("2026-01-10T23:00:00Z", ref(d), acts[d]) for d in held]
    case(name, "CND-02", [*events, *last], outcome, failing)


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
    ["t0"],
)
# A bare id resolves against held Decisions only: one naming another held event (d1's own ToolCall t9, an Outcome
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
    ["t0", "t9"],
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
    ["t0"],
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

# OVS-07 (18.158): an Interrupt whose refs.decision names no held event (another letter case, a misspelt IRI, an
# unheld id, null, empty or blank) may concern any held event, so the human-actor check counts the agents of every held
# event but its own login: a login whose agent any other held event carries is that agent's session (fails closed).
# A login through an identity provider seen nowhere else stays a human's; an Interrupt with no refs.decision key makes
# no claim and still counts only its own agents.
UNRESOLVED = {
    "uppercase": "agentce:event/T2",
    "garbled": "agentce:evnt/t2",
    "unresolved": "nope",
    "null": None,
    "empty": "",
    "spaces": "   ",
    "nbsp": "\u00a0agentce:event/T2",
    "bom": "\ufeffnope",
    "prefix-only": "agentce:event/",
}
for label, target in UNRESOLVED.items():
    case(
        f"ovs07-interrupt-names-toolcall-{label}-login-is-its-agent",
        "OVS-07",
        interrupt_on(target, worker_login()),
        NC,
        ["x1"],
    )
    case(
        f"ovs07-interrupt-names-toolcall-{label}-idp-login",
        "OVS-07",
        interrupt_on(target, idp("s2")),
        "conformant",
    )
case(
    "ovs07-interrupt-names-toolcall-unresolved-second-idp-login-fails-closed",
    "OVS-07",
    [*interrupt_on("nope", idp("s2")), idp("s3", "u3")],
    NC,
    ["x1"],
)


def worker_session_only(target, principal=None):
    """The worker agent's own run session s9 (naming `principal` when given) in place of its ToolCall, and an Interrupt
    naming `target`."""
    events = interrupt_on(target, worker_login())
    events[2] = ev("s9", "SessionStart", agent={"id": WORKER}, environment="run")
    if principal is not None:
        events[2]["data"]["principal"] = {"id": principal, "kind": "human"}
    return events


for name, target, principal in (
    ("run-session-naming-a-human-unresolved", "nope", "u7"),
    ("run-session-naming-a-human-names-it", ref("s9"), "u7"),
    ("names-run-session-unresolved", "nope", None),
):
    case(
        f"ovs07-interrupt-{name}-login-is-its-agent",
        "OVS-07",
        worker_session_only(target, principal),
        NC,
        ["x1"],
    )
# An agent seen only in the login itself: named, it is refused; misnamed, its data is the identity provider's own
# single login (the -idp-login cases), so no rule can refuse it there without refusing every such login.
login_only = interrupt_on(ref("s2"), worker_login())
del login_only[2]
case(
    "ovs07-interrupt-names-its-login-agent-seen-only-there",
    "OVS-07",
    login_only,
    NC,
    ["x1"],
)
# The ToolCall acted for u2: the subject's chain holds every event's acted_for, so this reads non-conformant at base too.
in_chain = interrupt_on("nope", idp("s2"))
in_chain[2]["data"]["acted_for"] = ["u2"]
case(
    "ovs07-interrupt-names-toolcall-unresolved-actor-in-its-chain",
    "OVS-07",
    in_chain,
    NC,
    ["x1"],
)
stop_first = interrupt_on("nope", worker_login())
case(
    "ovs07-interrupt-names-toolcall-unresolved-before-its-toolcall-login-is-its-agent",
    "OVS-07",
    [stop_first[3], *stop_first[:3]],
    NC,
    ["x1"],
)
no_ref = interrupt_on("nope", worker_login())
del no_ref[3]["data"]["refs"]
case(
    "ovs07-interrupt-without-decision-ref-worker-login", "OVS-07", no_ref, "conformant"
)

# A present refs.decision that is null names no held decision, so a ToolCall or Outcome carrying it may be any
# decision's effect and bounds every held decision's approvals, as an unheld name does (18.157); it withdraws none.
# A ToolCall or Outcome with no refs.decision key makes no claim and stays no decision's action.
case(
    "cnd02-approve-after-toolcall-null-decision",
    "CND-02",
    [dec(), run_at(at(1), None), login_at(at(4)), approve_at(at(5))],
    NC,
    ["t0"],
)
cnd02(
    "cnd02-approve-after-outcome-null-decision",
    [dec(), outcome_at(at(1), None), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
case(
    "ovs08-approve-after-outcome-null-decision",
    "OVS-08",
    [dec(), outcome_at(at(1), None), login_at(at(4)), approve_at(at(5))],
    NC,
    ["d1"],
)
case(
    "cnd02-approve-before-null-decision-toolcall",
    "CND-02",
    [dec(), login_at(at(1)), approve_at(at(1)), run_at(at(2), None)],
    NC,
    ["t0"],
)
cnd02(
    "cnd02-null-outcome-bounds-every-decision",
    [
        dec(),
        dec("d2"),
        outcome_at(at(1), None),
        login_at(at(4)),
        approve_at(at(5)),
        approve_at(at(5), ref("d2"), i="a2"),
    ],
    NC,
    ["d1", "d2"],
)
case(
    "cnd02-reject-after-null-decision-toolcall-still-withdraws",
    "CND-02",
    [
        dec(),
        login_at(at(1)),
        approve_at(at(1)),
        run_at(at(2), None),
        reject_at(at(3), i="a2"),
    ],
    NC,
    ["t0"],
)
cnd02(
    "cnd02-approve-null-decision",
    [dec(), login_at(at(1)), approve_at(at(1), None)],
    NC,
    ["d1"],
)
case(
    "cnd02-approve-after-toolcall-without-decision-ref",
    "CND-02",
    [
        dec(),
        ev("t1", "ToolCall", at=at(1), tool={"name": "x"}, refs={}),
        login_at(at(4)),
        approve_at(at(5)),
    ],
    NC,
    ["t1"],
)
cnd02(
    "cnd02-approve-after-outcome-without-refs",
    [dec(), ev("o1", "Outcome", at=at(1)), login_at(at(4)), approve_at(at(5))],
    "conformant",
)

write()
