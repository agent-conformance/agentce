#!/usr/bin/env python3
"""docs_doctest_check - run every shell command the public docs site shows, against the real CLI.

A command printed on the documentation site is a promise that a reader can paste it and get the result
the page describes. This tool keeps that promise by executing the command: it walks every Markdown page
(``.md`` and ``.mdx``) under ``website/src/content/docs/docs``, extracts each fenced ``bash``, ``sh``, or
``console`` block, and runs it for real, each block from its own fresh scratch directory. The scratch
directory links to the repository's ``engines/``, ``corpus/``, and ``spec/`` trees so that a relative
``--project engines/python`` or ``corpus/quickstart/...`` path resolves exactly as it does in a reader's
checkout. The documented commands write only into the scratch directory; the one side effect on the tree
is the engine's own gitignored virtual environment that ``uv run`` creates. A block that exits non-zero
fails the run.

A block that genuinely cannot run in a harness (it needs an operator's own agent, a production key, a live
service) opts out through the one documented marker: ``no-run`` after the language on the opening fence,
followed by the reason it cannot run. The opt-out is counted and printed, never silent, and a ``no-run``
with no reason fails the run. A shell-looking fence the tool cannot classify (``bash title="x"``, ``zsh``)
also fails the run instead of being skipped, and so does an untagged (or ``text``) fence whose lines
start like a command, so a block cannot dodge execution by an unusual or missing language tag.
The run also fails if fewer than ``MIN_BLOCKS`` blocks ran, so deleting a broken example cannot turn the
check green.

    docs_doctest_check.py              run every block on the site (the invocation the CI job uses)
    docs_doctest_check.py --pages DIR  run every block under DIR instead of the site's pages, with the
                                       same rules (how a seeded fault is shown to be caught)
    docs_doctest_check.py --self-test  prove the checker discriminates: good fixtures pass, each bad
                                       fixture fails for exactly the rule it targets, and the command
                                       shape that was once documented (a bundle that does not exist)
                                       fails against the real CLI

Uses only the standard library and needs ``uv`` on PATH to run the documented ``uv run`` commands. No
network, no learned component.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = Path("website/src/content/docs/docs")
LINKED_TREES = ("engines", "corpus", "spec")
MIN_BLOCKS = 4
BLOCK_TIMEOUT_S = 150
MIN_REASON_CHARS = 8
# Per-page floor (finding #41 rework): the corpus-wide MIN_BLOCKS alone lets a contributor strip every
# block from one page (e.g. ci-integration.md) as long as enough blocks remain elsewhere. This manifest
# names every page that currently documents at least one runnable command and requires it to keep at
# least that many; a page can never be silenced by deleting its examples. Regenerate it (see
# `_generate_min_blocks_manifest` below) whenever a page's documented commands legitimately change.
MIN_BLOCKS_MANIFEST_PATH = ROOT / "tools" / "docs_doctest_min_blocks.json"

RUNNABLE = {"bash", "sh", "console"}
# Every language tag that means "a shell session" to a reader. A fence with one of these that is not a
# plain runnable block or a reasoned `no-run` is an error, never a silent skip.
SHELL_LIKE = RUNNABLE | {
    "shell",
    "zsh",
    "shell-session",
    "sh-session",
    "bash-session",
    "console-session",
    "shellscript",
    "shellsession",
    "terminal",
    "powershell",
    "pwsh",
    "cmd",
}
# Fences that carry no language: prose output, unless a line starts like a command a reader would paste.
NEUTRAL = {"", "text", "txt", "plaintext"}
COMMAND_STARTS = (
    "$ ",
    "cd ",
    "export ",
    "curl ",
    "brew ",
    "agentce ",
    "uv ",
    "uvx ",
    "pipx ",
    "pip ",
    "pnpm ",
    "npm ",
    "npx ",
    "docker ",
    "java ",
    "gradle ",
    "./gradlew ",
    "python ",
    "python3 ",
)
FENCE_OPEN = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})\s*(?P<info>[^`]*)$")


@dataclass(frozen=True)
class Block:
    page: str
    line: int
    lang: str
    script: str
    no_run_reason: (
        str | None
    )  # None: runnable; a string: opted out with that reason (may be empty)
    error: str | None = None  # set when a shell-looking fence cannot be classified


def extract(page: str, text: str) -> list[Block]:
    """Return every shell-looking fenced block in ``text``, classified. Non-shell fences are ignored."""
    lines = text.splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        opened_at = i + 1
        fence = m.group("fence")
        indent = m.group("indent")
        info = m.group("info").split()
        body: list[str] = []
        i += 1
        while i < len(lines) and not re.match(
            r"^[ \t]*" + fence[0] + "{" + str(len(fence)) + r",}\s*$", lines[i]
        ):
            body.append(
                lines[i][len(indent) :] if lines[i].startswith(indent) else lines[i]
            )
            i += 1
        i += 1
        lang = info[0].lower() if info else ""
        if lang in NEUTRAL:
            found = [ln for ln in body if ln.lstrip().startswith(COMMAND_STARTS)]
            if found:
                msg = f"a {lang or 'untagged'} fence shows a command ({found[0].strip()[:60]!r}); tag it bash so it is run, or `bash no-run <reason>`"
                blocks.append(Block(page, opened_at, lang, "", None, msg))
            continue
        if lang not in SHELL_LIKE:
            continue
        rest = info[1:]
        script = "\n".join(body)
        if lang not in RUNNABLE:
            blocks.append(
                Block(
                    page,
                    opened_at,
                    lang,
                    script,
                    None,
                    f"fence language {lang!r} is not runnable; use bash, sh, or console",
                )
            )
        elif not rest:
            blocks.append(Block(page, opened_at, lang, script, None))
        elif rest[0] == "no-run":
            blocks.append(Block(page, opened_at, lang, script, " ".join(rest[1:])))
        else:
            blocks.append(
                Block(
                    page,
                    opened_at,
                    lang,
                    script,
                    None,
                    f"unrecognised fence info {' '.join(rest)!r}; only `no-run <reason>` may follow the language",
                )
            )
    return blocks


def _sandbox(repo: Path) -> str:
    work = tempfile.mkdtemp(prefix="agentce-doctest-")
    for name in LINKED_TREES:
        if (repo / name).is_dir():
            os.symlink(repo / name, Path(work) / name)
    return work


def run_block(block: Block, repo: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    try:
        proc = subprocess.run(
            ["bash", "-c", "set -e\n" + block.script],
            cwd=_sandbox(repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=BLOCK_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {BLOCK_TIMEOUT_S}s"
    return proc.returncode, (proc.stderr or proc.stdout)[-500:]


def _load_min_blocks_manifest(path: Path = MIN_BLOCKS_MANIFEST_PATH) -> dict[str, int]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def check(
    pages_dir: Path,
    repo: Path,
    min_blocks: int = MIN_BLOCKS,
    manifest: dict[str, int] | None = None,
) -> int:
    """Run every block under ``pages_dir``; return 0 iff all ran clean, enough of them ran overall, and
    every page named in the min-blocks manifest kept at least its required count (so deleting one page's
    examples cannot pass just because other pages still carry enough blocks). The manifest keys pages by
    path relative to ``pages_dir`` (e.g. ``ci-integration.md``), not to ``repo``, so it still matches when
    a contract check runs this against a throwaway copy of the docs tree rather than the original."""
    if manifest is None:
        manifest = _load_min_blocks_manifest()
    ran = skipped = 0
    page_counts: dict[str, int] = {}
    failures: list[str] = []
    for page in sorted([*pages_dir.rglob("*.md"), *pages_dir.rglob("*.mdx")]):
        rel = str(page.relative_to(repo)) if page.is_relative_to(repo) else str(page)
        page_key = str(page.relative_to(pages_dir))
        for block in extract(rel, page.read_text(encoding="utf-8")):
            where = f"{block.page}:{block.line}"
            if block.error:
                failures.append(f"{where}: {block.error}")
            elif block.no_run_reason is not None:
                skipped += 1
                page_counts[page_key] = page_counts.get(page_key, 0) + 1
                if len(block.no_run_reason) < MIN_REASON_CHARS:
                    failures.append(
                        f"{where}: `no-run` needs a reason of at least {MIN_REASON_CHARS} characters on the fence line"
                    )
                else:
                    print(
                        f"doctest: skip {where} ({block.no_run_reason})",
                        file=sys.stderr,
                    )
            else:
                ran += 1
                page_counts[page_key] = page_counts.get(page_key, 0) + 1
                code, tail = run_block(block, repo)
                if code != 0:
                    failures.append(f"{where}: exit {code}\n{tail}")
    print(
        f"doctest: ran {ran} skipped {skipped} failed {len(failures)}", file=sys.stderr
    )
    if ran < min_blocks:
        failures.append(
            f"only {ran} block(s) ran, expected at least {min_blocks}: a block was removed instead of fixed"
        )
    for page_name, need in sorted(manifest.items()):
        got = page_counts.get(page_name, 0)
        if got < need:
            failures.append(
                f"{page_name}: only {got} block(s) ran/skipped, expected at least {need}: "
                "this page's examples may have been deleted instead of fixed"
            )
    for failure in failures:
        print("FAIL", failure, file=sys.stderr)
    return 1 if failures else 0


# --- self-test ---------------------------------------------------------------------------------------

_FIX = "```"

# The `assess` command shape that used to be documented: a bundle directory that exists nowhere, with no
# profile. Run against the real CLI it must fail, proving the doc-test reaches the engine.
_ONCE_DOCUMENTED = f"{_FIX}bash\nuv run --project engines/python agentce assess --bundle ./bundle --out ./out\n{_FIX}\n"

_CASES: list[tuple[str, str, int, str, int]] = [
    # (name, page text, expected exit, substring expected on stderr, min_blocks)
    (
        "good-block-passes",
        f"{_FIX}bash\ntrue\n{_FIX}\n",
        0,
        "ran 1 skipped 0 failed 0",
        1,
    ),
    ("failing-block-fails", f"{_FIX}bash\nfalse\n{_FIX}\n", 1, "exit 1", 1),
    (
        "multi-line-block-stops-at-first-failure",
        f"{_FIX}sh\nfalse\ntrue\n{_FIX}\n",
        1,
        "exit 1",
        1,
    ),
    (
        "non-shell-block-ignored",
        f"{_FIX}python\nraise SystemExit(9)\n{_FIX}\n{_FIX}bash\ntrue\n{_FIX}\n",
        0,
        "ran 1",
        1,
    ),
    (
        "indented-block-is-found",
        f"- step\n\n  {_FIX}bash\n  false\n  {_FIX}\n",
        1,
        "exit 1",
        1,
    ),
    ("tilde-fence-is-found", "~~~bash\nfalse\n~~~\n", 1, "exit 1", 1),
    (
        "no-run-with-reason-is-skipped",
        f"{_FIX}bash no-run needs the operator's own agent\nfalse\n{_FIX}\n{_FIX}bash\ntrue\n{_FIX}\n",
        0,
        "skip",
        1,
    ),
    (
        "no-run-without-reason-fails",
        f"{_FIX}bash no-run\ntrue\n{_FIX}\n",
        1,
        "needs a reason",
        0,
    ),
    (
        "unknown-fence-info-fails",
        f'{_FIX}bash title="x"\ntrue\n{_FIX}\n',
        1,
        "unrecognised fence info",
        0,
    ),
    (
        "unrunnable-shell-language-fails",
        f"{_FIX}zsh\ntrue\n{_FIX}\n",
        1,
        "not runnable",
        0,
    ),
    (
        "untagged-command-fence-fails",
        f"{_FIX}\nuv run --project engines/python agentce assess\n{_FIX}\n",
        1,
        "shows a command",
        0,
    ),
    (
        "untagged-output-fence-is-ignored",
        f"{_FIX}\n25 conformant, 19 insufficient evidence\n{_FIX}\n{_FIX}bash\ntrue\n{_FIX}\n",
        0,
        "ran 1",
        1,
    ),
    (
        "mdx-page-is-scanned",
        f"{_FIX}bash\nfalse\n{_FIX}\n",
        1,
        "exit 1",
        1,
    ),
    (
        "removed-block-fails-the-floor",
        f"{_FIX}bash\ntrue\n{_FIX}\n",
        1,
        "expected at least 2",
        2,
    ),
    ("no-blocks-at-all-fails-the-floor", "Just prose.\n", 1, "expected at least 1", 1),
    (
        "once-documented-assess-fails-against-real-cli",
        _ONCE_DOCUMENTED,
        1,
        "input.bundle_not_a_directory",
        1,
    ),
]


def self_test() -> int:
    ok = True
    with tempfile.TemporaryDirectory(prefix="agentce-doctest-selftest-") as tmp:
        for name, text, want_exit, want_text, floor in _CASES:
            pages = Path(tmp) / name
            pages.mkdir()
            page = "page.mdx" if name.startswith("mdx-") else "page.md"
            (pages / page).write_text(text, encoding="utf-8")
            got, err = _capture(pages, floor)
            good = got == want_exit and want_text in err
            print(f"self-test {name}: {'ok' if good else 'FAIL'}")
            if not good:
                ok = False
                print(
                    f"  wanted exit {want_exit} with {want_text!r}; got exit {got}\n{err}",
                    file=sys.stderr,
                )

        # Per-page floor (#41 rework): two pages, each with its own runnable block, both below the
        # OVERALL floor's reach (the corpus-wide count stays comfortably above `min_blocks` even after
        # one page is wiped) -- only a PER-PAGE requirement can catch "every block stripped from one
        # page". First prove the intact tree passes, then prove wiping one named page fails it, by name.
        pp_dir = Path(tmp) / "per-page-floor"
        pp_dir.mkdir()
        kept, wiped = pp_dir / "kept.md", pp_dir / "wiped.md"
        kept.write_text(
            f"{_FIX}bash\ntrue\n{_FIX}\n{_FIX}bash\ntrue\n{_FIX}\n", encoding="utf-8"
        )
        wiped.write_text(f"{_FIX}bash\ntrue\n{_FIX}\n", encoding="utf-8")
        pp_manifest = {"kept.md": 1, "wiped.md": 1}
        got, err = _capture(pp_dir, 1, manifest=pp_manifest)
        good = got == 0
        print(
            f"self-test per-page-floor-intact-tree-passes: {'ok' if good else 'FAIL'}"
        )
        ok = ok and good

        wiped.write_text(
            "Just prose; the example was deleted, not fixed.\n", encoding="utf-8"
        )
        got, err = _capture(pp_dir, 1, manifest=pp_manifest)
        # The corpus-wide floor (1) is still met by kept.md alone -- only the per-page manifest entry
        # for wiped.md, by its exact name, can catch this.
        good = (
            got == 1 and "wiped.md" in err and "examples may have been deleted" in err
        )
        print(
            f"self-test per-page-floor-catches-single-page-wipe: {'ok' if good else 'FAIL'}"
        )
        if not good:
            ok = False
            print(f"  got exit {got}\n{err}", file=sys.stderr)

        # The real, checked-in manifest must name only pages that exist and still meet their own floor
        # today, and must not have quietly gone empty (a manifest with zero entries is not a floor).
        real_manifest = _load_min_blocks_manifest()
        if not real_manifest:
            ok = False
            print(
                "self-test real-manifest-non-empty: FAIL (no entries)", file=sys.stderr
            )
        else:
            print(f"self-test real-manifest-non-empty: ok ({len(real_manifest)} pages)")
        manifest_pages_ok = True
        for page, need in real_manifest.items():
            p = ROOT / PAGES / page
            if not p.exists():
                ok = manifest_pages_ok = False
                print(
                    f"self-test real-manifest-pages-meet-floor: FAIL ({page} missing)",
                    file=sys.stderr,
                )
                continue
            got_blocks = sum(
                1
                for b in extract(page, p.read_text(encoding="utf-8"))
                if b.error is None
            )
            if got_blocks < need:
                ok = manifest_pages_ok = False
                print(
                    f"self-test real-manifest-pages-meet-floor: FAIL ({page} has {got_blocks}, needs {need})",
                    file=sys.stderr,
                )
        if manifest_pages_ok:
            print("self-test real-manifest-pages-meet-floor: ok")

    print("docs_doctest_check self-test:", "ok" if ok else "FAILED")
    return 0 if ok else 1


def _capture(
    pages: Path, floor: int, manifest: dict[str, int] | None = None
) -> tuple[int, str]:
    import contextlib
    import io

    # Isolate the pre-existing single-synthetic-page fixtures from the real, checked-in min-blocks
    # manifest (they exercise unrelated rules and never intend to satisfy it): default to an EMPTY
    # manifest here rather than `check()`'s own real-manifest default, which only the dedicated
    # per-page-floor cases and the real-manifest self-checks below opt into explicitly.
    if manifest is None:
        manifest = {}
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        code = check(pages, ROOT, floor, manifest=manifest)
    return code, buf.getvalue()


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2 and argv[0] == "--pages":
        return check(Path(argv[1]).resolve(), ROOT)
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    return check(ROOT / PAGES, ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
