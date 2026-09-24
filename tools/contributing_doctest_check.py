"""Repository check: CONTRIBUTING.md's documented commands genuinely test the code.

A newcomer copies the commands CONTRIBUTING.md shows and expects them to work, and to catch a real
defect. This runs every runnable fenced ``bash``/``sh``/``shell``/``console`` block in
CONTRIBUTING.md for real, from the repository root (a ``no-run`` fence-suffix opts a block out, the
convention ``docs_doctest_check.py`` also uses) -- rather than statically parsing the shell to guess
whether it invokes the test suite and linter. GREEN requires three facts at once: every runnable
block exits 0 on a clean checkout; with a failing test seeded in the reference engine
(``engines/python``), at least one block surfaces it; and, separately, with a lint violation seeded
there, at least one block surfaces it. A block that only names ``pytest``/``ruff``, short-circuits
past them (``true || pytest``), or is scoped to a package the fault is not in never catches the
seeded fault, so it cannot reach GREEN.

Usage:
    contributing_doctest_check.py              check the repository (exit 1 on any violation)
    contributing_doctest_check.py --self-test  prove the check discriminates: real commands pass and
                                                catch both seeded faults; a short-circuited, a
                                                named-only, and a bare unscoped block do not
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE_REL = Path("engines") / "python"
SEED_TEST_REL = Path("tests") / "test_zzz_agentce_contributing_doctest_seed.py"
SEED_LINT_REL = Path("agentce") / "_zzz_agentce_contributing_doctest_seed.py"
TEST_SRC = (
    "def test_zzz_agentce_contributing_doctest_seed():\n"
    '    assert False, "seeded fault: the contributor doc-test proves pytest runs"\n'
)
LINT_SRC = "import os\n\nx=1\n"  # F401 unused import + an unformatted line
BLOCK_TIMEOUT_S = 250

FENCE_OPEN = re.compile(r"^```(bash|sh|shell|console)(\s+no-run\b.*)?\s*$")
FENCE_CLOSE = re.compile(r"^```\s*$")


def extract(text: str) -> list[tuple[bool, str]]:
    """Return ``(no_run, script)`` for every runnable fenced block in ``text``."""
    lines = text.splitlines()
    found: list[tuple[bool, str]] = []
    i = 0
    while i < len(lines):
        m = FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        no_run = bool(m.group(2))
        i += 1
        body = []
        while i < len(lines) and not FENCE_CLOSE.match(lines[i]):
            body.append(lines[i])
            i += 1
        i += 1
        found.append((no_run, "\n".join(body)))
    return found


def _run_blocks(
    runnable: list[str], root: Path, stop_on_fail: bool
) -> list[tuple[str, int, str]]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)  # a stray activated venv must not mask the result
    failures = []
    for script in runnable:
        proc = subprocess.run(
            ["bash", "-c", "set -e\n" + script],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=BLOCK_TIMEOUT_S,
        )
        if proc.returncode != 0:
            first = next((ln for ln in script.splitlines() if ln.strip()), script)
            failures.append(
                (first, proc.returncode, (proc.stderr or proc.stdout)[-600:])
            )
            if stop_on_fail:
                break
    return failures


def check(
    root: Path, contributing: Path | None = None, engine: Path | None = None
) -> int:
    """Run every runnable block from ``contributing`` (default ``root/CONTRIBUTING.md``) at
    ``root``, seeding a broken test and a lint violation in ``engine`` (default
    ``root/engines/python``) to prove they have teeth. Leaves the tree clean; returns 0 iff every
    fact holds."""
    contributing = contributing or (root / "CONTRIBUTING.md")
    if not contributing.exists():
        print(f"SETUP: {contributing} not found", file=sys.stderr)
        return 3

    blocks = extract(contributing.read_text(encoding="utf-8"))
    runnable = [script for no_run, script in blocks if not no_run]
    if not runnable:
        print(
            f"SETUP: {contributing} has {len(blocks)} fenced bash/sh/shell/console block(s), "
            f"0 runnable (none without a 'no-run' marker) -- no documented command exists to "
            f"prove the toolchain works",
            file=sys.stderr,
        )
        return 3

    engine = engine or (root / ENGINE_REL)
    seed_test = engine / SEED_TEST_REL
    seed_lint = engine / SEED_LINT_REL

    def cleanup() -> None:
        for p in (seed_test, seed_lint):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    if not engine.is_dir():
        print(f"SETUP: reference engine {engine} not found", file=sys.stderr)
        return 3

    cleanup()  # clear any leftover from an interrupted prior run
    problems = []
    try:
        clean_fail = _run_blocks(runnable, root, stop_on_fail=False)
        if clean_fail:
            for first, rc, tail in clean_fail:
                print(f"FAIL clean (exit {rc}): {first!r}\n{tail}", file=sys.stderr)
            problems.append(
                "a documented command fails on a clean checkout (a newcomer following "
                "CONTRIBUTING.md would hit this error)"
            )

        seed_test.parent.mkdir(parents=True, exist_ok=True)
        seed_test.write_text(TEST_SRC, encoding="utf-8")
        try:
            caught = _run_blocks(runnable, root, stop_on_fail=True)
        finally:
            try:
                seed_test.unlink()
            except FileNotFoundError:
                pass
        if not caught:
            problems.append(
                "the documented commands do not surface a broken test in the reference engine "
                "-- they never actually run pytest (named-only, short-circuited, or scoped to "
                "the wrong package)"
            )

        seed_lint.parent.mkdir(parents=True, exist_ok=True)
        seed_lint.write_text(LINT_SRC, encoding="utf-8")
        try:
            caught = _run_blocks(runnable, root, stop_on_fail=True)
        finally:
            try:
                seed_lint.unlink()
            except FileNotFoundError:
                pass
        if not caught:
            problems.append(
                "the documented commands do not surface a lint violation in the reference "
                "engine -- they never actually run ruff"
            )
    finally:
        cleanup()

    if problems:
        print("RED:", file=sys.stderr)
        for p in problems:
            print(" -", p, file=sys.stderr)
        return 1

    print(
        "GREEN: every documented command passes on a clean checkout, and the documented "
        "commands surface both a seeded broken test and a seeded lint violation in the "
        "reference engine -- pytest and ruff genuinely run"
    )
    return 0


# --- self-test ---------------------------------------------------------------------------------------


def _cases(
    engine_dir: str, elsewhere_dir: str, pytest_bin: str, ruff_bin: str
) -> list[tuple[str, str, int]]:
    """Fixture (name, CONTRIBUTING.md text, expected exit), built against a fast scratch
    ``engine_dir`` (a tiny real project, not the full 472-test reference engine) so self-test runs
    in seconds while still exercising the real ``pytest``/``ruff`` binaries -- no ``uv run``
    project resolution needed since the scratch project carries no lockfile of its own."""
    real = (
        f"```bash\ncd {engine_dir} && {pytest_bin} -q\n```\n\n"
        f"```bash\ncd {engine_dir} && {ruff_bin} check .\n```\n"
    )
    short_circuit = (
        f"```bash\ntrue || (cd {engine_dir} && {pytest_bin} -q)\n```\n\n"
        f"```bash\ncd {engine_dir} && {ruff_bin} check .\n```\n"
    )
    named_only = '```bash\necho "run pytest and ruff yourself"\n```\n'
    wrong_scope = (
        f"```bash\ncd {elsewhere_dir} && {pytest_bin} -q\n```\n"  # not engine_dir
    )
    no_run_setup_plus_real = (
        "```bash no-run needs network access\nuv sync && pnpm install\n```\n\n" + real
    )
    return [
        ("real-commands-pass", real, 0),
        ("short-circuit-is-red", short_circuit, 1),
        ("named-only-is-red", named_only, 1),
        ("wrong-scope-is-red", wrong_scope, 1),
        ("no-run-setup-block-is-skipped", no_run_setup_plus_real, 0),
    ]


def self_test() -> int:
    venv_bin = ROOT / ENGINE_REL / ".venv" / "bin"
    pytest_bin, ruff_bin = venv_bin / "pytest", venv_bin / "ruff"
    if not (pytest_bin.exists() and ruff_bin.exists()):
        print(
            f"SETUP: {venv_bin} has no pytest/ruff -- run `uv sync --frozen` in {ENGINE_REL} first",
            file=sys.stderr,
        )
        return 3

    ok = True
    with tempfile.TemporaryDirectory(prefix="agentce-contributing-selftest-") as tmp:
        engine_dir = Path(tmp) / "scratch-engine"
        (engine_dir / "tests").mkdir(parents=True)
        (engine_dir / "agentce").mkdir(parents=True)
        (engine_dir / "tests" / "test_ok.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
        elsewhere_dir = Path(tmp) / "elsewhere"
        elsewhere_dir.mkdir()
        cases = _cases(
            str(engine_dir), str(elsewhere_dir), str(pytest_bin), str(ruff_bin)
        )
        for name, text, want_exit in cases:
            fixture = Path(tmp) / f"CONTRIBUTING-{name}.md"
            fixture.write_text(text, encoding="utf-8")
            got = check(ROOT, contributing=fixture, engine=engine_dir)
            good = got == want_exit
            print(f"self-test {name}: {'ok' if good else 'FAIL'}")
            if not good:
                ok = False
                print(f"  wanted exit {want_exit}, got {got}", file=sys.stderr)
    print("contributing_doctest_check self-test:", "ok" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    return check(ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
