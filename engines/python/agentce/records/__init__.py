"""Records folders: ``agentce assess <folder>`` reads the records it is given (SPEC §12, §13.4 AX-1).

A newcomer's first assessment starts from a folder of trace exports, not from a hand-built evidence
bundle and a hand-written profile. :func:`scan` reads every OpenTelemetry GenAI or OpenInference trace
export under a folder through the vendored ``otel-genai`` adapter (:mod:`agentce.records.otel_genai`, held
byte-identical to ``adapters/otel-genai`` by a test), and derives from what it found the two inputs the
pipeline needs: an evidence bundle and a default applicability profile that names the baseline lens. Nothing is written until :meth:`ScannedRecords.write`, so a run that is refused
later (a bad flag, an unresolvable catalog) leaves nothing behind.

Every file that is not recognised is listed with its reason and never dropped silently; a symlink that
leaves the folder is never followed. The generated bundle and profile carry no timestamp, host or folder
path, so the same records give byte-identical canonical results wherever the folder lives.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .. import bundle, bundled
from ..activity import summarize_activity
from ..canonical import canonical_string
from ..errors import InputError
from ..profile import Profile
from . import otel_genai

#: File suffixes read as trace exports: one OTLP/JSON document per ``.json`` file, one per line in
#: ``.jsonl`` / ``.ndjson`` (the shape an OpenTelemetry Collector file exporter writes).
RECORD_SUFFIXES = (".json", ".jsonl", ".ndjson")
ADAPTER = "otel-genai"
SOURCE_CLASS = "self_report"
BUNDLE_DIR = "records-bundle"
DERIVED_PROFILE_FILE = "applicability.yaml"

#: Recorded in the manifest and the claim of every records-folder run (SPEC §8.7): traces carry no decision
#: records, so an empty population says the records show none, not that the control does not apply.
RECORDS_LIMITATION = (
    "assessed from trace records only: traces carry no decision records and no declaration says which "
    "tool calls are consequential, so a control the records give no population for is reported as "
    "insufficient evidence rather than not applicable."
)

_CLASS_JUSTIFICATION = (
    "read from the agent's own trace export; the agent could have written anything in it, so it is "
    "recorded as self-reported."
)


@dataclass
class ScannedRecords:
    """What a records folder held: the adapted events and the summary of how they were found."""

    events: list[dict[str, Any]]
    subject: str
    summary: dict[str, Any]
    _sources: dict[str, str] = field(default_factory=dict)
    #: The bytes of each source's event stream, keyed by source URI, built (and size-checked) by scan.
    _streams: dict[str, bytes] = field(default_factory=dict)

    @property
    def profile(self) -> dict[str, Any]:
        """The default applicability profile (SPEC §6.5): the subject, the observation window read
        from the events, the baseline lens, one self-reported evidence source per source URI, and
        the tools and models this scan actually saw -- declared up front so a fresh, unedited first
        run shows nothing as undeclared (18.4); anything new a later run sees is the useful signal."""
        times = [str(e["time"]) for e in self.events]
        # `summarize_activity` is the one place that extracts tool/model names from events
        # (agentce.activity); reuse it rather than re-deriving the same names here.
        seen = summarize_activity(self.events, Profile())
        return {
            "profile_version": 1,
            "observation_window": {"start": min(times), "end": max(times)},
            "catalogs": [bundled.DEFAULT_LENS],
            "subjects": [
                {
                    "id": self.subject,
                    "name": "Agent records",
                    "role": "deployer",
                    "evidence_sources": [
                        {
                            "adapter": ADAPTER,
                            "source": source,
                            "class": self._sources[source],
                            "class_justification": _CLASS_JUSTIFICATION,
                        }
                        for source in sorted(self._sources)
                    ],
                    "declared_tools": [t["name"] for t in seen["tools"]],
                    "declared_models": [m["name"] for m in seen["models"]],
                }
            ],
        }

    def write(self, out_dir: Path, *, write_profile: bool) -> Path:
        """Write the evidence bundle under ``out_dir`` (replacing a previous run's) and, when
        ``write_profile``, the default profile beside it; return the bundle directory. An adopter's own
        ``--profile`` is never written over."""
        root = out_dir / BUNDLE_DIR
        shutil.rmtree(root, ignore_errors=True)
        (root / "events").mkdir(parents=True)
        files: list[dict[str, str]] = []
        for source in sorted(self._streams):
            stem = re.sub(r"[^A-Za-z0-9]+", "-", source).strip("-")[:60]
            name = (
                f"events/{stem}-{hashlib.sha256(source.encode()).hexdigest()[:8]}.jsonl"
            )
            data = self._streams[source]
            (root / name).write_bytes(data)
            files.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {
            "agentce_bundle_version": 1,
            "files": files,
            "sources": [
                {"id": source, "class": self._sources[source]}
                for source in sorted(self._sources)
            ],
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        if write_profile:
            (out_dir / DERIVED_PROFILE_FILE).write_text(
                "# The default profile `agentce assess <folder>` derived from the records it found.\n"
                "# Edit it (declare your agents, oversight and catalogs) and pass it back with --profile.\n"
                + yaml.safe_dump(self.profile, sort_keys=False),
                encoding="utf-8",
            )
        return root


NO_GENAI_SPANS = "spans found, but none is a GenAI operation the adapter maps"


def _adapt(payload: bytes, subject: str) -> otel_genai.AdaptResult:
    """Adapt one OTLP/JSON document; raise ``ValueError`` when it is not a trace export."""
    try:
        result = otel_genai.adapt(payload, subject=subject, source_class=SOURCE_CLASS)
    except otel_genai.AdapterError as exc:
        raise ValueError(str(exc)) from exc
    except RecursionError as exc:
        raise ValueError("nested too deeply to parse safely") from exc
    if not result.events:
        raise ValueError(
            "no spans: not an OpenTelemetry or OpenInference trace export"
            if result.report.spans_seen == 0
            else NO_GENAI_SPANS
        )
    return result


#: File suffixes that are archives or compressed exports: named as not read, never opened.
COMPRESSED_SUFFIXES = frozenset({".gz", ".tgz", ".zip", ".zst", ".bz2", ".xz"})


def _same(a: Path, b: Path) -> bool:
    """True when ``a`` and ``b`` are the same folder on disk, however each is spelled (case, Unicode
    form, symlinks); a path that does not exist is the same as nothing."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _within(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` or lies under it, compared by identity on disk, not by spelling."""
    return any(_same(p, root) for p in (path, *path.parents))


def _candidates(
    folder: Path, skip: list[Path], unread: list[dict[str, str]]
) -> list[tuple[str, Path | None]]:
    """The record files under ``folder`` in a fixed order (hidden files and directories and the ``skip``
    trees are not walked); ``None`` marks a symlink that leaves the folder. Compressed files and symlinked
    directories, which are never read, are named in ``unread`` with why."""
    found: list[tuple[str, Path | None]] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        here = Path(dirpath)
        kept = []
        for d in sorted(dirnames):
            if d.startswith(".") or any(_same(here / d, tree) for tree in skip):
                continue
            if (here / d).is_symlink():
                unread.append(
                    {
                        "path": (here / d).relative_to(folder).as_posix(),
                        "reason": "a symlinked folder is not followed",
                    }
                )
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = here / name
            if name.startswith("."):
                continue
            if path.suffix.lower() in COMPRESSED_SUFFIXES:
                unread.append(
                    {
                        "path": path.relative_to(folder).as_posix(),
                        "reason": "compressed files are not read; decompress them first",
                    }
                )
                continue
            if path.suffix.lower() not in RECORD_SUFFIXES:
                continue
            rel = path.relative_to(folder).as_posix()
            if not bundle.safe_is_file(path):
                continue
            found.append((rel, bundle.confine_to_root(folder, rel)))
    return found


#: How many lines of a JSON Lines file that were not trace exports are named in the summary.
MAX_LISTED_LINES = 20


@dataclass
class _FileRead:
    """One record file: the documents that adapted, and the lines (a ``.json`` file is line 1) that did
    not, with why."""

    results: list[otel_genai.AdaptResult] = field(default_factory=list)
    bad_lines: list[tuple[int, str]] = field(default_factory=list)


def _read(path: Path, subject: str) -> _FileRead | str:
    """Read one record file; return why it cannot be read at all instead when it cannot."""
    try:
        size = path.stat().st_size
        if size > bundle.DEFAULT_MAX_MANIFEST_FILE_BYTES:
            return f"{size} bytes is over the {bundle.DEFAULT_MAX_MANIFEST_FILE_BYTES}-byte limit"
        raw = path.read_bytes()
    except OSError as exc:
        return f"could not be read: {exc.strerror}"
    documents = (
        [(1, raw)]
        if path.suffix.lower() == ".json"
        else [(n, line) for n, line in enumerate(raw.splitlines(), 1) if line.strip()]
    )
    read = _FileRead()
    for number, document in documents:
        try:
            read.results.append(_adapt(document, subject))
        except ValueError as exc:
            read.bad_lines.append((number, str(exc)))
    return read


def scan(folder: Path, *, subject: str, exclude: Path | None = None) -> ScannedRecords:
    """Read every recognised trace export under ``folder``; refuse a folder with none.

    ``exclude`` is the run's ``--out``: when it lies strictly inside the folder its files are never read
    as records, and a previous run's bundle under it never is, wherever it lies."""
    folder = folder.resolve()
    skip: list[Path] = []
    if exclude is not None:
        out = exclude.resolve()
        stale = out / BUNDLE_DIR
        skip.append(stale)
        if (
            _within(folder, stale)
            or stale.is_symlink()
            or (stale.is_dir() and not (stale / "manifest.json").is_file())
        ):
            raise InputError(
                "input.records_out_collides",
                f"{BUNDLE_DIR!r} in the output folder is where the run's bundle is rebuilt, and it "
                "holds the records or is not a previous run's bundle; writing there would overwrite records.",
                "choose an output folder outside the records folder with --out, or an empty one.",
            )
        if _within(out, folder):
            if _same(out, folder) or (
                out.exists() and not stale.is_dir() and any(out.iterdir())
            ):
                raise InputError(
                    "input.records_out_collides",
                    f"the output folder {out.name!r} is the records folder or lies inside it and holds "
                    "files that are not a previous run's output; writing there would overwrite records.",
                    "choose an output folder outside the records folder with --out, or an empty one.",
                )
            skip.append(out)
    by_id: dict[str, dict[str, Any]] = {}
    read: list[dict[str, Any]] = []
    unrecognised: list[dict[str, str]] = []
    bad_lines: list[dict[str, Any]] = []
    counts = {"spans": 0, "spans_skipped": 0, "duplicates": 0, "lines_unrecognised": 0}
    conventions: set[str] = set()

    unread: list[dict[str, str]] = []
    candidates = _candidates(folder, skip, unread)
    unrecognised.extend(unread)
    for rel, path in candidates:
        if path is None:
            unrecognised.append(
                {"path": rel, "reason": "a symlink that leaves the folder is not read"}
            )
            continue
        got = _read(path, subject)
        if isinstance(got, str):
            unrecognised.append({"path": rel, "reason": got})
            continue
        if not got.results:
            unrecognised.append(
                {
                    "path": rel,
                    "reason": got.bad_lines[0][1] if got.bad_lines else "empty file",
                }
            )
            continue
        spans = 0
        for result in got.results:
            spans += result.report.spans_seen
            counts["spans_skipped"] += len(result.report.skipped)
            conventions.update(result.report.conventions)
            for event in result.events:
                if event["id"] in by_id:
                    counts["duplicates"] += 1
                else:
                    by_id[event["id"]] = event
        counts["spans"] += spans
        read.append({"path": rel, "spans": spans})
        if path.suffix.lower() != ".json":
            counts["lines_unrecognised"] += len(got.bad_lines)
            bad_lines += [
                {"path": rel, "line": number, "reason": reason}
                for number, reason in got.bad_lines[: MAX_LISTED_LINES - len(bad_lines)]
            ]

    if not read and any(item["reason"] == NO_GENAI_SPANS for item in unrecognised):
        raise InputError(
            "input.records_no_genai_spans",
            "the folder holds OpenTelemetry traces, but none of their spans is a GenAI operation "
            f"({len(unrecognised)} file(s) not recognised).",
            "instrument the agent with OpenTelemetry GenAI or OpenInference, then export its traces.",
        )
    if not read:
        raise InputError(
            "input.records_none_recognised",
            f"no OpenTelemetry GenAI or OpenInference trace export was found in the folder "
            f"({len(unrecognised)} candidate file(s) not recognised).",
            "point assess at a folder of OTLP/JSON trace exports (.json, .jsonl or .ndjson); "
            "compressed files are not read, so decompress them first.",
        )
    events = sorted(by_id.values(), key=lambda e: (str(e["time"]), str(e["id"])))
    sources = {str(e["source"]): str(e["agentcesourceclass"]) for e in events}
    lines: dict[str, list[str]] = {source: [] for source in sources}
    for e in events:
        lines[str(e["source"])].append(canonical_string(e) + "\n")
    streams = {
        source: "".join(chunk).encode("utf-8") for source, chunk in lines.items()
    }
    for source, data in streams.items():
        if len(data) > bundle.DEFAULT_MAX_MANIFEST_FILE_BYTES:
            raise InputError(
                "input.bundle_manifest_file_too_large",
                f"the events of source {source!r} come to {len(data)} bytes, over the "
                f"{bundle.DEFAULT_MAX_MANIFEST_FILE_BYTES}-byte per-file limit.",
                "assess the records in smaller folders, or reference bulk content by an opaque "
                "locator instead of inlining it (SPEC R12).",
            )
    summary: dict[str, Any] = {
        "adapter": ADAPTER,
        "conventions": sorted(conventions),
        "files": read,
        "unrecognised": unrecognised,
        "unrecognised_lines": bad_lines,
        "events": len(events),
        "sources": sorted(sources),
        **counts,
    }
    return ScannedRecords(
        events=events,
        subject=subject,
        summary=summary,
        _sources=sources,
        _streams=streams,
    )
