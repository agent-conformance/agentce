"""Write cases.json for VG-STREAM-SOURCE-BOUND (18.147). Run from this folder, in the Python engine's environment (the
signed cases sign with its signing module): uv run --project ../../../../engines/python python make_cases.py.

Ingest checks time order within a stream (SPEC §6.6: a stream is (source, subject)). The stream id a record claims in
data.integrity.stream names a stream of the record's own source only: a record never joins another source's ordering
group by naming that source's stream. Each hijack case reads exactly like its twin, where the same record claims no
stream, so the enforcement point's genuine ToolCall is never quarantined time_order and CND-02 still fails d1."""

import base64
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import (  # noqa: E402
    CHAIN,
    NC,
    SUBJECT,
    appr,
    case,
    dec,
    envelope,
    ev,
    idp,
    ran,
    source_signer,
    trust_root,
    write,
)

EP = "urn:src:enforcement_point"
LATER = "2026-01-10T01:00:00Z"


def claim(e, stream):
    """Give e an integrity block that claims `stream` (no chain; integrity is SHOULD for these classes)."""
    e["data"]["integrity"] = {
        "hash": hashlib.sha256(f"{e['id']} claims {stream}".encode()).hexdigest(),
        "prev": hashlib.sha256(b"no chain").hexdigest(),
        "stream": stream,
        "strength": "export_chained",
    }
    return e


def note(i="x9", at=LATER, stream=None, cls="self_report", source=None):
    """The agent's own routine Decision (not consequential, so CND-02 does not judge it) recorded later than the
    ToolCall; `stream` is the stream it claims, if any."""
    e = ev(i, "Decision", at=at, cls=cls, decision_type="dom:Lookup", acted_for=CHAIN)
    if source is not None:
        e["source"] = source
    return claim(e, stream) if stream is not None else e


# The repro: the agent's own Decision x9 claims the enforcement point's stream with a later time, read before the
# ToolCall t0. Bound to its own source, x9 orders nothing of the enforcement point's.
case(
    "cnd02-no-stream-hijack",
    "CND-02",
    [dec(), note(), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
)
case(
    "cnd02-self-report-stream-hijack",
    "CND-02",
    [dec(), note(stream=EP), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
)
# Source class: an undeclared source (trusted as self_report) and an independent_system source make the same claim.
case(
    "cnd02-unclassed-source-stream-hijack",
    "CND-02",
    [
        dec(),
        note(cls="independent_system", source="urn:src:unclassed", stream=EP),
        ran(),
        idp(),
        appr("reject"),
    ],
    NC,
    ["d1"],
)
case(
    "cnd02-independent-system-stream-hijack",
    "CND-02",
    [dec(), note(cls="independent_system", stream=EP), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
)
# The enforcement point's records carry their own integrity stream id, and the agent's record claims that id.
case(
    "cnd02-no-claimed-stream-hijack",
    "CND-02",
    [dec(), note(), claim(ran(), "urn:stream:gateway-1"), idp(), appr("reject")],
    NC,
    ["d1"],
)
case(
    "cnd02-claimed-stream-hijack",
    "CND-02",
    [
        dec(),
        note(stream="urn:stream:gateway-1"),
        claim(ran(), "urn:stream:gateway-1"),
        idp(),
        appr("reject"),
    ],
    NC,
    ["d1"],
)
# Arrival order: the claiming record after the ToolCall with an earlier time is its own source's record, not late.
case(
    "cnd02-stream-hijack-after-toolcall",
    "CND-02",
    [dec(), ran(), note(at="2026-01-09T23:00:00Z", stream=EP), idp(), appr("reject")],
    NC,
    ["d1"],
)
# Subject: the claiming record names another subject.
other = note(stream=EP)
other["subject"] = "spiffe://corp/agents/other"
other["data"]["agent"] = {"id": "spiffe://corp/agents/other"}
case(
    "cnd02-other-subject-stream-hijack",
    "CND-02",
    [dec(), other, ran(), idp(), appr("reject")],
    NC,
    ["d1"],
)
# Acted-on type: the identity provider's login and the reviewer's reject (independent_system) keep their place
# when the agent's record claims their stream; an approve through the login still gates the decision. The twin's record
# claims its own source's stream, so both carry the same unchained integrity block and give the same exit code.
IS = "urn:src:independent_system"
case(
    "cnd02-no-login-stream-hijack",
    "CND-02",
    [dec(), note(stream="urn:src:self_report"), ran(), idp(), appr("approve")],
    "conformant",
)
case(
    "cnd02-login-stream-hijack",
    "CND-02",
    [dec(), note(stream=IS), ran(), idp(), appr("approve")],
    "conformant",
)
case(
    "cnd02-reject-stream-hijack",
    "CND-02",
    [dec(), ran(), idp(), note(stream=IS), appr("reject")],
    NC,
    ["d1"],
)
# One source still keeps time order within a stream it claims: the enforcement point's own later record, then its
# ToolCall with an earlier time in the same claimed stream, quarantines the ToolCall as before; judged with the held
# records, the withheld ToolCall still fails d1 (quarantine never gives a better outcome, 18.119). Its CND-02 gate,
# recorded at its time after t9, is quarantined with it, and t9 itself executes no decision, so it is the failing action.
ep_late = claim(
    ev("t9", "ToolCall", at=LATER, tool={"name": "x"}), "urn:stream:gateway-1"
)
case(
    "cnd02-same-source-claimed-stream-time-order",
    "CND-02",
    [dec(), ep_late, claim(ran(), "urn:stream:gateway-1"), idp(), appr("reject")],
    NC,
    ["t9"],
    quarantined=2,
)
# Integrity chains (18.151) are bound the same way: SPEC §6.6 verifies a hash chain per stream, and a record's claimed
# stream names a chain of its own source. The gateway's two ToolCalls form a real export_chained chain; in the hijack
# case the agent's Decision x9, read between them, claims that chain with a hash that matches nothing. Each case's
# expected.integrity is every {stream, status} its integrity.jsonl holds, ordered as the engines write it; the
# genuine chain reads verified_weak in both, and the hijacker's own entry under the same claimed id reads failed.
GW = "urn:stream:gw"


def chained(e, prev, strength="export_chained"):
    """Give e a real link in GW: the SHA-256 of its RFC 8785 form without data.integrity (ASCII only, so sorted
    compact JSON is that form)."""
    text = json.dumps(e, sort_keys=True, separators=(",", ":"))
    e["data"]["integrity"] = {
        "hash": hashlib.sha256(text.encode()).hexdigest(),
        "prev": prev,
        "stream": GW,
        "strength": strength,
    }
    return e


def gateway(strength="export_chained"):
    """Each link carries its effect class before it is hashed (CND-02 judges the irreversible t0; t8 only reads)."""
    head = chained(ran(effect_class="irreversible"), "0" * 64, strength)
    tail = chained(
        ev("t8", "ToolCall", tool={"name": "x"}, effect_class="read"),
        head["data"]["integrity"]["hash"],
        strength,
    )
    return head, tail


def fallback(cls):
    """The stream of a source's records that claim none: (source, subject)."""
    return f"urn:src:{cls}|{SUBJECT}"


# The unchained streams: the enforcement point's (t0's CND-02 gate claims no stream), the identity provider's, the
# agent's.
UNCHAINED = [
    {"stream": fallback("enforcement_point"), "status": "failed"},
    {"stream": fallback("independent_system"), "status": "failed"},
    {"stream": fallback("self_report"), "status": "failed"},
]


def integrity_case(
    name, middle, integrity, strength="export_chained", anchor_at=None, gw=None, files=None, trust=None
):
    """The gateway's two-link GW chain (`gw`, else a fresh one) with `middle` (the agent's Decision) read between them;
    anchor_at, if given, makes the manifest anchor GW's head at that time; files and trust are the case's other bundle
    files and the trust root the run is given."""
    head, tail = gw if gw is not None else gateway(strength)
    streams = None
    if anchor_at is not None:
        streams = [
            {
                "stream": GW,
                "anchors": [
                    {"kind": "rfc3161", "ref": "anchors/gw-head.tsr", "at": anchor_at}
                ],
            }
        ]
    case(
        name,
        "CND-02",
        [dec(), head, middle, tail, idp(), appr("reject")],
        NC,
        ["d1"],
        integrity=UNCHAINED + integrity,
        streams=streams,
        files=files,
        trust=trust,
    )


integrity_case(
    "integrity-chain-no-hijack", note(), [{"stream": GW, "status": "verified_weak"}]
)
integrity_case(
    "integrity-chain-hijack",
    note(stream=GW),
    [{"stream": GW, "status": "verified_weak"}, {"stream": GW, "status": "failed"}],
)


# Anchors (SPEC §6.6): the manifest anchors GW's head, naming no source. The gateway's export_anchored chain reads
# verified alone. The agent's own record claims GW with a well-formed export_anchored link of its own (a genesis prev
# and its real hash), so it forms a chain that would borrow GW's anchors: with two sources on one id, neither chain
# reads verified (fails closed), and an anchor earlier than the records still marks every chain time_suspect.
def anchored(name, at, hijack, integrity):
    middle = chained(note(), "0" * 64, "export_anchored") if hijack else note()
    integrity_case(name, middle, integrity, "export_anchored", at)


ANCHOR_AT = "2026-01-10T02:00:00Z"
anchored(
    "integrity-anchor-no-hijack",
    ANCHOR_AT,
    False,
    [{"stream": GW, "status": "verified"}],
)
anchored(
    "integrity-anchor-hijack",
    ANCHOR_AT,
    True,
    [
        {"stream": GW, "status": "verified_weak"},
        {"stream": GW, "status": "verified_weak"},
    ],
)
EARLY_AT = "2026-01-09T23:00:00Z"
anchored(
    "integrity-anchor-time-suspect-no-hijack",
    EARLY_AT,
    False,
    [{"stream": GW, "status": "time_suspect"}],
)
anchored(
    "integrity-anchor-time-suspect-hijack",
    EARLY_AT,
    True,
    [
        {"stream": GW, "status": "time_suspect"},
        {"stream": GW, "status": "time_suspect"},
    ],
)


# Source signatures (SPEC §6.6, 18.155): the gateway's GW chain is source_signed, both links naming one envelope.
# GW reads verified only when that envelope is a DSSE envelope that verifies against the run's own trust root and its
# in-toto Statement names that link's own hash (one envelope signs the segment by naming every link's hash). A
# file that is no envelope, a key the run does not trust, a statement naming another hash, no trust root at all, and
# the engine's own development key (its seed is published, so it never counts, even when the run names it as its trust root) each read
# unsigned.
SIG_REF = "signatures/gw-segment.dsse.json"
SOURCE = source_signer()


def signed(name, body, status, trust=SOURCE, forge=False):
    """forge: after the source signs, the export host replaces the first link with a record of its own and re-points
    the second link's prev at it; the second link's hash leaves out data.integrity, so it does not change."""
    head, tail = gateway("source_signed")
    for link in (head, tail):
        link["data"]["integrity"]["sig_ref"] = SIG_REF
    files = {SIG_REF: body(tail["data"]["integrity"]["hash"], head["data"]["integrity"]["hash"])}
    if forge:
        forged = copy.deepcopy(head)
        del forged["data"]["integrity"]
        forged["data"]["tool"] = {"name": "forged-by-export-host"}  # a call the source never made
        head = chained(forged, "0" * 64, "source_signed")
        head["data"]["integrity"]["sig_ref"] = SIG_REF
        tail["data"]["integrity"]["prev"] = head["data"]["integrity"]["hash"]
    integrity_case(
        name,
        note(),
        [{"stream": GW, "status": status}],
        gw=(head, tail),
        files=files,
        trust=trust_root(trust) if trust is not None else None,
    )


def dev_release_signer():
    """The vendored development root's KMS release key (conformance/dev_trust.py: derived from a published seed)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "conformance"))
    import dev_trust

    from agentce import signing

    return signing.KmsSigner(dev_trust.kms_key())


def tampered(h, first):
    """An envelope the trusted key signed over another statement, its payload swapped for one naming `h`."""
    signed_other = json.loads(envelope([first], SOURCE))
    signed_other["payload"] = json.loads(envelope([first, h], SOURCE))["payload"]
    return json.dumps(signed_other, indent=2) + "\n"


def not_intoto(h, first):
    """A DSSE envelope the trusted key really signs, over a payload type other than an in-toto Statement."""
    from agentce import signing

    statement = json.loads(base64.b64decode(json.loads(envelope([first, h], SOURCE))["payload"]))
    payload = signing.canonicalize(statement)
    sig = SOURCE.sign(signing._pae("application/json", payload))
    body = {
        "payloadType": "application/json",
        "payload": base64.b64encode(payload).decode(),
        "signatures": [{"keyid": SOURCE.keyid, "sig": base64.b64encode(sig).decode()}],
    }
    return json.dumps(body, indent=2) + "\n"


signed("integrity-source-signed-trusted-key", lambda h, first: envelope([first, h], SOURCE), "verified")
signed(
    "integrity-source-signed-fake-sig",
    lambda h, first: "not a real DSSE envelope, no signature, no key\n",
    "unsigned",
)
signed(
    "integrity-source-signed-untrusted-key",
    lambda h, first: envelope([first, h], source_signer("another source")),
    "unsigned",
)
signed(
    "integrity-source-signed-hash-not-covered",
    lambda h, first: envelope([hashlib.sha256(b"a record this stream does not hold").hexdigest()], SOURCE),
    "unsigned",
)
# A statement naming only the chain's first record covers that record, not the later one: the stream is unsigned.
# A statement naming only the chain's head covers that record, not the earlier one: a record's hash leaves out its
# prev, so the head commits to nothing before it.
signed("integrity-source-signed-head-only", lambda h, first: envelope([h], SOURCE), "unsigned")
# The source signed the head alone; the export host swapped in its own first record and re-pointed the head's prev.
signed("integrity-source-signed-forged-prefix", lambda h, first: envelope([h], SOURCE), "unsigned", forge=True)
signed("integrity-source-signed-first-link-only", lambda h, first: envelope([first], SOURCE), "unsigned")
signed("integrity-source-signed-tampered-payload", tampered, "unsigned")
signed("integrity-source-signed-not-intoto", not_intoto, "unsigned")
signed("integrity-source-signed-no-trust-root", lambda h, first: envelope([first, h], SOURCE), "unsigned", None)
signed(
    "integrity-source-signed-dev-root-key",
    lambda h, first: envelope([first, h], dev_release_signer()),
    "unsigned",
    None,
)
# The same development key with the operator naming it as the run's trust root: its seed is published, so it still
# signs no source record.
signed(
    "integrity-source-signed-dev-root-named",
    lambda h, first: envelope([first, h], dev_release_signer()),
    "unsigned",
    dev_release_signer(),
)
write()
