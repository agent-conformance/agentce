"""Evidence bundle loading and manifest verification (SPEC §8.1, §5.4).

An evidence bundle is a directory with ``events/*.jsonl``, ``attestations/``, ``reference/``, and a
``manifest.json`` that lists every file with its SHA-256. Loading verifies that the manifest is
present and that every listed file hashes correctly; a missing or mismatching manifest aborts the run
with exit code 3 (an :class:`~agentce.errors.InputError`), never a silent partial read.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import canonical
from .errors import InputError, UnreadableError
from .error_catalogue import MESSAGE_KEYS
from .signing import parse_untrusted_json

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


def _unreadable(bundle_dir: Path, rel: str) -> UnreadableError:
    return UnreadableError(
        "input.bundle_unreadable",
        "the evidence bundle",
        bundle_dir,
        rel,
        "evidence bundle",
    )


def permission_denied(path: Path) -> bool:
    """True when ``path`` cannot even be looked at because of a permission error (an unreadable
    parent folder): the one reason a listed file that is "not a file" is unreadable, not missing."""
    try:
        path.stat()
    except PermissionError:
        return True
    except (OSError, RuntimeError, ValueError):
        return False
    return False


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
            f"manifest lists an unsafe path {rel}: it is absolute, contains '..', resolves "
            "outside the bundle root (a symlink or junction escapes it), or cannot be safely "
            "resolved.",
            "the manifest must list only paths that stay inside the bundle after symlinks resolve.",
        )
    return member


def copy_bundle(src_root: Path, dst_root: Path) -> None:
    """Copy exactly the files a bundle's own ``manifest.json`` lists (plus the manifest itself) into
    ``dst_root``, each resolved through :func:`confine_to_root` first (SPEC §8.1's own bundle-safety
    rule, reused here rather than a plain ``shutil.copytree``): a symlink that escapes ``src_root``, or
    a file present on disk but not in the manifest, never reaches the copy. Does not itself verify the
    bundle; call :func:`load_bundle` on ``dst_root`` afterwards to confirm the copy is faithful."""
    manifest_path = src_root / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    dst_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, dst_root / "manifest.json")
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        rel = str(entry["path"])
        src = confine_to_root(src_root, rel)
        if src is None or not safe_is_file(src):
            continue  # load_bundle on dst_root reports this the same way it reports any other gap
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def load_bundle(bundle_dir: Path) -> Bundle:
    """Load and verify the bundle at ``bundle_dir``; raise :class:`InputError` (exit 3) on any problem."""
    manifest_path = bundle_dir / "manifest.json"
    if not safe_is_file(manifest_path):
        if permission_denied(manifest_path):
            raise _unreadable(bundle_dir, "manifest.json")
        raise InputError(
            "input.bundle_manifest_missing",
            f"the bundle at {str(bundle_dir)} has no manifest.json.",
            MESSAGE_KEYS["input.bundle_manifest_missing"].fix,
        )
    try:
        parsed = parse_untrusted_json(manifest_path.read_bytes())
    except PermissionError as exc:
        raise _unreadable(bundle_dir, "manifest.json") from exc
    except (OSError, ValueError) as exc:
        raise InputError(
            "input.bundle_manifest_invalid",
            "manifest.json is not valid JSON.",
            "regenerate the bundle so its manifest.json is well-formed.",
        ) from exc
    if not isinstance(parsed, dict):
        raise InputError(
            "input.bundle_manifest_invalid",
            "manifest.json is not an object.",
            "regenerate the bundle so its manifest.json is well-formed.",
        )
    manifest: dict[str, Any] = parsed

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise InputError(
            "input.bundle_manifest_files",
            "manifest.json has no non-empty 'files' array.",
            "the manifest must list every file with its path and sha256.",
        )

    event_files: list[Path] = []
    for entry in files:
        if (
            not isinstance(entry, dict)
            or "path" not in entry
            or "sha256" not in entry
            # A non-string path/sha256 folds into the same refusal as a missing one (not a type
            # this caller could usefully stringify, and `str()`/`JSON.stringify`/Jackson `asText()`
            # disagree on how to render an array, object or null, which would make the following
            # "missing from the bundle"/"does not match" messages diverge across engines for no
            # reason a reader could use).
            or not isinstance(entry["path"], str)
            or not isinstance(entry["sha256"], str)
        ):
            raise InputError(
                "input.bundle_manifest_entry",
                "a 'files' entry is missing 'path' or 'sha256'.",
                'each entry needs {"path": ..., "sha256": ...}.',
            )
        rel: str = entry["path"]
        member = _safe_member(bundle_dir, rel)
        if not safe_is_file(member):
            if permission_denied(member):
                raise _unreadable(bundle_dir, rel)
            raise InputError(
                "input.bundle_manifest_mismatch",
                f"manifest lists {rel}, which is missing from the bundle.",
                "regenerate the bundle so its files match the manifest.",
            )
        try:
            size = member.stat().st_size
        except PermissionError as exc:
            raise _unreadable(bundle_dir, rel) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise InputError(
                "input.bundle_manifest_mismatch",
                f"manifest lists {rel}, which could not be safely accessed.",
                "regenerate the bundle so its files match the manifest.",
            ) from exc
        if size > DEFAULT_MAX_MANIFEST_FILE_BYTES:
            raise InputError(
                "input.bundle_manifest_file_too_large",
                f"{rel} is {size} bytes, over the {DEFAULT_MAX_MANIFEST_FILE_BYTES}-byte "
                "per-file limit.",
                "split large evidence into more, smaller files, or reference bulk content by an "
                "opaque locator instead of inlining it (SPEC R12).",
            )
        try:
            actual = _sha256_hex(member)
        except PermissionError as exc:
            raise _unreadable(bundle_dir, rel) from exc
        if actual != _normalise_digest(entry["sha256"]):
            raise InputError(
                "input.bundle_manifest_mismatch",
                f"{rel} does not match its manifest SHA-256.",
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
            # A non-string `id` (an array, object, number, null) folds into the same "not declared"
            # skip as a missing one, rather than coercing with `str()`: Python's `str(["x"])`,
            # TypeScript's `String(["x"])` and Java's Jackson `asText()` each render a non-string
            # JSON value differently, which would make a tampered source's trust classification
            # diverge across engines for no reason a reader could use (the same reasoning
            # `bundle.py`'s `files[].path`/`sha256` guard already applies; verifier round 1, 18.65).
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            source_id = item["id"]
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
