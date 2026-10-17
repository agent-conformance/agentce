"""Ingest: acceptance and every Phase-1 quarantine reason (SPEC §8.1, App. F)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentce.bundle import load_bundle
from agentce.ingest import ingest
from agentce.quarantine import QuarantineReason


def _ingest(make_bundle: Callable[..., Any], items: list[Any], **kwargs: Any) -> Any:
    return ingest(load_bundle(make_bundle(items, **kwargs)))


def test_accepts_valid_events(
    make_bundle: Callable[..., Any], example_events: list[dict[str, Any]]
) -> None:
    result = _ingest(make_bundle, example_events)
    assert len(result.accepted) == 2
    assert result.quarantined == []


def test_blank_lines_are_skipped(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    import json

    result = ingest(load_bundle(make_bundle(["", json.dumps(example_event), "   "])))
    assert len(result.accepted) == 1
    assert result.quarantined == []


def test_invalid_json(make_bundle: Callable[..., Any]) -> None:
    result = _ingest(make_bundle, ["{not json"])
    assert [q.reason for q in result.quarantined] == [QuarantineReason.SCHEMA_INVALID]


def test_schema_invalid_event(make_bundle: Callable[..., Any]) -> None:
    result = _ingest(make_bundle, [{"id": "x"}])
    assert result.accepted == []
    assert result.quarantined[0].reason == QuarantineReason.SCHEMA_INVALID


def test_unknown_type(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    bad = clone(example_event)
    bad["type"] = "org.agent-conformance.evidence.NotAType.v1"
    result = _ingest(make_bundle, [bad])
    assert result.quarantined[0].reason == QuarantineReason.UNKNOWN_TYPE


def test_duplicate_id(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    result = _ingest(make_bundle, [example_event, example_event])
    assert len(result.accepted) == 1
    assert result.quarantined[0].reason == QuarantineReason.DUPLICATE_ID


def test_a_lower_class_record_never_displaces_a_higher_class_record(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    # 18.143: the agent's own record reusing the enforcement point's id, before or after it, is the one quarantined.
    genuine = str(example_event["source"])
    shadow = clone(example_event)
    shadow["source"] = "urn:example:agent"
    shadow["agentcesourceclass"] = "self_report"
    classes = {genuine: "enforcement_point", "urn:example:agent": "self_report"}
    for items, detail in (
        (
            [shadow, example_event],
            "a record of a higher source class has this event id",
        ),
        ([example_event, shadow], "event id already seen in this bundle"),
    ):
        result = _ingest(make_bundle, items, source_classes=classes)
        assert result.accepted == [example_event]
        assert [(q.reason, q.source, q.detail) for q in result.quarantined] == [
            (QuarantineReason.DUPLICATE_ID, "urn:example:agent", detail)
        ]

    # The class ingest trusts ranks, not the record's own: an undeclared source asserting
    # independent_system counts as self_report and still loses the id.
    shadow["agentcesourceclass"] = "independent_system"
    result = _ingest(
        make_bundle,
        [shadow, example_event],
        sources=["urn:example:agent"],
        source_classes={genuine: "enforcement_point"},
    )
    assert result.accepted == [example_event]
    assert result.quarantined[0].source == "urn:example:agent"


def test_time_order(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    first = clone(example_event)
    earlier = clone(example_event)
    earlier["id"] = "b" * 64
    earlier["time"] = "2020-01-01T00:00:00.000Z"
    result = _ingest(make_bundle, [first, earlier])
    assert len(result.accepted) == 1
    assert result.quarantined[0].reason == QuarantineReason.TIME_ORDER
    assert result.quarantined[0].stream


def test_a_record_never_joins_another_sources_time_order_by_claiming_its_stream(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    # 18.147: the agent's own later record names the enforcement point's stream; time order is per source, so the
    # enforcement point's earlier record is not quarantined.
    genuine = str(example_event["source"])
    claimed = example_event["data"].get("integrity", {}).get("stream") or genuine
    hijack = clone(example_event)
    hijack["id"] = "b" * 64
    hijack["source"] = "urn:example:agent"
    hijack["agentcesourceclass"] = "self_report"
    hijack["time"] = "2099-01-01T00:00:00.000Z"
    hijack["data"]["integrity"] = {
        **hijack["data"].get("integrity", {}),
        "stream": claimed,
    }
    classes = {genuine: "enforcement_point", "urn:example:agent": "self_report"}
    result = _ingest(make_bundle, [hijack, example_event], source_classes=classes)
    assert [e["id"] for e in result.accepted] == [hijack["id"], example_event["id"]]
    assert result.quarantined == []


def test_time_order_compares_times_as_instants(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    # 18.145: 00:30 two hours behind UTC is 02:30Z, after 01:00Z, so it is not quarantined; 01:30 two hours ahead of
    # UTC is 23:30Z the day before, so it is.
    first, later, earlier = (clone(example_event) for _ in range(3))
    for event, ident, time in (
        (first, "a", "2026-01-10T01:00:00Z"),
        (later, "b", "2026-01-10T00:30:00-02:00"),
        (earlier, "c", "2026-01-10T01:30:00+02:00"),
    ):
        event["id"] = ident * 64
        event["time"] = time
    result = _ingest(make_bundle, [first, later, earlier])
    assert [e["id"] for e in result.accepted] == [first["id"], later["id"]]
    assert [(q.event_id, q.reason) for q in result.quarantined] == [
        (earlier["id"], QuarantineReason.TIME_ORDER)
    ]


def test_unknown_source(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    result = _ingest(make_bundle, [example_event], sources=["urn:agentce:source:other"])
    assert result.quarantined[0].reason == QuarantineReason.UNKNOWN_SOURCE


def test_oversize(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    result = ingest(load_bundle(make_bundle([example_event])), max_event_bytes=10)
    assert result.quarantined[0].reason == QuarantineReason.OVERSIZE
    assert result.accepted == []


def test_class_mismatch(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    # The event is enforcement_point; the manifest declares the source as self_report (SPEC §6.4).
    source = str(example_event["source"])
    result = _ingest(
        make_bundle, [example_event], source_classes={source: "self_report"}
    )
    assert result.accepted == []
    assert result.quarantined[0].reason == QuarantineReason.CLASS_MISMATCH
    assert result.quarantined[0].source == source


def test_class_match_is_accepted(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    source = str(example_event["source"])
    result = _ingest(
        make_bundle, [example_event], source_classes={source: "enforcement_point"}
    )
    assert len(result.accepted) == 1
    assert result.quarantined == []


def test_source_declared_without_a_class_defaults_to_self_report(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    # The source is declared (passes UNKNOWN_SOURCE) but the manifest names no class for it; the
    # event's own self-assertion (enforcement_point) is never trusted as-is (SPEC §6.4).
    source = str(example_event["source"])
    assert example_event["agentcesourceclass"] == "enforcement_point"
    result = _ingest(make_bundle, [example_event], sources=[source])
    assert len(result.accepted) == 1
    assert result.quarantined == []
    assert result.accepted[0]["agentcesourceclass"] == "self_report"


def test_bundle_with_no_declared_classes_at_all_defaults_to_self_report(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    # No manifest "sources" entries at all: every source is implicitly undeclared, so every event's
    # self-asserted class is corrected the same way as a source named without a class.
    result = _ingest(make_bundle, [example_event])
    assert len(result.accepted) == 1
    assert result.accepted[0]["agentcesourceclass"] == "self_report"


def test_undeclared_source_already_self_report_is_unchanged(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    event = clone(example_event)
    event["agentcesourceclass"] = "self_report"
    source = str(event["source"])
    result = _ingest(make_bundle, [event], sources=[source])
    assert len(result.accepted) == 1
    assert result.accepted[0]["agentcesourceclass"] == "self_report"
    # No correction was needed, so the accepted and raw copies are the identical object.
    assert result.accepted[0] is result.raw_accepted[0]


def test_class_match_is_accepted_keeps_raw_and_accepted_identical(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    source = str(example_event["source"])
    result = _ingest(
        make_bundle, [example_event], source_classes={source: "enforcement_point"}
    )
    assert len(result.accepted) == 1
    assert result.accepted[0] is result.raw_accepted[0]


def test_undeclared_source_correction_never_reaches_raw_accepted(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    """The correction that makes `accepted` honest must never change the bytes integrity
    verification hashes (SPEC §6.6 hashes the whole CloudEvent, `agentcesourceclass` included, as the
    source emitted it): `raw_accepted` stays byte-identical so a genuinely unmodified, undeclared-source
    event still verifies, while the corrected copy would wrongly look tampered."""
    from agentce.integrity import IntegrityStatus, recompute_hash, verify_bundle

    event = clone(example_event)
    integrity_block = event["data"]["integrity"]
    integrity_block["prev"] = "0" * 64
    integrity_block["strength"] = "export_chained"
    integrity_block.pop("sig_ref", None)
    integrity_block["hash"] = recompute_hash(event)
    source = str(event["source"])

    bundle = load_bundle(make_bundle([event], sources=[source]))
    result = ingest(bundle)
    assert result.accepted[0]["agentcesourceclass"] == "self_report"
    assert result.raw_accepted[0]["agentcesourceclass"] == "enforcement_point"
    assert result.raw_accepted[0] == event

    verified = verify_bundle(result.raw_accepted, bundle.manifest, bundle.root)
    assert [r.status for r in verified] == [IntegrityStatus.VERIFIED_WEAK.value]

    tampered_view = verify_bundle(result.accepted, bundle.manifest, bundle.root)
    assert [r.status for r in tampered_view] == [IntegrityStatus.FAILED.value]


def _ingest_bytes(make_bundle: Callable[..., Any], body: bytes) -> Any:
    """Ingest a bundle whose one events file holds exactly ``body`` (any bytes, not only UTF-8)."""
    import hashlib
    import json

    root = make_bundle([])
    events = root / "events" / "stream.jsonl"
    events.write_bytes(body)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = hashlib.sha256(body).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return ingest(load_bundle(root))


def test_lines_end_at_lf_crlf_or_a_lone_cr(
    make_bundle: Callable[..., Any], example_events: list[dict[str, Any]]
) -> None:
    """The stream-line rule all three engines share (18.65): a lone CR ends a line as LF and CRLF
    do, so two events joined by a CR are two events, not one invalid line."""
    import json

    first, second = (json.dumps(e).encode() for e in example_events)
    for sep in (b"\n", b"\r\n", b"\r"):
        result = _ingest_bytes(make_bundle, first + sep + second + sep)
        assert len(result.accepted) == 2, sep
        assert result.quarantined == [], sep


def test_a_line_that_is_not_utf8_is_quarantined_alone(
    make_bundle: Callable[..., Any], example_events: list[dict[str, Any]]
) -> None:
    """Each line decodes on its own: an invalid UTF-8 line is one schema_invalid record, and the
    file's other lines are still read (18.65; Python used to crash on the whole file)."""
    import json

    first, second = (json.dumps(e).encode() for e in example_events)
    result = _ingest_bytes(make_bundle, first + b"\n\xff\xfe{}\n" + second + b"\n")
    assert len(result.accepted) == 2
    assert [(q.reason, q.detail) for q in result.quarantined] == [
        (QuarantineReason.SCHEMA_INVALID, "invalid UTF-8")
    ]


def test_a_leading_byte_order_mark_is_not_json_whitespace(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    """Only JSON's own whitespace is trimmed, so a BOM-prefixed line is not JSON in any engine."""
    import json

    result = _ingest_bytes(
        make_bundle, b"\xef\xbb\xbf" + json.dumps(example_event).encode()
    )
    assert result.accepted == []
    assert [q.reason for q in result.quarantined] == [QuarantineReason.SCHEMA_INVALID]


def test_nesting_past_the_limit_refuses_and_brackets_in_strings_do_not_count(
    make_bundle: Callable[..., Any],
) -> None:
    import pytest

    from agentce.errors import AgentceError
    from agentce.signing import MAX_JSON_DEPTH

    with pytest.raises(AgentceError) as exc:
        _ingest_bytes(make_bundle, b"[" * (MAX_JSON_DEPTH + 1) + b"\n")
    assert exc.value.key == "input.event_structure_too_deep"
    # Brackets inside a JSON string are text: the line parses and is quarantined as an event that
    # is not an object, not refused for depth.
    deep_text = b'"' + b"[" * (MAX_JSON_DEPTH + 1) + b'"\n'
    result = _ingest_bytes(make_bundle, deep_text)
    assert [q.detail for q in result.quarantined] == ["event is not a JSON object"]


def test_manifest_class_empty_null_or_non_string_is_treated_as_undeclared(
    tmp_path: Any,
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    """A manifest entry naming a source is not the same as declaring its class (SPEC §6.4): an empty
    string, `null`, or a non-string JSON value must be treated exactly like no `class` key at all, not
    coerced or rejected -- a hostile or malformed manifest gets the same honest default as one that
    simply forgot the field."""
    import json

    from conftest import write_bundle

    sources = [
        f"urn:agentce:source:undeclared-{suffix}"
        for suffix in ("empty", "null", "number")
    ]
    events = []
    for index, source in enumerate(sources):
        event = clone(example_event)
        event["source"] = source
        event["id"] = "a" * 63 + str(index)
        events.append(event)

    root = write_bundle(
        tmp_path / "bundle",
        [json.dumps(e) for e in events],
        source_classes={sources[0]: "", sources[1]: None, sources[2]: 42},
    )

    result = ingest(load_bundle(root))
    assert len(result.accepted) == 3
    assert result.quarantined == []
    assert all(e["agentcesourceclass"] == "self_report" for e in result.accepted)


def test_withheld_lists_the_quarantined_records_that_may_hold_evidence(
    make_bundle: Callable[..., Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    example_event: dict[str, Any],
) -> None:
    # 18.119: a record refused for where it sits stays readable; one ingest cannot read has no event; an identical
    # redelivery and an unknown type hold nothing new.
    late = clone(example_event)
    late["id"] = "b" * 64
    late["time"] = "2020-01-01T00:00:00.000Z"
    other = clone(example_event)
    other["time"] = "2030-01-01T00:00:00.000Z"
    unknown = clone(example_event)
    unknown["id"] = "c" * 64
    unknown["type"] = "org.agent-conformance.evidence.Heartbeat.v1"
    items = [example_event, late, clone(example_event), other, unknown, "{"]
    result = _ingest(make_bundle, items)
    reasons = [w.record.reason for w in result.withheld]
    # File order, then each non-identical duplicate once every record is placed.
    assert reasons == [
        QuarantineReason.TIME_ORDER,
        QuarantineReason.SCHEMA_INVALID,
        QuarantineReason.DUPLICATE_ID,
    ]
    time_order, unreadable, duplicate = result.withheld
    assert unreadable.event is None and unreadable.subject is None
    # Each readable record is the event as readers see it: this undeclared source's class reads self_report.
    read_as = {"agentcesourceclass": "self_report"}
    assert time_order.event == {**late, **read_as}
    assert time_order.subject == late["subject"]
    assert duplicate.event == {**other, **read_as} and duplicate.digest is not None
    assert len(result.quarantined) == 5


def test_withheld_oversize_has_no_event(
    make_bundle: Callable[..., Any], example_event: dict[str, Any]
) -> None:
    result = ingest(load_bundle(make_bundle([example_event])), max_event_bytes=10)
    assert [(w.record.reason, w.event, w.subject) for w in result.withheld] == [
        (QuarantineReason.OVERSIZE, None, None)
    ]


def test_withheld_keeps_the_raw_facts_of_an_unreadable_write(
    make_bundle: Callable[..., Any],
) -> None:
    # 18.172: an unreadable record is a MemoryWrite unless it names another type the engine knows; its record_ref and
    # store are kept when each is a string.
    from agentce.graph import UnreadableWrite

    def write(event_id: str, type_: Any, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": event_id,
            "source": "urn:src:guard",
            "subject": "spiffe://corp/agents/other",
            "type": type_,
            "time": "2026-01-02T00:00:01Z",
            "specversion": "1.0",
            "data": {"store": "kb", "record_ref": "mem:r1", "extra": 1, **data},
        }

    evidence = "org.agent-conformance.evidence.{}.v1".format
    items = [
        write("w1", evidence("MemoryWrite"), {"@type": "MemoryWrite"}),
        write("w2", 5, {}),
        write("w3", evidence("MemoryWrit"), {"@type": "MemoryWrit"}),
        write("w4", evidence("MemoryRead"), {"@type": "MemoryRead"}),
        write("w5", 5, {"@type": "MemoryRead"}),
        write("w6", 5, {"record_ref": 7, "store": None}),
        write("w7", evidence("MemoryRead"), {"@type": "MemoryWrite"}),
    ]
    result = _ingest(make_bundle, items)
    assert [w.write for w in result.withheld] == [
        UnreadableWrite("mem:r1", "kb"),
        UnreadableWrite("mem:r1", "kb"),
        UnreadableWrite("mem:r1", "kb"),
        None,
        None,
        UnreadableWrite(None, None),
        UnreadableWrite("mem:r1", "kb"),
    ]
    assert {w.subject for w in result.withheld} == {"spiffe://corp/agents/other"}


def test_an_unknown_version_of_memory_write_is_withheld_as_unreadable(
    make_bundle: Callable[..., Any],
) -> None:
    # 18.202: an unknown_type record whose envelope type names MemoryWrite at any version or none, or whose data
    # @type is MemoryWrite, is withheld as an unreadable write; another unknown type is only quarantined. An empty
    # record_ref or store reads as absent.
    from agentce.graph import UnreadableWrite

    def write(event_id: str, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": event_id,
            "source": "urn:src:guard",
            "subject": "spiffe://corp/agents/other",
            "type": type_,
            "time": "2026-01-02T00:00:01Z",
            "specversion": "1.0",
            "datacontenttype": "application/ld+json",
            "agentcesourceclass": "enforcement_point",
            "data": data
            or {"@type": "MemoryWrite", "store": "kb", "record_ref": "mem:r1"},
        }

    read = {"@type": "MemoryRead", "store": "kb", "record_refs": ["mem:r1"]}

    v2 = "org.agent-conformance.evidence.{}.v2".format
    items = [
        write("w1", v2("MemoryWrite"), {}),
        write("w2", v2("MemoryWrite"), read),
        write("w3", "com.example.Thing.v1", {}),
        write("w4", v2("MemoryRead"), read),
        write(
            "w5",
            v2("MemoryWrite"),
            {"@type": "MemoryWrite", "record_ref": "", "store": ""},
        ),
        write("w6", "org.agent-conformance.evidence.MemoryWrite", read),
        write("w7", "org.agent-conformance.evidence.MemoryWriteV2", read),
        write("w8", "org.agent-conformance.evidence.MemoryRead", read),
    ]
    result = _ingest(make_bundle, items)
    assert [r.reason for r in result.quarantined] == [
        QuarantineReason.UNKNOWN_TYPE
    ] * len(items)
    assert [(w.record.event_id, w.write) for w in result.withheld] == [
        ("w1", UnreadableWrite("mem:r1", "kb")),
        ("w2", UnreadableWrite(None, "kb")),
        ("w3", UnreadableWrite("mem:r1", "kb")),
        ("w5", UnreadableWrite(None, None)),
        ("w6", UnreadableWrite(None, "kb")),
        ("w7", UnreadableWrite(None, "kb")),
    ]
