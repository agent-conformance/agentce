"""Adapter conformance probe: run one adapter's fixtures inside that adapter's environment.

Invoked by ``conformance.py`` as ``uv run --project <adapter> python _probe.py <adapter>`` so that the
adapter's ``agentce_adapters`` package and the engine (``agentce``) are both importable. For every
fixture it checks two things and prints one JSON line:

* **byte-identity** -- the adapter's events equal the fixture's expected events under the shared
  canonical form (SPEC 6.7): ``identical`` of ``total`` events match;
* **round-trip** -- those events, written to a bundle and read back through the engine's ingest, are
  accepted unchanged (SPEC 11.5): no event is lost or quarantined and each re-canonicalises identically.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from agentce.bundle import load_bundle
from agentce.canonical import canonical_string
from agentce.ingest import ingest
from agentce_adapters.fixtures import discover_fixtures


def _round_trip(events: list[dict[str, Any]]) -> bool:
    """Write ``events`` to a bundle, ingest it, and confirm they survive unchanged."""
    if not events:
        return True
    source_classes = {str(e["source"]): str(e["agentcesourceclass"]) for e in events}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "events").mkdir()
        stream = root / "events" / "stream.jsonl"
        stream.write_text(
            "".join(canonical_string(event) + "\n" for event in events),
            encoding="utf-8",
        )
        digest = hashlib.sha256(stream.read_bytes()).hexdigest()
        manifest = {
            "agentce_bundle_version": 1,
            "sources": [
                {"id": source, "class": cls}
                for source, cls in sorted(source_classes.items())
            ],
            "files": [{"path": "events/stream.jsonl", "sha256": digest}],
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        result = ingest(load_bundle(root))
    if len(result.accepted) != len(events):
        return False
    return [canonical_string(a) for a in result.accepted] == [
        canonical_string(e) for e in events
    ]


def main() -> int:
    adapter_dir = Path(sys.argv[1])
    total = 0
    identical = 0
    round_trip = True
    conventions: set[str] = set()
    by_type: dict[str, int] = {}
    name: str | None = None

    for fixture in discover_fixtures(adapter_dir / "fixtures"):
        result = fixture.run()
        name = result.report.adapter
        conventions.update(result.report.conventions)
        produced = [canonical_string(event) for event in result.events]
        expected = [canonical_string(event) for event in fixture.expected]
        total += len(produced)
        if len(produced) == len(expected):
            identical += sum(
                1 for a, b in zip(produced, expected, strict=True) if a == b
            )
        round_trip = round_trip and _round_trip(result.events)
        for event in result.events:
            event_type = str(event["data"]["@type"])
            by_type[event_type] = by_type.get(event_type, 0) + 1

    print(
        json.dumps(
            {
                "adapter": name or adapter_dir.name,
                "conventions": sorted(conventions),
                "total": total,
                "identical": identical,
                "round_trip": round_trip,
                "events_by_type": dict(sorted(by_type.items())),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
