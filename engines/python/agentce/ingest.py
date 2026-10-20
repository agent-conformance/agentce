"""Ingest and validate an evidence bundle (SPEC §8.1 first module).

Parse every event in ``events/*.jsonl``, validate it against the JSON Schema generated from the
LinkML model, and quarantine anything the engine refuses with a stable reason (SPEC App. F): an
oversize line, malformed JSON or a schema violation (``schema_invalid``), an unrecognised event type
(``unknown_type``), an event from a source the bundle does not declare (``unknown_source``), an event
whose ``agentcesourceclass`` differs from the class the bundle declares for its source
(``class_mismatch``, SPEC §6.4), a repeated id (``duplicate_id``), or an out-of-order timestamp within
a source's stream (``time_order``). Quarantine is an output, never a silent drop.

A source the manifest declares with no trust class (or a bundle with no manifest classes at all) is
"undeclared": its events keep flowing, but ``accepted`` never trusts the event's own self-asserted
``agentcesourceclass`` at face value -- it is corrected down to the weakest class, ``self_report``,
for every downstream reader (SPEC §6.4). That correction never reaches ``raw_accepted``, which stays
byte-identical to what the source emitted, because SPEC §6.6 defines each event's ``integrity.hash``
over the whole CloudEvent (``agentcesourceclass`` included) as received -- callers verifying integrity
must hash what was actually signed, not the engine's trust-corrected copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .bundle import Bundle
from .canonical import CanonicalizationError, sha256_hex
from .errors import InputError
from .graph import UnreadableWrite, _text
from .quarantine import QuarantineReason, QuarantineRecord
from .safe_json import JSONError, load_json
from .schema import event_types, validate_event
from .signing import MAX_JSON_DEPTH
from .source_class import SOURCE_CLASS_RANK
from .structural import compare_instants

#: Default maximum size of a single event line, in bytes (SPEC App. F ``oversize``).
DEFAULT_MAX_EVENT_BYTES = 1_048_576
#: The whitespace JSON itself allows around a value (RFC 8259 §2); nothing else is trimmed from a line.
_JSON_WHITESPACE = " \t\r\n"


_TYPE_RE = re.compile(r"^org\.agent-conformance\.evidence\.(?P<name>[A-Za-z0-9]+)\.v1$")
#: The envelope type prefix of a MemoryWrite at any version or none, known or not (SPEC §7.4, 18.202).
_MEMORY_WRITE_TYPE = "org.agent-conformance.evidence.MemoryWrite"


@dataclass(frozen=True)
class Withheld:
    """A quarantined record that may hold evidence a control needs (18.119): quarantine never gives a better
    outcome. ``event`` is the record as readers would see it (class corrected as for an accepted event) when
    ingest could read it but refused it for where it sits (``time_order``, ``duplicate_id`` that is not an
    identical redelivery, ``class_mismatch``, ``unknown_source``); it is ``None`` when ingest could not read
    it at all (``schema_invalid``, ``oversize``). ``subject`` is the record's own subject, ``None`` when
    unknown (then the record may belong to any subject). An ``unknown_type`` record is not withheld: the
    engine reads nothing from a type it does not model, unless its type names MemoryWrite, which is withheld as
    unreadable (SPEC §7.4, 18.202)."""

    record: QuarantineRecord
    event: dict[str, Any] | None
    subject: str | None
    digest: str | None
    #: Set on an unreadable record whose raw type names MemoryWrite, or whose type cannot be read at all (18.172).
    write: UnreadableWrite | None = None


@dataclass
class IngestResult:
    """The outcome of ingest: the accepted events, in order, and the quarantine records.

    ``accepted`` is what assessment, activity, and graph building read: an undeclared source's event
    carries ``agentcesourceclass: self_report`` here, never its own unproven self-assertion. ``raw_accepted``
    is the same events exactly as received -- pass this, never ``accepted``, to integrity verification
    (SPEC §6.6), which must hash what the source actually signed.
    """

    accepted: list[dict[str, Any]] = field(default_factory=list)
    raw_accepted: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[QuarantineRecord] = field(default_factory=list)
    withheld: list[Withheld] = field(default_factory=list)


def _event_type_name(type_value: str) -> str | None:
    match = _TYPE_RE.match(type_value)
    return match.group("name") if match else None


def _stream_of(event: dict[str, Any]) -> str:
    data = event.get("data")
    if isinstance(data, dict):
        integrity = data.get("integrity")
        if isinstance(integrity, dict):
            stream = integrity.get("stream")
            if isinstance(stream, str) and stream:
                return stream
    return str(event.get("source", ""))


def _precedes(time: str, previous: str) -> bool:
    """Whether ``time`` is before ``previous``, compared as instants (SPEC §6.7), so a UTC offset or a fraction of a
    second never puts a later record before an earlier one (18.145). Two times that are not comparable instants (not
    date-times, or one naive and one aware) keep the byte order ingest has always used."""
    order = compare_instants(time, previous)
    return time < previous if order is None else order < 0


def ingest(
    bundle: Bundle, *, max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES
) -> IngestResult:
    """Ingest ``bundle``'s events, returning the accepted events and the quarantine records."""
    result = IngestResult()
    valid_types = event_types()
    # Pass 1 checks each record on its own; `records` keeps, in file order, each quarantine record and each candidate
    # (the event as readers see it, as received, its source, type and class rank). `owner_rank` is the highest trusted
    # class among the candidates that carry each id: a record of a lower class never displaces it (18.143).
    records: list[
        QuarantineRecord
        | Withheld
        | tuple[dict[str, Any], dict[str, Any], str, str, int]
    ] = []
    owner_rank: dict[str, int] = {}

    for path in bundle.event_files:
        # One line rule the three engines share (18.65): lines end at \n, \r\n or a lone \r
        # (`bytes.splitlines`), each line is strict UTF-8 on its own, and only JSON's own whitespace
        # is trimmed. A line that is not UTF-8 is quarantined like a line that is not JSON, instead
        # of the whole file failing to decode. The bytes are the ones load_bundle checked and hashed (18.184f).
        for raw_line in bundle.contents[path].splitlines():
            try:
                raw = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                records.append(
                    _unreadable(
                        QuarantineRecord(
                            QuarantineReason.SCHEMA_INVALID, detail="invalid UTF-8"
                        )
                    )
                )
                continue
            line = raw.strip(_JSON_WHITESPACE)
            if not line:
                continue
            if len(line.encode("utf-8")) > max_event_bytes:
                records.append(
                    _unreadable(
                        QuarantineRecord(
                            QuarantineReason.OVERSIZE,
                            detail=f"event exceeds {max_event_bytes} bytes",
                        )
                    )
                )
                continue
            # The bracket count bounds the nesting, so most lines skip the character scan.
            if (
                line.count("[") + line.count("{") > MAX_JSON_DEPTH
                and _max_nesting(line) > MAX_JSON_DEPTH
            ):
                raise _too_deep()
            try:
                event = load_json(line)
            except JSONError as exc:
                records.append(
                    _unreadable(
                        QuarantineRecord(
                            QuarantineReason.SCHEMA_INVALID,
                            detail=f"invalid JSON: {exc.msg}",
                        )
                    )
                )
                continue
            except RecursionError as exc:
                raise _too_deep() from exc
            if not isinstance(event, dict):
                records.append(
                    _unreadable(
                        QuarantineRecord(
                            QuarantineReason.SCHEMA_INVALID,
                            detail="event is not a JSON object",
                        )
                    )
                )
                continue

            errors = validate_event(event)
            if errors:
                records.append(
                    _unreadable(
                        QuarantineRecord(
                            QuarantineReason.SCHEMA_INVALID,
                            event_id=_opt(event, "id"),
                            source=_opt(event, "source"),
                            type=_opt(event, "type"),
                            detail=errors[0],
                        ),
                        event,
                    )
                )
                continue

            type_value = str(event["type"])
            name = _event_type_name(type_value)
            if name is None or name not in valid_types:
                unknown = QuarantineRecord(
                    QuarantineReason.UNKNOWN_TYPE,
                    event_id=str(event["id"]),
                    source=str(event["source"]),
                    type=type_value,
                    detail=f"unrecognised event type {type_value!r}",
                )
                # A MemoryWrite at a version the engine does not know may still have changed a record (18.202).
                records.append(
                    _unreadable(unknown, event)
                    if _names_memory_write(event)
                    else unknown
                )
                continue

            source = str(event["source"])
            if bundle.sources is not None and source not in bundle.sources:
                records.append(
                    _readable(
                        QuarantineRecord(
                            QuarantineReason.UNKNOWN_SOURCE,
                            event_id=str(event["id"]),
                            source=source,
                            type=type_value,
                            detail="source is not declared in the bundle manifest",
                        ),
                        {**event, "agentcesourceclass": "self_report"},
                    )
                )
                continue

            raw_event = event
            declared_class = (
                bundle.source_classes.get(source)
                if bundle.source_classes is not None
                else None
            )
            if declared_class is not None:
                event_class = str(event["agentcesourceclass"])
                if event_class != declared_class:
                    records.append(
                        _readable(
                            QuarantineRecord(
                                QuarantineReason.CLASS_MISMATCH,
                                event_id=str(event["id"]),
                                source=source,
                                type=type_value,
                                detail=(
                                    f"event class {event_class!r} differs from the "
                                    f"declared class {declared_class!r} for this source"
                                ),
                            ),
                            {**event, "agentcesourceclass": declared_class},
                        )
                    )
                    continue
            elif str(event["agentcesourceclass"]) != "self_report":
                # SPEC §6.4: the manifest declares no trust class for this source, so the event's own
                # self-assertion is never trusted as-is. `raw_event` (appended below, unmodified) keeps
                # the original for integrity hashing (SPEC §6.6); this shallow copy is what every
                # assessment, activity, and graph reader sees.
                event = {**event, "agentcesourceclass": "self_report"}

            event_id = str(event["id"])
            # The evidence ladder (SPEC §6.4): of two records with one id, the lower class is quarantined.
            rank = SOURCE_CLASS_RANK[str(event["agentcesourceclass"])]
            owner_rank[event_id] = max(rank, owner_rank.get(event_id, rank))
            records.append((event, raw_event, source, type_value, rank))

    seen_ids: set[str] = set()
    # Time order is checked per (source, stream): the stream a record claims names a stream of its own source, so a
    # record never joins another source's ordering group by naming that source's stream (18.147).
    last_time: dict[tuple[str, str], str] = {}
    # Each duplicate_id record with the raw record it was read from: withheld unless it is an identical
    # redelivery of the record held under its id, which is known only once every record is placed.
    duplicates: list[tuple[Withheld, dict[str, Any]]] = []
    for record in records:
        if isinstance(record, QuarantineRecord):
            result.quarantined.append(record)
            continue
        if isinstance(record, Withheld):
            result.quarantined.append(record.record)
            result.withheld.append(record)
            continue
        event, raw_event, source, type_value, rank = record
        event_id = str(event["id"])
        if event_id in seen_ids or rank < owner_rank[event_id]:
            duplicate = _readable(
                QuarantineRecord(
                    QuarantineReason.DUPLICATE_ID,
                    event_id=event_id,
                    source=source,
                    type=type_value,
                    detail=(
                        "event id already seen in this bundle"
                        if event_id in seen_ids
                        else "a record of a higher source class has this event id"
                    ),
                ),
                event,
            )
            result.quarantined.append(duplicate.record)
            duplicates.append((duplicate, raw_event))
            continue

        stream = _stream_of(event)
        order_key = (source, stream)
        time_value = str(event["time"])
        previous = last_time.get(order_key)
        if previous is not None and _precedes(time_value, previous):
            late = _readable(
                QuarantineRecord(
                    QuarantineReason.TIME_ORDER,
                    event_id=event_id,
                    source=source,
                    stream=stream,
                    type=type_value,
                    detail=f"time {time_value} precedes {previous} in the stream",
                ),
                event,
            )
            result.quarantined.append(late.record)
            result.withheld.append(late)
            continue

        seen_ids.add(event_id)
        last_time[order_key] = time_value
        result.accepted.append(event)
        result.raw_accepted.append(raw_event)

    # Only ids that were delivered twice are compared, so only their held records are hashed.
    ids = {str(raw_event["id"]) for _, raw_event in duplicates}
    held = {
        str(e["id"]): record_digest(e)
        for e in result.raw_accepted
        if str(e["id"]) in ids
    }
    result.withheld.extend(
        duplicate
        for duplicate, raw_event in duplicates
        if (digest := record_digest(raw_event)) is None
        or held.get(str(raw_event["id"])) != digest
    )
    return result


def record_digest(event: dict[str, Any]) -> str | None:
    """The record's canonical digest, ``None`` when it has no canonical form (a schema-invalid record may hold a
    number the canonical form refuses, or one too long to print)."""
    try:
        return sha256_hex(event)
    except (CanonicalizationError, ValueError):
        return None


def _readable(record: QuarantineRecord, event: dict[str, Any]) -> Withheld:
    return Withheld(record, event, _opt(event, "subject"), record_digest(event))


def _unreadable(
    record: QuarantineRecord, event: dict[str, Any] | None = None
) -> Withheld:
    return Withheld(
        record,
        None,
        _opt(event, "subject") if event is not None else None,
        record_digest(event) if event is not None else None,
        _unreadable_write(event) if event is not None else None,
    )


def _names_memory_write(event: dict[str, Any]) -> bool:
    """Whether ``event``'s envelope type names MemoryWrite at any version or none, or its ``data.@type`` does
    (MemoryWrite, MemoryWrite.v2, MemoryWriteV2)."""
    type_value = event.get("type")
    data = event.get("data")
    return (
        isinstance(type_value, str) and type_value.startswith(_MEMORY_WRITE_TYPE)
    ) or (
        isinstance(data, dict)
        and isinstance(data.get("@type"), str)
        and data["@type"].startswith("MemoryWrite")
    )


def _unreadable_write(event: dict[str, Any]) -> UnreadableWrite | None:
    """The raw MemoryWrite facts of an unreadable ``event``: a MemoryWrite unless its envelope or data names another
    known type, so a record whose type cannot be read, or names a type the engine does not know, counts as one (fail
    closed, 18.172). An empty record_ref or store is one that cannot be read (18.202)."""
    type_value = event.get("type")
    name = _event_type_name(type_value) if isinstance(type_value, str) else None
    data = event.get("data")
    data = data if isinstance(data, dict) else {}
    declared = data.get("@type")
    if not _names_memory_write(event) and (
        name in event_types() or declared in event_types()
    ):
        return None
    return UnreadableWrite(_text(data.get("record_ref")), _text(data.get("store")))


def _too_deep() -> InputError:
    return InputError(
        "input.event_structure_too_deep",
        "an evidence event line is nested too deeply to parse safely.",
        "flatten the event's structure; reference deeply nested content by an "
        "opaque locator instead (SPEC R12).",
    )


def _max_nesting(line: str) -> int:
    """The deepest `[`/`{` nesting in `line`, counted lexically (brackets inside strings ignored)
    before any parse, so the limit is the same number in all three engines whatever their parser's
    own recursion limit, and an unclosed line counts too (18.65 round 3)."""
    depth = deepest = 0
    in_string = escaped = False
    for ch in line:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif ch in "]}":
            depth -= 1
    return deepest


def _opt(event: dict[str, Any], key: str) -> str | None:
    value = event.get(key)
    return str(value) if isinstance(value, str) else None
