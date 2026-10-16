"""Write cases.json for VG-INTEGRITY-CAP (18.132). Run from this folder: python make_cases.py.

SPEC §6.6 and §8.1: an enforcement-point record counts at its class only on a verified stream. A control that reads
conformant only because of a record on a failed, gap or missing chain reads insufficient_evidence; one resting on an
enforcement-point record on an export_chained or unsigned stream reads partial; a timing control (INC) leaves the
records of a time_suspect stream out. A violation never changes: a source that claims the genuine stream's id lowers
that chain to verified_weak (18.151), and the violation still reads non-conformant.

Each case chains every source's records itself (so the gate's own chaining stays out of it) and pins every
{stream, status} integrity.jsonl holds. Hashes are SHA-256 of the record's RFC 8785 form without data.integrity; the
records are ASCII with no numbers, so sorted compact JSON is that form."""

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import C, NC, SUBJECT, appr, case, dec, ev, idp, ran, write  # noqa: E402

GENESIS = "0" * 64
#: After every record's time, so an anchored head covers them all.
ANCHOR_AT = "2026-01-10T02:00:00Z"
#: Before every record's time, so an anchored stream reads time_suspect.
EARLY_AT = "2026-01-09T23:00:00Z"
CLASSES = ("enforcement_point", "independent_system", "self_report")


def digest(e):
    return hashlib.sha256(json.dumps(e, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def stream_of(cls):
    return f"urn:stream:{cls}"


def chain(name, modes, claimer=None):
    """Chain C[name]'s records per source class. `modes` maps a class to how its stream is written (a class it does not
    name is anchored): anchored, early (anchored before its records: time_suspect), signed (source_signed with a
    sig_ref file), unsigned (source_signed, no sig_ref), chained (export_chained), gap (a link names a hash the stream
    does not hold), failed (a stored hash that is not its record's), missing (no integrity member), naive-anchor
    (anchored, the anchor's time with no UTC offset), early-chained (export_chained, anchored before its records). `claimer`, if given,
    is a record of another source that claims the enforcement point's stream id with a well-formed link of its own.
    Sets the case's events, the manifest's streams and files, and expected.integrity."""
    entry = C[name]
    events = copy.deepcopy(entry["events"])
    heads, streams, files, integrity = {}, [], {}, {}
    naive = "naive-unsigned" in modes.values()
    if naive:  # every record time without its UTC offset: none can be placed against an anchor (all time_suspect)
        for e in events:
            e["time"] = e["time"].rstrip("Z")
    for cls in CLASSES:
        mode = modes.get(cls, "anchored")
        records = [e for e in events if e["agentcesourceclass"] == cls and e["source"] == f"urn:src:{cls}"]
        if not records:
            continue
        if mode == "missing":
            integrity[f"urn:src:{cls}|{SUBJECT}"] = "failed"
            continue
        strength = {
            "signed": "source_signed", "unsigned": "source_signed", "early-unsigned": "source_signed",
            "naive-unsigned": "source_signed", "chained": "export_chained", "early-chained": "export_chained",
        }.get(
            mode, "export_anchored"
        )
        prev = GENESIS
        for index, e in enumerate(records):
            block = {"hash": digest(e), "prev": prev, "stream": stream_of(cls), "strength": strength}
            if mode == "gap" and index == len(records) - 1:
                block["prev"] = hashlib.sha256(b"a link the stream does not hold").hexdigest()
            if mode == "signed":
                block["sig_ref"] = f"signatures/{cls}-{e['id']}.dsse.json"
                files[block["sig_ref"]] = json.dumps({"payloadType": "application/vnd.in-toto+json"}) + "\n"
            e["data"]["integrity"] = block
            prev = block["hash"]
        if mode == "failed":  # the stored hash is not the record's: the record changed after it was hashed
            records[0]["data"]["integrity"]["hash"] = hashlib.sha256(b"the record before it changed").hexdigest()
        heads[cls] = prev
        if strength == "export_anchored" or mode in ("early-unsigned", "naive-unsigned", "early-chained"):
            at = {"early": EARLY_AT, "early-unsigned": EARLY_AT, "early-chained": EARLY_AT, "naive-anchor": ANCHOR_AT[:-1]}
            streams.append({"stream": stream_of(cls), "anchors": [
                {"kind": "rfc3161", "ref": f"anchors/{cls}-head.tsr", "at": at.get(mode, ANCHOR_AT)}
            ]})
        integrity[stream_of(cls)] = "time_suspect" if naive else {
            "anchored": "verified", "signed": "verified", "early": "time_suspect", "unsigned": "unsigned",
            "chained": "verified_weak", "gap": "gap", "failed": "failed", "early-unsigned": "time_suspect",
            "naive-unsigned": "time_suspect", "naive-anchor": "time_suspect", "early-chained": "time_suspect",
        }[mode]
    expected = []
    if claimer is not None:
        claimer["data"]["integrity"] = {
            "hash": digest(claimer), "prev": GENESIS, "stream": stream_of("enforcement_point"),
            "strength": "export_anchored",
        }
        events.append(claimer)
        # Two sources under one stream id: neither chain reads verified (18.151); the claimer's own chain is listed
        # after the genuine one (sorted by source).
        integrity[stream_of("enforcement_point")] = "verified_weak"
        expected.append({"stream": stream_of("enforcement_point"), "status": "verified_weak"})
    entry["events"] = events
    entry["expected"]["integrity"] = sorted(
        [{"stream": s, "status": st} for s, st in integrity.items()] + expected, key=lambda x: x["stream"]
    )
    if streams:
        entry["streams"] = streams
    if files:
        entry["files"] = files


def gated_pass():
    """CND-02 met: the enforcement point gates and runs d1's action; human u2 approves it through an identity-provider
    login before it ran."""
    return [dec(), ran(effect_class="irreversible"), idp(), appr("approve")]


def gated_reject():
    """CND-02 violated: the only approval on d1 is a reject, and the action ran."""
    return [dec(), ran(effect_class="irreversible"), idp(), appr("reject")]


def note():
    """The agent's own routine Decision (not consequential, so CND-02 does not judge it)."""
    return ev("x9", "Decision", decision_type="dom:Lookup", acted_for=dec()["data"]["acted_for"])


def incident():
    """An incident record from the incident register (independent_system) naming its accountable actor (INC-02)."""
    return ev("i1", "Incident", incident_id="i1", incident_class="fundamental_rights", detected_at="2026-01-10T00:00:00Z")


IE = "insufficient_evidence"

# Enforcement point: what the stream it rides reads decides what CND-02's pass is worth.
for name, mode, outcome in (
    ("signed-stream-enforcement-point", "signed", "conformant"),
    ("anchored-stream-enforcement-point", "anchored", "conformant"),
    ("unsigned-stream-claims-enforcement-point", "unsigned", "partial"),
    ("chained-stream-enforcement-point", "chained", "partial"),
    ("gap-stream-enforcement-point", "gap", IE),
    ("failed-stream-enforcement-point", "failed", IE),
    ("missing-integrity-enforcement-point", "missing", IE),
):
    case(name, "CND-02", gated_pass(), outcome)
    chain(name, {"enforcement_point": mode})

# A time_suspect status never hides a weak strength (critic r1 F1): a source_signed stream with no signatures, read
# against an anchor dated before its records or with record times that carry no UTC offset, still caps at partial.
for name, mode in (
    ("time-suspect-unsigned-stream-enforcement-point", "early-unsigned"),
    ("time-suspect-no-offset-unsigned-stream-enforcement-point", "naive-unsigned"),
):
    case(name, "CND-02", gated_pass(), "partial")
    chain(name, {"enforcement_point": mode})

# An anchor whose time has no UTC offset anchors nothing (critic r2 N1): the stream cannot be cross-checked, so it
# reads time_suspect, and an enforcement point on it caps at partial as an unanchored one would. An export_chained
# stream anchored before its records is time_suspect and still weak (critic r2 register).
for name, mode in (
    ("anchor-no-offset-enforcement-point", "naive-anchor"),
    ("time-suspect-chained-stream-enforcement-point", "early-chained"),
):
    case(name, "CND-02", gated_pass(), "partial")
    chain(name, {"enforcement_point": mode})

# Independent system: an export_chained stream keeps its class (SPEC §6.6 MAY); a failed one does not.
case("chained-stream-independent-system", "CND-02", gated_pass(), "conformant")
chain("chained-stream-independent-system", {"independent_system": "chained"})
case("failed-stream-independent-system", "CND-02", gated_pass(), IE)
chain("failed-stream-independent-system", {"independent_system": "failed"})
# Self report is never capped: the agent's own records on a failed stream leave the pass as it is.
case("failed-stream-self-report", "CND-02", gated_pass(), "conformant")
chain("failed-stream-self-report", {"self_report": "failed"})

# A violation is never changed by a cap: the gateway's failed stream leaves the reject a violation.
case("failed-stream-violation", "CND-02", gated_reject(), NC, ["d1"])
chain("failed-stream-violation", {"enforcement_point": "failed"})

# Timing controls (SPEC §6.6 Timestamp trust): INC-02's incident record on a time_suspect stream is left out.
case("time-suspect-stream-in-timing-control", "INC-02", [incident()], IE)
chain("time-suspect-stream-in-timing-control", {"independent_system": "early"})
case("time-verified-stream-in-timing-control", "INC-02", [incident()], "conformant", exit=1)
chain("time-verified-stream-in-timing-control", {"independent_system": "anchored"})
case("anchor-no-offset-in-timing-control", "INC-02", [incident()], IE)
chain("anchor-no-offset-in-timing-control", {"independent_system": "naive-anchor"})
# Outside a timing control a time_suspect stream does not cap: CND-02 still passes on an early-anchored gateway.
case("time-suspect-stream-outside-timing-control", "CND-02", gated_pass(), "conformant")
chain("time-suspect-stream-outside-timing-control", {"enforcement_point": "early"})

# A claimer on the genuine stream id (inbox row 263): the gateway's chain reads verified_weak (18.151), and a
# violation must still read non-conformant, exactly as its no-claimer twin does.
case("integrity-cap-claimer-genuine-violation", "CND-02", [*gated_reject(), note()], NC, ["d1"])
chain("integrity-cap-claimer-genuine-violation", {}, claimer=C["integrity-cap-claimer-genuine-violation"]["events"].pop())
case("integrity-cap-no-claimer-genuine-violation", "CND-02", [*gated_reject(), note()], NC, ["d1"])
chain("integrity-cap-no-claimer-genuine-violation", {})
write()
