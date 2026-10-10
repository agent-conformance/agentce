"""engine_goldens_fresh_check - every TypeScript and Java parity golden is what the live Python engine
writes today (18.37d, VG-ENGINE-GOLDENS-FRESH).

The TypeScript and Java engines show byte-identical output to Python by comparing against goldens
captured from Python. Nothing used to re-capture them, so a Python-only change (the graph builder,
the state directory) left the ports' tests green on a stale snapshot. Each engine's
``generate_goldens.py`` now builds every golden its tests read from one map and, with ``--check``,
compares the map with the committed files byte for byte, writes nothing, and exits 1 naming each
stale, missing or unproduced ``*-golden.*`` file.

The real run calls both generators with ``--check``. ``--self-test`` proves ``--check`` is honest: on
a copy of the TypeScript testdata directory (beside links to the real Python engine and catalogs, so
the copied generator resolves the same inputs) it edits one golden, deletes one and adds a stray
``*-golden.*`` file, and requires exit 1, all three names, and the copy left byte for byte as it was;
the unedited copy must pass.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GENERATORS = (
    Path("engines/typescript/testdata/generate_goldens.py"),
    Path("engines/java/src/test/resources/testdata/generate_goldens.py"),
)


def run_check(root: Path, generator: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO / "engines" / "python"),
            "--frozen",
            "python",
            str(root / generator),
            "--check",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def snapshot(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def self_test() -> int:
    generator = GENERATORS[0]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "engines").mkdir()
        (root / "engines" / "python").symlink_to(REPO / "engines" / "python")
        (root / "spec").symlink_to(REPO / "spec")
        testdata = root / generator.parent
        shutil.copytree(REPO / generator.parent, testdata)

        clean = run_check(root, generator)
        if clean.returncode != 0:
            print(
                f"SELF-TEST FAILED: an unedited copy did not pass:\n{clean.stderr}",
                file=sys.stderr,
            )
            return 1

        edited, deleted, stray = (
            "assess-golden.json",
            "state-golden.json",
            "stray-golden.json",
        )
        with (testdata / edited).open("a", encoding="utf-8") as handle:
            handle.write(" ")
        (testdata / deleted).unlink()
        (testdata / stray).write_text("{}\n", encoding="utf-8")
        before = snapshot(testdata)
        seeded = run_check(root, generator)
        problems = []
        if seeded.returncode != 1:
            problems.append(f"exit {seeded.returncode}, expected 1")
        problems += [
            f"{name} not named"
            for name in (edited, deleted, stray)
            if name not in seeded.stderr
        ]
        if snapshot(testdata) != before:
            problems.append("--check changed the testdata directory")
    if problems:
        print("SELF-TEST FAILED: " + "; ".join(problems), file=sys.stderr)
        print(seeded.stderr, file=sys.stderr)
        return 1
    print(
        "ENGINE-GOLDENS-FRESH SELF-TEST PASSED (edited, deleted and stray goldens refused; nothing written)"
    )
    return 0


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if argv:
        print(
            f"usage: engine_goldens_fresh_check.py [--self-test] (got {argv!r})",
            file=sys.stderr,
        )
        return 2
    failed = False
    for generator in GENERATORS:
        result = run_check(REPO, generator)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            failed = True
            print(
                f"STALE: {generator} --check exited {result.returncode}",
                file=sys.stderr,
            )
    if failed:
        print(
            "ENGINE-GOLDENS-FRESH FAILED: regenerate with each generate_goldens.py and commit",
            file=sys.stderr,
        )
        return 1
    print(
        "ENGINE-GOLDENS-FRESH OK (every TypeScript and Java parity golden matches the live Python engine)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
