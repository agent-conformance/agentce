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

#: Ceiling on any single manifest-listed file's byte size, checked with `stat()` before the file is
#: opened or hashed (SPEC §8.1) -- so a hostile bundle cannot force gigabytes to stream through
#: `_sha256_hex` before any check has a chance to refuse it.
DEFAULT_MAX_MANIFEST_FILE_BYTES = 512 * 1024 * 1024


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


def safe_is_file(path: Path) -> bool:
    """``path.is_file()``, but an OS-level hazard a confined path can still hit at access time (a
    symlink loop, a name too long for the filesystem) is treated as "not present", never left to
    propagate as an unexpected error."""
    try:
        return path.is_file()
    except (OSError, RuntimeError, ValueError):
        return False


def confine_to_root(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` against ``root``; return the path only if it stays inside ``root`` after
    symlinks resolve, else ``None``. Every bundle-adjacent reference an adversarial evidence bundle
    can carry -- the primary manifest's own file list, a coverage denominator's manifest, an
    integrity block's ``sig_ref`` -- is confined through this one function, so a symlink escape, a
    literal ``..``/absolute path, a symlink loop, an embedded NUL byte, or a path segment too long
    for the filesystem are refused the same deliberate way everywhere, never left to surface as an
    unexpected error."""
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    candidate = root / rel
    try:
        resolved = candidate.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
        return None
    return candidate


def _safe_member(root: Path, rel: str) -> Path:
    member = confine_to_root(root, rel)
    if member is None:
        raise InputError(
            "input.bundle_manifest_path",
            f"manifest lists an unsafe path {rel!r}: it is absolute, contains '..', resolves "
            "outside the bundle root (a symlink or junction escapes it), or cannot be safely "
            "resolved.",
            "the manifest must list only paths that stay inside the bundle after symlinks resolve.",
        )
    return member


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
        if not safe_is_file(member):
            raise InputError(
                "input.bundle_manifest_mismatch",
                f"manifest lists {rel!r}, which is missing from the bundle.",
                "regenerate the bundle so its files match the manifest.",
            )
        try:
            size = member.stat().st_size
        except (OSError, RuntimeError, ValueError) as exc:
            raise InputError(
                "input.bundle_manifest_mismatch",
                f"manifest lists {rel!r}, which could not be safely accessed: {exc}.",
                "regenerate the bundle so its files match the manifest.",
            ) from exc
        if size > DEFAULT_MAX_MANIFEST_FILE_BYTES:
            raise InputError(
                "input.bundle_manifest_file_too_large",
                f"{rel!r} is {size} bytes, over the {DEFAULT_MAX_MANIFEST_FILE_BYTES}-byte "
                "per-file limit.",
                "split large evidence into more, smaller files, or reference bulk content by an "
                "opaque locator instead of inlining it (SPEC R12).",
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
