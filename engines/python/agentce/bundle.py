"""Evidence bundle loading and manifest verification (SPEC §8.1, §5.4).

An evidence bundle is a directory with ``events/*.jsonl``, ``attestations/``, ``reference/``, and a
``manifest.json`` that lists every file with its SHA-256. Loading verifies that the manifest is
present and that every listed file hashes correctly; a missing or mismatching manifest aborts the run
with exit code 3 (an :class:`~agentce.errors.InputError`), never a silent partial read.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import canonical
from .errors import InputError, UnreadableError
from .error_catalogue import MESSAGE_KEYS
from . import signing
from .signing import parse_untrusted_json

#: Ceiling on any single manifest-listed file's byte size, checked with `stat()` before the file is
#: opened or hashed (SPEC §8.1) -- so a hostile bundle cannot force gigabytes to be read before any check has a
#: chance to refuse it.
DEFAULT_MAX_MANIFEST_FILE_BYTES = 512 * 1024 * 1024


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
    #: The bytes of manifest.json and of each listed member (keyed by its path under ``root``), exactly as
    #: :func:`load_bundle` checked and hashed them: ingest and the copy for sharing use these, never a second read
    #: of the same name, so a file changed after the check never reaches a report (18.184f).
    manifest_bytes: bytes = b""
    contents: dict[Path, bytes] = field(default_factory=dict)
    #: The same bytes keyed by each listed path as the manifest writes it (the coverage denominator's lookup).
    members: dict[str, bytes] = field(default_factory=dict)

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
    propagate as an unexpected error. Unreadable reads as absent here; callers test permission first
    and refuse it as ``input.bundle_unreadable``."""
    try:
        return signing.is_regular_file(path, unreadable_is_absent=True)
    except (OSError, RuntimeError, ValueError):
        return False


def confine_to_root(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` against ``root``; return the path only if it stays inside ``root`` after
    symlinks resolve, else ``None``. Every bundle-adjacent reference an adversarial evidence bundle
    can carry -- the primary manifest's own file list, a coverage denominator's manifest, an
    integrity block's ``sig_ref`` -- is confined through this one function, so a symlink escape, a
    literal ``..``/absolute path, a symlink loop, or an embedded NUL byte are refused the same
    deliberate way everywhere, never left to surface as an unexpected error. A path that cannot be
    looked at (missing, too long, under a file or an unreadable folder) is returned for the
    caller's own checks to report."""
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    candidate = root / rel
    try:
        resolved = candidate.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if _meets_symlink_loop(resolved):
        return None
    if (
        resolved != resolved_root
        and not resolved.is_relative_to(resolved_root)
        # The same folder spelled another way (letter case), judged by identity (18.184l).
        and not signing.inside_folder(resolved_root, resolved, or_root=True)
    ):
        return None
    return candidate


def _changed_while_read(rel: str) -> InputError:
    return InputError(
        "input.bundle_manifest_path",
        f"{rel} changed while it was being read: the file opened is not the one checked to be inside "
        "the bundle root.",
        "keep the bundle folder unchanged while the command reads it, then run the command again.",
    )


def read_confined(
    root: Path, rel: str, member: Path, limit: int | None = None
) -> bytes:
    """The bytes of ``member``, the path :func:`confine_to_root` returned for ``rel``, read through one open
    file, and only when, once that file is open, ``rel`` still resolves inside ``root`` to the very file opened.
    A member swapped between the confinement check and the read (for a link outside the bundle, or for any other
    file) is refused with ``input.bundle_manifest_path`` and its bytes are never used (18.184f). At most
    ``limit + 1`` bytes are read when ``limit`` is set, so the caller can refuse an oversize file. An ``OSError``
    from opening or reading (``PermissionError`` included) propagates for the caller to report."""
    with open(
        os.open(member, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)), "rb"
    ) as handle:
        opened = os.fstat(handle.fileno())
        again = confine_to_root(root, rel)
        try:
            same = again is not None and os.path.samestat(os.stat(again), opened)
        except (OSError, ValueError):
            same = False
        if not same or not stat.S_ISREG(opened.st_mode):
            raise _changed_while_read(rel)
        return handle.read() if limit is None else handle.read(limit + 1)


def _meets_symlink_loop(resolved: Path) -> bool:
    """True when ``resolved`` still ends in a symlink loop. Python 3.12's ``resolve(strict=False)``
    raises on a loop; 3.13 and newer return the looping path unchanged, so ask the OS the same
    question 3.12 does (stat the resolved path, ELOOP) to keep one answer on every version."""
    try:
        os.stat(resolved)
    except OSError as exc:
        return exc.errno == errno.ELOOP
    return False


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


def copy_bundle(loaded: Bundle, dst_root: Path) -> None:
    """Write exactly the files ``loaded``'s own manifest lists (plus the manifest itself) into ``dst_root``, from
    the bytes :func:`load_bundle` checked and hashed (SPEC §8.1's own bundle-safety rule, reused here rather than a
    plain ``shutil.copytree``): a symlink that escapes the bundle, a file present on disk but not in the manifest,
    or a file changed after the check never reaches the copy (18.184f). Call :func:`load_bundle` on ``dst_root``
    afterwards to confirm the copy is faithful."""
    dst_root.mkdir(parents=True, exist_ok=True)
    (dst_root / "manifest.json").write_bytes(loaded.manifest_bytes)
    for entry in loaded.manifest.get("files", []):
        rel = str(entry["path"])
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(loaded.members[rel])


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
    if confine_to_root(bundle_dir, "manifest.json") is None:
        raise InputError(
            "input.bundle_manifest_path",
            "manifest.json resolves outside the bundle root (a symlink or junction escapes it), "
            "so the bundle's list of files does not travel with the bundle.",
            "replace the manifest.json link with the manifest file itself, inside the bundle folder.",
        )
    try:
        manifest_bytes = read_confined(bundle_dir, "manifest.json", manifest_path)
        parsed = parse_untrusted_json(manifest_bytes)
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
    contents: dict[Path, bytes] = {}
    members: dict[str, bytes] = {}
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
        data = b""
        if size <= DEFAULT_MAX_MANIFEST_FILE_BYTES:
            try:
                data = read_confined(
                    bundle_dir, rel, member, DEFAULT_MAX_MANIFEST_FILE_BYTES
                )
            except PermissionError as exc:
                raise _unreadable(bundle_dir, rel) from exc
            # A file that grew past the limit after the stat is refused the same way.
            size = max(size, len(data))
        if size > DEFAULT_MAX_MANIFEST_FILE_BYTES:
            raise InputError(
                "input.bundle_manifest_file_too_large",
                f"{rel} is {size} bytes, over the {DEFAULT_MAX_MANIFEST_FILE_BYTES}-byte "
                "per-file limit.",
                "split large evidence into more, smaller files, or reference bulk content by an "
                "opaque locator instead of inlining it (SPEC R12).",
            )
        if hashlib.sha256(data).hexdigest() != _normalise_digest(entry["sha256"]):
            raise InputError(
                "input.bundle_manifest_mismatch",
                f"{rel} does not match its manifest SHA-256.",
                "regenerate the bundle so its files match the manifest.",
            )
        contents[member] = data
        members[rel] = data
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
        manifest_bytes=manifest_bytes,
        contents=contents,
        members=members,
    )
