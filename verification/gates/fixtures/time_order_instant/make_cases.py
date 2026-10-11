"""Write cases.json for VG-TIME-ORDER-INSTANT (18.145). Run from this folder: python make_cases.py.

Ingest quarantines a record time_order when its time precedes the previous record's in its stream. Times are compared
as instants (SPEC §6.7): an RFC 3339 time with a UTC offset is placed in UTC before it is compared, and a fraction of a
second counts. Each offset case reads exactly like its twin, where every time is written in UTC; a record truly
earlier than the one before it is still quarantined, however its time is written."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import NC, appr, case, dec, ev, idp, ran, write  # noqa: E402

APPROVE = "2026-01-10T01:00:00Z"


def at(e, time):
    e["time"] = time
    return e


def run_at(time, i="t0"):
    return at(ran(i=i), time)


def withdraw(reject_at, approve_at=APPROVE, action_at="2026-01-10T03:00:00Z"):
    """One reviewer's approve, then the same human's reject in the same stream, then the action: the reject withdraws
    the approve when it is at or after it, so CND-02 fails d1."""
    return [
        dec(),
        idp(),
        at(appr("approve"), approve_at),
        at(appr("reject", i="a2"), reject_at),
        run_at(action_at),
    ]


# The repro (critic p8): the reject at 00:30 two hours behind UTC is 02:30Z, after the approve at 01:00Z.
case("cnd02-no-offset-twin", "CND-02", withdraw("2026-01-10T02:30:00Z"), NC, ["d1"])
case(
    "cnd02-offset-instant-order",
    "CND-02",
    withdraw("2026-01-10T00:30:00-02:00"),
    NC,
    ["d1"],
)
# The same instant written two ways is not out of order: the reject equals the approve's time.
case(
    "cnd02-equal-instant-twin",
    "CND-02",
    withdraw(APPROVE),
    NC,
    ["d1"],
)
case(
    "cnd02-equal-offset-instant-order",
    "CND-02",
    withdraw(APPROVE, approve_at="2026-01-10T03:00:00+02:00"),
    NC,
    ["d1"],
)
# A fraction of a second counts: 01:00:00.500Z is after 01:00:00Z.
case("cnd02-fraction-twin", "CND-02", withdraw("2026-01-10T01:00:01Z"), NC, ["d1"])
case(
    "cnd02-fraction-instant-order",
    "CND-02",
    withdraw("2026-01-10T01:00:00.500Z"),
    NC,
    ["d1"],
)


# Acted-on type: the enforcement point's own stream. Its earlier record t9 at 03:00Z, then the action t0 at 02:00 two
# hours behind UTC (04:00Z): t0 is later and is never quarantined.
def toolcall(time):
    return [
        dec(),
        idp(),
        appr("approve"),
        at(appr("reject", i="a2"), "2026-01-10T02:30:00Z"),
        run_at("2026-01-10T03:00:00Z", i="t9"),
        run_at(time),
    ]


case(
    "cnd02-toolcall-no-offset-twin",
    "CND-02",
    toolcall("2026-01-10T04:00:00Z"),
    NC,
    ["d1"],
)
case(
    "cnd02-toolcall-offset-instant-order",
    "CND-02",
    toolcall("2026-01-10T02:00:00-02:00"),
    NC,
    ["d1"],
)
# No new lenience: a reject truly earlier than the approve (01:30 two hours ahead of UTC is 23:30Z the day before) is
# still quarantined time_order, as its UTC twin is, and they read alike.
case(
    "cnd02-earlier-utc-time-order",
    "CND-02",
    withdraw("2026-01-09T23:30:00Z"),
    "conformant",
    quarantined=1,
)
case(
    "cnd02-earlier-offset-time-order",
    "CND-02",
    withdraw("2026-01-10T01:30:00+02:00"),
    "conformant",
    quarantined=1,
)
# A time with no offset cannot be placed against one with an offset, so the pair keeps the byte order ingest has always
# used: the naive reject at 00:30 sorts before the approve and is quarantined, as before 18.145.
case(
    "cnd02-naive-time-order-fallback",
    "CND-02",
    withdraw("2026-01-10T00:30:00"),
    "insufficient_evidence",
    quarantined=1,
)
write()
