#!/usr/bin/env python3
"""nul_byte_check - no tracked source file carries a raw NUL byte.

A raw ``\\x00`` byte inside a source file is functionally invisible to GNU ``grep`` (it treats any file
containing one as binary and silently reports zero matches for *anything*, without ``-a``), which makes
every later ``grep``/``grep -n`` search of that file blind -- a footgun for every future session, not a
correctness bug (a literal NUL inside a string literal is semantically identical to its two-character
escape, e.g. Java's ``"\\0"``, TypeScript's ``"\\0"``, in every language this repo uses). Two such bytes
were found in ``engines/java/src/main/java/org/agentce/Report.java`` and
``engines/typescript/src/report.ts`` (item 18.24; MAINTAINER-NOTES 2026-09-29 "Report.java NUL bytes")
and fixed there; this check guards against a future reintroduction.

Scans every tracked file except a small denylist of extensions that are legitimately binary (jars,
wheels, images, archives, fonts, compiled artifacts): those may contain NUL bytes as ordinary binary
content, and are outside what "source file" means here.

Uses only the standard library and git; no network, no learned component.

    nul_byte_check.py             scan every tracked file in the working tree
    nul_byte_check.py --self-test prove detection has teeth (a seeded NUL byte) and clears clean text
"""

from __future__ import annotations

import sys
from pathlib import Path

from spdx_check import tracked_files

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: Extensions that are legitimately binary and may contain NUL bytes as ordinary content -- excluded
#: rather than allowlisting every source extension, so a new source language needs no update here.
BINARY_EXTENSIONS = frozenset(
    {
        "jar",
        "whl",
        "bin",
        "class",
        "pyc",
        "so",
        "dylib",
        "dll",
        "wasm",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "ico",
        "webp",
        "bmp",
        "pdf",
        "zip",
        "gz",
        "tar",
        "tgz",
        "7z",
        "woff",
        "woff2",
        "ttf",
        "otf",
        "eot",
    }
)


def is_binary_path(path: str) -> bool:
    return Path(path).suffix.lstrip(".").lower() in BINARY_EXTENSIONS


def check(files: dict[str, bytes]) -> list[str]:
    """``path: N raw NUL byte(s)`` for every file in ``files`` (path -> content) that is not a
    known-binary extension and contains at least one ``\\x00`` byte."""
    problems: list[str] = []
    for path, content in sorted(files.items()):
        if is_binary_path(path):
            continue
        count = content.count(b"\x00")
        if count:
            problems.append(f"{path}: {count} raw NUL byte(s)")
    return problems


def self_test() -> int:
    # A seeded fault: a source-like file carrying a raw NUL byte must be caught.
    dirty = check({"src/example.ts": b"const key = `${a}\x00${b}`;\n"})
    assert dirty == ["src/example.ts: 1 raw NUL byte(s)"], dirty
    # Clean text of the same shape (the escaped form) must not be flagged.
    clean = check({"src/example.ts": b"const key = `${a}\\0${b}`;\n"})
    assert clean == [], clean
    # A real binary extension carrying a NUL byte is not a violation.
    binary = check({"engines/java/app.jar": b"PK\x00\x00garbage"})
    assert binary == [], binary
    # Two NUL bytes in one file are both counted, not just detected as present/absent.
    two = check({"src/two.ts": b"\x00\x00"})
    assert two == ["src/two.ts: 2 raw NUL byte(s)"], two
    print("NUL BYTE CHECK SELF-TEST OK")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    files: dict[str, bytes] = {}
    for rel in tracked_files():
        if is_binary_path(rel):
            continue
        path = ROOT / rel
        try:
            files[rel] = path.read_bytes()
        except OSError:
            continue  # a tracked path that is a submodule or has since been removed on disk
    problems = check(files)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"NUL BYTE CHECK FAILED: {len(problems)} file(s)", file=sys.stderr)
        return 1
    print("NUL BYTE CHECK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
