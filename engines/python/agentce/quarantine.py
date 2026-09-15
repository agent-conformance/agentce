"""Quarantine records: an ingested item the engine refused, with a stable reason (SPEC App. F).

Quarantine is an output, never a silent drop (SPEC §5, module contract §8.1). Each record is one line
of ``quarantine.jsonl`` and validates against ``spec/report/quarantine.schema.json``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class QuarantineReason(str, Enum):
    """The stable quarantine reason codes (SPEC Appendix F / quarantine.schema.json)."""

    SCHEMA_INVALID = "schema_invalid"
    DUPLICATE_ID = "duplicate_id"
    TIME_ORDER = "time_order"
    UNKNOWN_TYPE = "unknown_type"
    UNKNOWN_SOURCE = "unknown_source"
    CLASS_MISMATCH = "class_mismatch"
    OVERSIZE = "oversize"
    CONTEXT_MISMATCH = "context_mismatch"


@dataclass(frozen=True)
class QuarantineRecord:
    """One quarantined item. ``reason`` is the stable part; ``detail`` is human-readable."""

    reason: QuarantineReason
    event_id: str | None = None
    source: str | None = None
    stream: str | None = None
    type: str | None = None
    detail: str | None = None

    def to_json(self) -> dict[str, str]:
        record: dict[str, str] = {"reason": self.reason.value}
        for field in ("event_id", "source", "stream", "type", "detail"):
            value = getattr(self, field)
            if value is not None:
                record[field] = value
        return record


def counts_by_reason(records: Iterable[QuarantineRecord]) -> dict[str, int]:
    """Return a deterministic ``{reason: count}`` map (sorted by reason)."""
    counts: dict[str, int] = {}
    for record in records:
        counts[record.reason.value] = counts.get(record.reason.value, 0) + 1
    return {reason: counts[reason] for reason in sorted(counts)}


def write_quarantine(records: Iterable[QuarantineRecord], path: Path) -> int:
    """Write ``records`` to ``path`` as one JSON object per line (input order). Return the count."""
    written = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(record.to_json(), sort_keys=True, separators=(",", ":"))
            )
            handle.write("\n")
            written += 1
    return written
