"""Write cases.json for VG-STREAM-SOURCE-BOUND (18.147). Run from this folder: python make_cases.py.

Ingest checks time order within a stream (SPEC §6.6: a stream is (source, subject)). The stream id a record claims in
data.integrity.stream names a stream of the record's own source only: a record never joins another source's ordering
group by naming that source's stream. Each hijack case reads exactly like its twin, where the same record claims no
stream, so the enforcement point's genuine ToolCall is never quarantined time_order and CND-02 still fails d1."""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import CHAIN, NC, appr, case, dec, ev, idp, ran, write  # noqa: E402

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
# records, the withheld ToolCall still fails d1 (quarantine never gives a better outcome, 18.119).
ep_late = claim(
    ev("t9", "ToolCall", at=LATER, tool={"name": "x"}), "urn:stream:gateway-1"
)
case(
    "cnd02-same-source-claimed-stream-time-order",
    "CND-02",
    [dec(), ep_late, claim(ran(), "urn:stream:gateway-1"), idp(), appr("reject")],
    NC,
    ["d1"],
    quarantined=1,
)
write()
