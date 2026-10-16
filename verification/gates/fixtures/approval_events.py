"""Event builders and the case table shared by the approval gate fixtures (VG-APPROVAL-OUTCOME, VG-APPROVAL-ORDER):
one subject, a high-effect decision, its enforcement-point action, identity-provider logins and the ApprovalDecided
records on it. Every approver meets the SPEC §10.4 human-actor rule unless a case says otherwise; `at` sets an event's
time (all at AT by default)."""

import copy
import hashlib
import json
from pathlib import Path

SUBJECT = "spiffe://corp/agents/approval-outcome"
CTX = "https://agent-conformance.org/contexts/evidence/v1"
CLASS = {"Decision": "self_report", "ToolCall": "enforcement_point"}
CHAIN = ["urn:example:service:orchestrator", "urn:example:person:owner-1"]
IDP = "urn:example:service:identity-provider"
NC = "non-conformant"
MISSING = object()
AT = "2026-01-10T00:00:00Z"


def ev(i, t, at=AT, **data):
    cls = data.pop("cls", CLASS.get(t, "independent_system"))
    return {
        "specversion": "1.0",
        "id": i,
        "source": f"urn:src:{cls}",
        "subject": SUBJECT,
        "type": f"org.agent-conformance.evidence.{t}.v1",
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": cls,
        "time": at,
        "data": {"@context": CTX, "@type": t, "agent": {"id": SUBJECT}, **data},
    }


def ref(i):
    return f"agentce:event/{i}"


def dec(i="d1", **k):
    return ev(i, "Decision", decision_type="dom:CreditDecision", acted_for=CHAIN, **k)


def dual(i="d1"):
    return dec(i, oversight_modality="dual_control")


def ran(d="d1", i="t0", **data):
    """The enforcement point executed the decision's action."""
    return ev(i, "ToolCall", tool={"name": "x"}, refs={"decision": ref(d)}, **data)


def idp(i="s1", who="u2"):
    """The identity-provider login of human `who` (SPEC §10.4)."""
    e = ev(
        i,
        "SessionStart",
        environment="idp-login",
        principal={"id": who, "kind": "human"},
    )
    e["data"]["agent"] = {"id": IDP}
    return e


def appr(outcome, i="a1", d="d1", who="u2", s="s1", kind="human"):
    """An ApprovalDecided on decision d by `who` through login s; outcome=MISSING leaves the outcome out."""
    data = {
        "refs": {"decision": ref(d)},
        "actor": {"id": who, "kind": kind, "authority_ref": "ref:authority/reviewers"},
        "session_ref": ref(s),
    }
    if outcome is not MISSING:
        data["outcome"] = outcome
    return ev(i, "ApprovalDecided", **data)


def incident():
    return ev(
        "i1",
        "Incident",
        incident_id="i1",
        incident_class="fundamental_rights",
        related_refs=[ref("d1")],
        detected_at="2026-01-10T00:00:00Z",
    )


def gated(events, ruling="require_approval"):
    """CND-02 judges actions, not decisions (SPEC §7.7.4; 18.187): each ToolCall here gains a high-effect class
    (irreversible) when it gives none, and an enforcement-point PolicyDecision ruling `ruling` on it, read just before
    its first record and at its time, so a case still judges the approval defect it was built for rather than a
    missing gate. A line that is not a ToolCall record (an unreadable line, another type) passes as it is."""
    out, named = [], set()
    for e in events:
        if isinstance(e, dict) and str(e.get("type", "")).endswith(".ToolCall.v1"):
            e = copy.deepcopy(e)
            e["data"].setdefault("effect_class", "irreversible")
            if e["id"] not in named:
                named.add(e["id"])
                gate = ev(f"{e['id']}-gate", "PolicyDecision", at=e["time"], cls="enforcement_point")
                gate["data"].update(decision=ruling, refs={"request": ref(e["id"])})
                out.append(gate)
        out.append(e)
    return out


def is_cnd02(control):
    return "CND-02" in (control if isinstance(control, list) else [control])


def cnd02(control, events):
    """`events`, gated when `control` is or includes CND-02."""
    return gated(events) if is_cnd02(control) else events


def acting(events, failing):
    """CND-02's focus is the action, not the decision: each failing decision id becomes the ToolCalls whose
    refs.decision names it (its IRI or bare id, padded or not), sorted. An id that is no decision stays as it is."""
    records = [e for e in events if isinstance(e, dict)]
    decisions = {e["id"] for e in records if e["data"].get("@type") == "Decision"}
    calls = [e for e in records if e["data"].get("@type") == "ToolCall"]
    out = {f for f in failing if f not in decisions}
    for f in decisions.intersection(failing):
        for e in calls:
            name = e["data"].get("refs", {}).get("decision")
            if isinstance(name, str) and name.strip() in (f, ref(f)):
                out.add(e["id"])
    return sorted(out)


def acted_last(events, run):
    """`events`, then each decision's own action, run last at 23:00: t9 executes d1 and t8 executes d2, built by
    run(time, decision, id). CND-02 judges actions (SPEC §7.7.4; 18.187), so a decision needs one to be judged at all;
    run last, the action's time never bounds the approval, and the case judges what its other records bound."""
    held = [e["id"] for e in events if e["data"]["@type"] == "Decision"]
    return [*events, *(run("2026-01-10T23:00:00Z", d, {"d1": "t9", "d2": "t8"}[d]) for d in held)]


C = {}


def case(
    name,
    control,
    events,
    outcome,
    failing=(),
    quarantined=0,
    profile="eu",
    exit=2,
    integrity=None,
    streams=None,
    files=None,
    trust=None,
):
    C[name] = {
        "control": control,
        "profile": profile,
        "events": cnd02(control, events),
        "expected": {
            "outcome": outcome,
            "failing": acting(events, failing) if is_cnd02(control) else list(failing),
            "quarantined": quarantined,
            "exit": exit,
        },
    }
    if integrity is not None:
        C[name]["expected"]["integrity"] = integrity
    if streams is not None:
        C[name]["streams"] = streams
    if files is not None:  # a case's other bundle files (signature envelopes)
        C[name]["files"] = files
    if trust is not None:  # the trust root the run is given (--trust-root)
        C[name]["trust_root"] = trust


TWO = [idp("s1"), idp("s2", who="u3")]


# Source signatures (SPEC §6.6, 18.155): a source_signed record counts only when its sig_ref envelope verifies against
# the run's own trust root (--trust-root) and its in-toto Statement names the record's hash or a later hash of its
# chain. The keys are fixed Ed25519 keys derived from a label, so cases.json is reproducible (Ed25519 is
# deterministic); signing needs the engine's own module, so a make_cases.py that signs runs in the Python engine's
# environment (uv run --project engines/python).
def source_signer(label="source"):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from agentce import signing

    seed = hashlib.sha256(f"agentce gate fixture {label} key".encode()).digest()
    return signing.KmsSigner(Ed25519PrivateKey.from_private_bytes(seed))


def envelope(hashes, signer):
    """A DSSE envelope `signer` signs over an in-toto Statement naming `hashes` as its subjects, as file text."""
    from agentce import signing

    statement = {
        "_type": signing.INTOTO_STATEMENT_TYPE,
        "subject": [{"name": "stream-segment", "digest": {"sha256": h}} for h in hashes],
        "predicateType": "https://agent-conformance.org/attestations/stream-segment/v1",
        "predicate": {},
    }
    return json.dumps(signing.sign_statement(statement, signer), indent=2) + "\n"


def trust_root(signer):
    """The trust root document that trusts `signer`'s key alone (signing.TrustRoot.document's shape)."""
    from agentce import signing

    return signing.TrustRoot.document(signer.keyid, signer.public_key_b64, "urn:src:enforcement_point")


def write():
    Path("cases.json").write_text(
        json.dumps({"cases": C}, indent=2) + "\n", encoding="utf-8"
    )
    print(len(C), "cases")
