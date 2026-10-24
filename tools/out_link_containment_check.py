"""out_link_containment_check - a run never writes through a link in --out that leads outside it (item 18.184s).

SPEC 8.7 has a run write only under --out and --state, and ruling R7 refuses a link under --out that leads
outside before anything is written. For each of the three engines built from source, the check plants one
link in a fresh quickstart --out, runs ``quickstart --out``, and expects exit 3 with
``input.out_link_outside`` naming the entry, the outside file or folder unchanged, and --out holding only
the link. The links: ``integrity.jsonl`` and ``manifest.json`` to an outside file, and the folder
``packs`` to an outside folder. Then two --out paths the engine cannot look at (item 18.184zd): one under a
folder that cannot be entered (skipped when run as root, which enters any folder) and one whose last name is
too long, and an --out that can be written but not listed holding a link to an outside file; each must be refused ``input.out_dir_unwritable`` with exit 3, never ``internal.unexpected``.

Usage (from the repository root, after ``pnpm build`` in engines/typescript and
``./gradlew :assemble installDist`` in engines/java):
    out_link_containment_check.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from json_parity_check import ENGINES, ROOT, cli_argv

KEPT = "kept\n"
LINKS = (("integrity.jsonl", False), ("manifest.json", False), ("packs", True))


def _snapshot(path: Path) -> list[tuple[str, str]]:
    if path.is_file():
        return [("", path.read_text(encoding="utf-8"))]
    return sorted(
        (p.relative_to(path).as_posix(), p.read_text(encoding="utf-8"))
        for p in path.rglob("*")
        if p.is_file()
    )


def _quickstart(engine: str, out: Path) -> tuple[int, str]:
    """Run quickstart into ``out``: its exit code and output, with ``error [key]: `` shortened to ``key: ``."""
    proc = subprocess.run(
        cli_argv(engine) + ["quickstart", "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    return proc.returncode, re.sub(
        r"error \[([a-z_.]+)\]: ", r"\1: ", proc.stdout + proc.stderr
    )


def problems_for(engine: str, work: Path) -> list[str]:
    problems: list[str] = []
    for entry, folder in LINKS:
        case = work / f"{engine}-{entry}"
        outside = case / "outside"
        if folder:
            outside.mkdir(parents=True)
            (outside / "kept.txt").write_text(KEPT, encoding="utf-8")
        else:
            case.mkdir(parents=True)
            outside.write_text(KEPT, encoding="utf-8")
        before = _snapshot(outside)
        out = case / "out"
        out.mkdir()
        (out / entry).symlink_to(outside, target_is_directory=folder)
        code, text = _quickstart(engine, out)
        where = f"{engine} quickstart --out with {entry} linked outside"
        if code != 3 or f"input.out_link_outside: {entry} in --out" not in text:
            problems.append(
                f"{where}: expected exit 3 and input.out_link_outside naming {entry}, got exit "
                f"{code}: {text.strip()[-300:]}"
            )
        if _snapshot(outside) != before:
            problems.append(
                f"{where}: the outside {'folder' if folder else 'file'} was changed"
            )
        held = sorted(p.relative_to(out).as_posix() for p in out.iterdir())
        if held != [entry]:
            problems.append(f"{where}: --out holds {held}, expected only the link")
    return problems


def unwritable_problems_for(engine: str, work: Path) -> list[str]:
    """--out with a too-long name, under a folder that cannot be entered, or one that can be written but
    not listed (holding a link outside, which must not be written through): input.out_dir_unwritable."""
    problems: list[str] = []
    shut = work / f"{engine}-shut"
    shut.mkdir()
    cases = [("a name too long", work / f"{engine}-{'n' * 300}")]
    blind = work / f"{engine}-blind"
    blind.mkdir()
    outside = work / f"{engine}-blind-outside"
    outside.write_text(KEPT, encoding="utf-8")
    (blind / "integrity.jsonl").symlink_to(outside)
    if os.geteuid() != 0:
        cases.append(("a folder that cannot be entered", shut / "out"))
        cases.append(("a folder that can be written but not listed", blind))
    shut.chmod(0)
    blind.chmod(0o300)
    try:
        for what, out in cases:
            code, text = _quickstart(engine, out)
            if code != 3 or "input.out_dir_unwritable" not in text:
                problems.append(
                    f"{engine} quickstart --out ({what}): expected exit 3 and input.out_dir_unwritable, "
                    f"got exit {code}: {text.strip()[-300:]}"
                )
    finally:
        shut.chmod(0o755)
        blind.chmod(0o755)
    if outside.read_text(encoding="utf-8") != KEPT:
        problems.append(f"{engine} wrote through a link in an --out it cannot list")
    return problems


def main() -> int:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for engine in ENGINES:
            problems += problems_for(engine, Path(tmp))
            problems += unwritable_problems_for(engine, Path(tmp))
    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        return 1
    print(
        f"PASS {len(LINKS)} links in --out leading outside are refused input.out_link_outside with nothing "
        "written, and an --out that cannot be entered, listed or named is refused "
        "input.out_dir_unwritable, in python, typescript and java"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
