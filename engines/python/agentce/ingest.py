"""Ingest and validate an evidence bundle (SPEC §8.1 first module).

Parse every event in ``events/*.jsonl``, validate it against the JSON Schema generated from the
LinkML model, and quarantine anything the engine refuses with a stable reason (SPEC App. F): an
oversize line, malformed JSON or a schema violation (``schema_invalid``), an unrecognised event type
(``unknown_type``), an event from a source the bundle does not declare (``unknown_source``), an event
whose ``agentcesourceclass`` differs from the class the bundle declares for its source
(``class_mismatch``, SPEC §6.4), a repeated id (``duplicate_id``), or an out-of-order timestamp within
a stream (``time_order``). Quarantine is an output, never a silent drop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .bundle import Bundle
from .errors import InputError
from .quarantine import QuarantineReason, QuarantineRecord
from .schema import event_types, validate_event

#: Default maximum size of a single event line, in bytes (SPEC App. F ``oversize``).
DEFAULT_MAX_EVENT_BYTES = 1_048_576

_TYPE_RE = re.compile(r"^org\.agent-conformance\.evidence\.(?P<name>[A-Za-z0-9]+)\.v1$")


@dataclass
class IngestResult:
    """The outcome of ingest: the accepted events, in order, and the quarantine records."""

    accepted: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[QuarantineRecord] = field(default_factory=list)


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


def ingest(
    bundle: Bundle, *, max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES
) -> IngestResult:
    """Ingest ``bundle``'s events, returning the accepted events and the quarantine records."""
    result = IngestResult()
    valid_types = event_types()
    seen_ids: set[str] = set()
    last_time: dict[str, str] = {}

    for path in bundle.event_files:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                if len(line.encode("utf-8")) > max_event_bytes:
                    result.quarantined.append(
                        QuarantineRecord(
                            QuarantineReason.OVERSIZE,
                            detail=f"event exceeds {max_event_bytes} bytes",
                        )
                    )
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    result.quarantined.append(
                        QuarantineRecord(
                            QuarantineReason.SCHEMA_INVALID,
                            detail=f"invalid JSON: {exc.msg}",
                        )
                    )
                    continue
                except RecursionError as exc:
                    raise InputError(
                        "input.event_structure_too_deep",
                        "an evidence event line is nested too deeply to parse safely.",
                        "flatten the event's structure; reference deeply nested content by an "
                        "opaque locator instead (SPEC R12).",
                    ) from exc
                if not isinstance(event, dict):
                    result.quarantined.append(
                        QuarantineRecord(
                            QuarantineReason.SCHEMA_INVALID,
                            detail="event is not a JSON object",
                        )
                    )
                    continue

                errors = validate_event(event)
                if errors:
                    result.quarantined.append(
                        QuarantineRecord(
                            QuarantineReason.SCHEMA_INVALID,
                            event_id=_opt(event, "id"),
                            source=_opt(event, "source"),
                            type=_opt(event, "type"),
                            detail=errors[0],
                        )
                    )
                    continue

                type_value = str(event["type"])
                name = _event_type_name(type_value)
                if name is None or name not in valid_types:
                    result.quarantined.append(
                        QuarantineRecord(
                            QuarantineReason.UNKNOWN_TYPE,
                            event_id=str(event["id"]),
                            source=str(event["source"]),
                            type=type_value,
                            detail=f"unrecognised event type {type_value!r}",
                        )
                    )
                    continue

                source = str(event["source"])
                if bundle.sources is not None and source not in bundle.sources:
                    result.quarantined.append(
                        QuarantineRecord(
                            QuarantineReason.UNKNOWN_SOURCE,
                            event_id=str(event["id"]),
                            source=source,
                            type=type_value,
                            detail="source is not declared in the bundle manifest",
                        )
                    )
                    continue

                declared_class = (
                    bundle.source_classes.get(source)
                    if bundle.source_classes is not None
                    else None
                )
                if declared_class is not None:
                    event_class = str(event["agentcesourceclass"])
                    if event_class != declared_class:
                        result.quarantined.append(
                            QuarantineRecord(
                                QuarantineReason.CLASS_MISMATCH,
                                event_id=str(event["id"]),
                                source=source,
                                type=type_value,
                                detail=(
                                    f"event class {event_class!r} differs from the "
                                    f"declared class {declared_class!r} for this source"
                                ),
                            )
                        )
                        continue

                event_id = str(event["id"])
                if event_id in seen_ids:
                    result.quarantined.append(
                        QuarantineRecord(
                            QuarantineReason.DUPLICATE_ID,
                            event_id=event_id,
                            source=source,
                            type=type_value,
                            detail="event id already seen in this bundle",
                        )
                    )
                    continue

                stream = _stream_of(event)
                time_value = str(event["time"])
                previous = last_time.get(stream)
                if previous is not None and time_value < previous:
                    result.quarantined.append(
                        QuarantineRecord(
                            QuarantineReason.TIME_ORDER,
                            event_id=event_id,
                            source=source,
                            stream=stream,
                            type=type_value,
                            detail=f"time {time_value} precedes {previous} in the stream",
                        )
                    )
                    continue

                seen_ids.add(event_id)
                last_time[stream] = time_value
                result.accepted.append(event)

    return result


def _opt(event: dict[str, Any], key: str) -> str | None:
    value = event.get(key)
    return str(value) if isinstance(value, str) else None
