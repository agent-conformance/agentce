"""The incremental-assessment state directory (SPEC §5.4 B7, §9.6, HR-10).

For incremental assessment over long windows, ``agentce`` maintains a local state directory: an index
of the bundle digests it has ingested and the identity of the last report it wrote. Re-running over the
same bundle digest is a no-op (SPEC §8.2 #8). When a new bundle differs — most importantly when
late-arriving evidence lands inside an already-assessed window — the engine re-assesses and the new
report lists the prior one in ``supersedes`` (HR-10); a window is never silently closed. The directory
carries a ``state_version``; an engine that meets an incompatible version refuses with exit code 3 and
names the real recovery path (there is no migration command: start a fresh state directory), never
silently re-ingesting.

Late events (those whose source timestamp falls at or before the previously assessed window end) are
counted per integrity stream. The manifest schema (``spec/report/manifest.schema.json``) does not yet
carry a ``late_events`` field and forbids additional properties, so the count is surfaced on the assess
command's ``--json`` envelope and recorded in the state directory rather than written into the manifest;
the manifest carries the normative ``supersedes`` signal. This gap is logged for a schema RFC.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import signing
from .assertions import Assertion
from .errors import InputError
from .outputs import NOT_A_FILE, UNREACHABLE, through_missing_folder, write_file

STATE_VERSION = 1
STATE_FILE = "state.json"
_FIX = "choose a writable --state directory."


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


def _check_state_file(state_file: Path) -> None:
    """Refuse a ``state.json`` that is not a regular file, cannot be read and written, or cannot be
    reached, under ``input.state_dir_unwritable``; absent or a dangling link is a fresh series."""
    shown = repr(str(state_file))
    try:
        info = os.stat(state_file)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise InputError(
            "input.state_dir_unwritable", f"{shown} {UNREACHABLE}.", _FIX
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise InputError("input.state_dir_unwritable", f"{shown} {NOT_A_FILE}.", _FIX)
    if not os.access(state_file, os.R_OK | os.W_OK):
        raise InputError(
            "input.state_dir_unwritable", f"{shown}: {os.strerror(errno.EACCES)}", _FIX
        )


@dataclass
class StateDir:
    """The on-disk state of an incremental assessment series."""

    path: Path
    version: int = STATE_VERSION
    bundle_digests: list[str] = field(default_factory=list)
    last_report_digest: str | None = None
    last_window_end: str | None = None
    last_outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> StateDir:
        state_file = path / STATE_FILE
        # unreadable, never read as a fresh series (18.169)
        try:
            has_state = signing.is_regular_file(state_file)
        except PermissionError as exc:
            raise InputError(
                "input.state_dir_unwritable",
                exc.strerror or str(exc),
                _FIX,
            ) from exc
        if not has_state:
            return cls(path=path)
        try:
            text = state_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise InputError(
                "input.state_dir_unwritable", exc.strerror or str(exc), _FIX
            ) from exc
        data = json.loads(text)
        version = int(data.get("state_version", 0))
        if version != STATE_VERSION:
            raise InputError(
                "input.state_version_incompatible",
                (
                    f"the state directory at {path} is state_version {version}, "
                    f"but this engine writes state_version {STATE_VERSION}."
                ),
                (
                    "there is no migration command: move or delete the state directory and re-run "
                    "with --state pointing at a fresh, empty directory (this discards the prior "
                    "bundle/outcome history recorded there, so late-arriving evidence and drift are "
                    "tracked only from that point forward)."
                ),
            )
        # `last_outcomes` is a purely additive field (introduced after `state_version` 1 was fixed):
        # a state directory written before it, or with no prior tracked pair, has no such key, and
        # `{}` here means exactly that -- "no prior outcome recorded" -- never a version mismatch.
        raw_outcomes = data.get("last_outcomes", {})
        return cls(
            path=path,
            version=version,
            bundle_digests=list(data.get("bundle_digests", [])),
            last_report_digest=data.get("last_report_digest"),
            last_window_end=data.get("last_window_end"),
            last_outcomes=dict(raw_outcomes) if isinstance(raw_outcomes, dict) else {},
        )

    def save(self) -> None:
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            payload = {
                "state_version": STATE_VERSION,
                "bundle_digests": sorted(set(self.bundle_digests)),
                "last_report_digest": self.last_report_digest,
                "last_window_end": self.last_window_end,
                "last_outcomes": dict(sorted(self.last_outcomes.items())),
            }
            write_file(
                self.path / STATE_FILE,
                json.dumps(payload, sort_keys=True, indent=2) + "\n",
            )
        except OSError as exc:
            raise InputError(
                "input.state_dir_unwritable",
                exc.strerror or str(exc),
                _FIX,
            ) from exc

    @staticmethod
    def ensure_writable(path: Path) -> None:
        """Prove ``path`` is writable before any ``--out`` write begins (a refused run must leave
        nothing behind), by actually creating and removing a probe file rather than only `mkdir`ing:
        a directory that exists but lost its write bit after creation would pass `mkdir(exist_ok=True)`
        and only fail on a real write. ``state.json`` itself must then be absent (a dangling link
        counts: the write replaces it) or a regular file that can be read and written; it is never
        opened here, so a FIFO is refused rather than waited on (18.184d)."""
        probe = path / f".agentce-write-test-{os.getpid()}"
        if through_missing_folder(path):
            raise InputError(
                "input.state_dir_unwritable",
                f"{str(path)!r}: {os.strerror(errno.ENOENT)}",
                _FIX,
            )
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            raise InputError(
                "input.state_dir_unwritable",
                exc.strerror or str(exc),
                _FIX,
            ) from exc
        _check_state_file(path / STATE_FILE)

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

    def drift(self, assertions: list[Assertion]) -> list[dict[str, Any]]:
        """Diff this run's outcomes against the last one recorded for each ``(subject, control)``
        pair (SPEC.md:249 DC-9/HR-10, SPEC.md:166 Art. 72); update ``last_outcomes`` in place for
        :meth:`record`/:meth:`save` to persist, and return the drift entries, sorted by
        ``(subject, control)`` for determinism.

        A pair with no prior recorded outcome (a brand-new state directory, a pre-16.2 one, or a pair
        never seen before) reports no drift for that pair -- there is nothing yet to compare against;
        it is only added to ``last_outcomes`` so the *next* run can compare. Re-running the identical
        bundle digest re-evaluates to the identical outcomes, so it also reports no drift, without any
        special-cased no-op check."""
        entries: list[dict[str, Any]] = []
        updated = dict(self.last_outcomes)
        for assertion in sorted(assertions, key=lambda a: (a.subject, a.control)):
            key = f"{assertion.subject}|{assertion.control}"
            prior = self.last_outcomes.get(key)
            prior_outcome = prior.get("outcome") if isinstance(prior, dict) else None
            if prior_outcome is not None and prior_outcome != assertion.outcome:
                entries.append(
                    {
                        "subject": assertion.subject,
                        "control": assertion.control,
                        "previous_outcome": prior_outcome,
                        "outcome": assertion.outcome,
                        "evidence": sorted({e.ref for e in assertion.evidence}),
                    }
                )
            updated[key] = {"outcome": assertion.outcome}
        self.last_outcomes = updated
        return entries

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
