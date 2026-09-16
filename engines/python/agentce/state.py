"""The incremental-assessment state directory (SPEC §5.4 B7, §9.6, HR-10).

For incremental assessment over long windows, ``agentce`` maintains a local state directory: an index
of the bundle digests it has ingested and the identity of the last report it wrote. Re-running over the
same bundle digest is a no-op (SPEC §8.2 #8). When a new bundle differs — most importantly when
late-arriving evidence lands inside an already-assessed window — the engine re-assesses and the new
report lists the prior one in ``supersedes`` (HR-10); a window is never silently closed. The directory
carries a ``state_version``; an engine that meets an incompatible version refuses with exit code 3 and
names the migration command, never silently re-ingesting.

Late events (those whose source timestamp falls at or before the previously assessed window end) are
counted per integrity stream. The manifest schema (``spec/report/manifest.schema.json``) does not yet
carry a ``late_events`` field and forbids additional properties, so the count is surfaced on the assess
command's ``--json`` envelope and recorded in the state directory rather than written into the manifest;
the manifest carries the normative ``supersedes`` signal. This gap is logged for a schema RFC.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import InputError

STATE_VERSION = 1
STATE_FILE = "state.json"


def _stream(event: dict[str, Any]) -> str:
    data = event.get("data")
    if isinstance(data, dict):
        block = data.get("integrity")
        if isinstance(block, dict) and isinstance(block.get("stream"), str):
            return str(block["stream"])
    return f"{event.get('source', '')}|{event.get('subject', '')}"


def window_end(profile: Any, events: list[dict[str, Any]]) -> str:
    """The observation window's end: the declared end, else the latest event time, else the epoch."""
    window = getattr(profile, "observation_window", {}) or {}
    if isinstance(window, dict) and "end" in window:
        return str(window["end"])
    times = sorted(str(e.get("time", "")) for e in events if e.get("time"))
    return times[-1] if times else "1970-01-01T00:00:00Z"


@dataclass
class StateDir:
    """The on-disk state of an incremental assessment series."""

    path: Path
    version: int = STATE_VERSION
    bundle_digests: list[str] = field(default_factory=list)
    last_report_digest: str | None = None
    last_window_end: str | None = None

    @classmethod
    def load(cls, path: Path) -> StateDir:
        state_file = path / STATE_FILE
        if not state_file.is_file():
            return cls(path=path)
        data = json.loads(state_file.read_text(encoding="utf-8"))
        version = int(data.get("state_version", 0))
        if version != STATE_VERSION:
            raise InputError(
                "input.state_version_incompatible",
                (
                    f"the state directory at {path} is state_version {version}, "
                    f"but this engine writes state_version {STATE_VERSION}."
                ),
                f"run `agentce state migrate --state {path}` before re-assessing.",
            )
        return cls(
            path=path,
            version=version,
            bundle_digests=list(data.get("bundle_digests", [])),
            last_report_digest=data.get("last_report_digest"),
            last_window_end=data.get("last_window_end"),
        )

    def save(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        payload = {
            "state_version": STATE_VERSION,
            "bundle_digests": sorted(set(self.bundle_digests)),
            "last_report_digest": self.last_report_digest,
            "last_window_end": self.last_window_end,
        }
        (self.path / STATE_FILE).write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def plan(
        self, bundle_digest: str, accepted: list[dict[str, Any]], new_window_end: str
    ) -> tuple[list[str], dict[str, int]]:
        """Return (supersedes, late_events_by_stream) for a new assessment against this state.

        Supersession fires when a prior report exists and the bundle digest is new (HR-10: the inputs
        differ). Re-running the same bundle digest supersedes nothing (idempotent, SPEC §8.2 #8)."""
        if self.last_report_digest is None or bundle_digest in self.bundle_digests:
            return [], {}
        late: dict[str, int] = {}
        prior_end = self.last_window_end
        if prior_end is not None:
            for event in accepted:
                event_time = str(event.get("time", ""))
                if event_time and event_time <= prior_end:
                    stream = _stream(event)
                    late[stream] = late.get(stream, 0) + 1
        return [self.last_report_digest], dict(sorted(late.items()))

    def record(
        self, bundle_digest: str, manifest_path: Path, new_window_end: str
    ) -> str:
        """Record the assessment: index the bundle digest and remember the new report's manifest digest."""
        manifest_digest = (
            "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        )
        if bundle_digest not in self.bundle_digests:
            self.bundle_digests.append(bundle_digest)
        self.last_report_digest = manifest_digest
        self.last_window_end = new_window_end
        self.save()
        return manifest_digest
