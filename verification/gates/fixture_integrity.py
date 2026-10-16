"""Integrity chains for the records a gate writes into its fixture bundles (SPEC §6.6, §8.1; 18.132).

An enforcement-point or independent-system record counts at its class only on a verified stream: a record with no
integrity member reads as a failed stream, and the controls resting on it read insufficient_evidence. A gate whose
cases judge something other than integrity chains each source's records over the ones ingest accepts, under the stream
id ingest already gives a record that claims none, its source (so time order groups records exactly as before), and
anchors the stream's head after its last record, so it reads verified. A record ingest quarantines (a duplicate id, a
schema or time-order failure) stays as written, so it never breaks the chain. A stream any of whose records already
carries an integrity member is the case's own and is left as written. The gates' bundle writers import it; they run in
the Python engine's environment.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from agentce.bundle import load_bundle
from agentce.ingest import ingest
from agentce.integrity import GENESIS_PREV, recompute_hash

#: The anchor time of a stream none of whose records has a string time.
_FALLBACK_AT = "2026-01-01T00:00:00Z"


def _instant(value: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def _line(event: Any) -> str:
    return json.dumps(event, sort_keys=True)


def _accepted(events: list[Any], manifest: dict[str, Any]) -> Counter[str]:
    """The records ingest accepts from ``events`` under ``manifest``'s declared sources, as written."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp)
        (bundle / "events").mkdir()
        text = "".join((e if isinstance(e, str) else _line(e)) + "\n" for e in events)
        (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
        files = [{"path": "events/subject.jsonl", "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}]
        (bundle / "manifest.json").write_text(json.dumps({**manifest, "files": files}), encoding="utf-8")
        return Counter(_line(e) for e in ingest(load_bundle(bundle)).raw_accepted)


def chain_streams(events: list[Any], manifest: dict[str, Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    """``events`` with each source's stream chained over its accepted records (export_anchored), and the manifest's
    ``streams`` entries anchoring each chained stream's head."""
    accepted = _accepted(events, manifest)
    own = {
        str(e.get("source", ""))
        for e in events
        if isinstance(e, dict) and isinstance(e.get("data"), dict) and "integrity" in e["data"]
    }
    out: list[Any] = []
    heads: dict[str, str] = {}
    last: dict[str, tuple[datetime, str]] = {}
    for event in events:
        line = _line(event)
        stream = str(event.get("source", "")) if isinstance(event, dict) else ""
        if not isinstance(event, dict) or not accepted[line] or stream in own or not isinstance(event.get("data"), dict):
            out.append(event)
            continue
        accepted[line] -= 1
        event = copy.deepcopy(event)
        block = {"stream": stream, "strength": "export_anchored", "prev": heads.get(stream, GENESIS_PREV)}
        block["hash"] = recompute_hash(event)  # the hash leaves data.integrity out (SPEC §6.6)
        event["data"]["integrity"] = block
        heads[stream] = block["hash"]
        when = _instant(event["time"]) if isinstance(event.get("time"), str) else None
        if when is not None and (stream not in last or when > last[stream][0]):
            last[stream] = (when, event["time"])
        out.append(event)
    streams = [
        {"stream": s, "head": h, "anchors": [{"kind": "rfc3161", "at": last[s][1] if s in last else _FALLBACK_AT}]}
        for s, h in sorted(heads.items())
    ]
    return out, streams
