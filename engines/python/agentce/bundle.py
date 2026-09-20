"""Evidence bundle loading and manifest verification (SPEC §8.1, §5.4).

An evidence bundle is a directory with ``events/*.jsonl``, ``attestations/``, ``reference/``, and a
``manifest.json`` that lists every file with its SHA-256. Loading verifies that the manifest is
present and that every listed file hashes correctly; a missing or mismatching manifest aborts the run
with exit code 3 (an :class:`~agentce.errors.InputError`), never a silent partial read.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import canonical
from .errors import InputError
from .error_catalogue import MESSAGE_KEYS


def _sha256_hex(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_digest(raw: str) -> str:
    return raw.split(":", 1)[1].lower() if raw.startswith("sha256:") else raw.lower()


@dataclass(frozen=True)
class Bundle:
    """A verified evidence bundle."""

    root: Path
    manifest: dict[str, Any]
    event_files: tuple[Path, ...]
    sources: frozenset[str] | None
    #: Declared trust class per source id, for sources whose manifest entry carries a ``class``
    #: (SPEC §6.4). Ingest quarantines an event whose ``agentcesourceclass`` differs (``class_mismatch``).
    source_classes: dict[str, str] | None = None

    @property
    def digest(self) -> str:
        """The bundle digest: SHA-256 of the RFC 8785 canonical manifest (SPEC §8.1)."""
        return "sha256:" + canonical.sha256_hex(self.manifest)


def _safe_member(root: Path, rel: str) -> Path:
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise InputError(
            "input.bundle_manifest_path",
            f"manifest lists an unsafe path {rel!r}.",
            "the manifest must list only paths inside the bundle.",
        )
    return root / rel


def load_bundle(bundle_dir: Path) -> Bundle:
    """Load and verify the bundle at ``bundle_dir``; raise :class:`InputError` (exit 3) on any problem."""
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise InputError(
            "input.bundle_manifest_missing",
            f"the bundle at {str(bundle_dir)!r} has no manifest.json.",
            MESSAGE_KEYS["input.bundle_manifest_missing"].fix,
        )
    try:
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InputError(
            "input.bundle_manifest_invalid",
            f"manifest.json is not valid JSON: {exc}.",
            "regenerate the bundle so its manifest.json is well-formed.",
        ) from exc

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise InputError(
            "input.bundle_manifest_files",
            "manifest.json has no non-empty 'files' array.",
            "the manifest must list every file with its path and sha256.",
        )

    event_files: list[Path] = []
    for entry in files:
        if not isinstance(entry, dict) or "path" not in entry or "sha256" not in entry:
            raise InputError(
                "input.bundle_manifest_entry",
                "a 'files' entry is missing 'path' or 'sha256'.",
                'each entry needs {"path": ..., "sha256": ...}.',
            )
        rel = str(entry["path"])
        member = _safe_member(bundle_dir, rel)
        if not member.is_file():
            raise InputError(
                "input.bundle_manifest_mismatch",
                f"manifest lists {rel!r}, which is missing from the bundle.",
                "regenerate the bundle so its files match the manifest.",
            )
        actual = _sha256_hex(member)
        if actual != _normalise_digest(str(entry["sha256"])):
            raise InputError(
                "input.bundle_manifest_mismatch",
                f"{rel!r} does not match its manifest SHA-256.",
                "regenerate the bundle so its files match the manifest.",
            )
        if rel.startswith("events/") and rel.endswith(".jsonl"):
            event_files.append(member)

    sources: frozenset[str] | None = None
    source_classes: dict[str, str] = {}
    declared = manifest.get("sources")
    if isinstance(declared, list):
        ids: set[str] = set()
        for item in declared:
            if not isinstance(item, dict) or "id" not in item:
                continue
            source_id = str(item["id"])
            ids.add(source_id)
            declared_class = item.get("class")
            if isinstance(declared_class, str) and declared_class:
                source_classes[source_id] = declared_class
        if ids:
            sources = frozenset(ids)

    return Bundle(
        root=bundle_dir,
        manifest=manifest,
        event_files=tuple(sorted(event_files)),
        sources=sources,
        source_classes=source_classes or None,
    )
