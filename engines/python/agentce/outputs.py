"""Checked writes to the paths a command's options name (SPEC §8.7 writes only where asked, §8.5 exit 3).

Every output a command writes goes through one role: a target that exists as the wrong type, a path
that cannot be reached, or a folder that cannot be written is refused under the role's key before
anything is written, and a write that fails anyway is refused under the same key. A file is written
to a fresh sibling with a bounded name and moved over its target, as in TypeScript and Java: the target
is never opened, so a FIFO is never waited on (18.180), a hard link's other name keeps its bytes, and a
dangling link is replaced rather than followed out of the folder (18.184c).
"""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import InputError

NOT_A_FILE = "is not a regular file"
NOT_A_DIRECTORY = "is not a directory"
DANGLING = "is a link that points nowhere"
UNREACHABLE = "cannot be reached (a link loop, a folder that cannot be entered, or a name too long)"
UNWRITABLE = "cannot be written"


@dataclass(frozen=True)
class OutputRole:
    """One option a command writes to: the key it is refused under, the flag shown, the fix, and
    whether it names a file or a directory."""

    key: str
    flag: str
    fix: str
    directory: bool = False


#: The ``--out`` directory of ingest, collect, init and conformance run.
OUT_DIR = OutputRole(
    "input.out_dir_unwritable", "--out", "choose a writable --out directory.", True
)


def _refuse(role: OutputRole, raw: str, reason: str) -> InputError:
    return InputError(role.key, f"{role.flag} {raw!r} {reason}.", role.fix)


def through_missing_folder(path: Path) -> bool:
    """``path`` has a ``..`` right after a folder that does not exist (``nosub/../name``). The OS
    cannot resolve it, so it is refused rather than created, the same in every engine (18.184c)."""
    return any(
        p.name == ".." and not os.path.isdir(p.parent) for p in (path, *path.parents)
    )


def _check_creatable(path: Path, role: OutputRole, raw: str) -> None:
    """A target that does not exist yet: its nearest existing ancestor must be a folder that can be
    written and entered (missing folders between them are created), and the path must not climb
    out of a folder that does not exist (``through_missing_folder``)."""
    if through_missing_folder(path):
        raise _refuse(role, raw, UNREACHABLE)
    for ancestor in path.parents:
        try:
            info = os.stat(ancestor)
        except FileNotFoundError:
            if os.path.islink(ancestor):
                raise _refuse(role, raw, UNREACHABLE) from None
            continue
        except OSError as exc:
            raise _refuse(role, raw, UNREACHABLE) from exc
        if not stat.S_ISDIR(info.st_mode):
            raise _refuse(role, raw, UNREACHABLE)
        if not os.access(ancestor, os.W_OK | os.X_OK):
            raise _refuse(role, raw, UNWRITABLE)
        return


def check_output(raw: str, role: OutputRole) -> Path:
    """Refuse ``raw`` under ``role.key`` unless it can be written: an existing target of the role's
    type that can be written, or a new one whose nearest existing folder can be. Nothing is opened."""
    path = Path(raw)
    try:
        info = os.stat(path)
    except FileNotFoundError:
        if os.path.islink(path):
            raise _refuse(role, raw, DANGLING) from None
        _check_creatable(path, role, raw)
        return path
    except OSError as exc:
        raise _refuse(role, raw, UNREACHABLE) from exc
    if role.directory:
        if not stat.S_ISDIR(info.st_mode):
            raise _refuse(role, raw, NOT_A_DIRECTORY)
        if not os.access(path, os.W_OK | os.X_OK):
            raise _refuse(role, raw, UNWRITABLE)
    else:
        if not stat.S_ISREG(info.st_mode):
            raise _refuse(role, raw, NOT_A_FILE)
        if not os.access(path, os.W_OK):
            raise _refuse(role, raw, UNWRITABLE)
    return path


class NotRegularFile(OSError):
    """The target is not a regular file (a FIFO, a device or a socket)."""


def _sibling(target: Path) -> Path:
    """A fresh name beside ``target`` whose length does not grow with the target's (a 255-byte
    target name still gets a sibling)."""
    return target.parent / f".agentce-{secrets.token_hex(8)}.tmp"


def write_file(
    path: Path, data: str | bytes, *, exclusive: bool = False, mode: int = 0o666
) -> None:
    """Write ``data`` to ``path`` without opening it: an existing target must be a regular file that
    can be written, and the bytes go to a fresh sibling that is then moved over it, keeping the
    target's mode. A link to a regular file is followed, as an open would; a dangling link is
    replaced. ``exclusive`` creates a new file and never replaces one. Raises ``OSError``
    (``NotRegularFile`` for a target of the wrong type); callers key it under their role."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if exclusive:
        _write_fd(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | cloexec, mode), payload
        )
        return
    target = Path(path)
    keep: int | None = None
    try:
        info = os.stat(path)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode):
            raise NotRegularFile(f"{path} is not a regular file")
        if not os.access(path, os.W_OK):
            raise PermissionError(f"{path} cannot be written")
        target = Path(os.path.realpath(path))
        keep = stat.S_IMODE(info.st_mode)
    sibling = _sibling(target)
    fd = os.open(sibling, os.O_WRONLY | os.O_CREAT | os.O_EXCL | cloexec, mode)
    try:
        _write_fd(fd, payload)
        if keep is not None:
            os.chmod(sibling, keep)
        os.replace(sibling, target)
    except BaseException:
        sibling.unlink(missing_ok=True)
        raise


def _write_fd(fd: int, payload: bytes) -> None:
    """Write all of ``payload`` to ``fd`` and close it."""
    try:
        view = memoryview(payload)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)


def write_output(
    raw: str,
    role: OutputRole,
    data: str | bytes,
    *,
    exclusive: bool = False,
    mode: int = 0o666,
) -> Path:
    """Check ``raw`` (``check_output``), create its missing folders and write ``data`` to it
    (``write_file``)."""
    path = check_output(raw, role)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_file(path, data, exclusive=exclusive, mode=mode)
    except NotRegularFile as exc:
        raise _refuse(role, raw, NOT_A_FILE) from exc
    except OSError as exc:
        raise _refuse(role, raw, UNWRITABLE) from exc
    return path


@contextmanager
def writing_into(raw: str, role: OutputRole) -> Iterator[Path]:
    """Check the directory ``raw`` names and refuse any write inside the block that fails anyway;
    files inside it are written with ``write_file``, so a FIFO at a fixed name is refused too."""
    path = check_output(raw, role)
    try:
        yield path
    except OSError as exc:
        raise _refuse(role, raw, UNWRITABLE) from exc
