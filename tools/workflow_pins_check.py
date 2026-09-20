"""Repository check: every GitHub Action a workflow uses is pinned to a full commit SHA.

A tag or branch (``@v4``, ``@release/v1``) can be moved to different code after review, so
``.github/workflows/*.yml`` and ``.github/actions/**/action.yml`` may only reference an action by its
40-hex commit id. Local actions (``./path``) and image digests (``docker://...@sha256:...``) are exempt.

Usage:
    workflow_pins_check.py              check the repository (exit 1 on any unpinned action)
    workflow_pins_check.py --self-test  prove the check discriminates on planted bad input
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USES = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>[^\s#]+)")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
DIGEST = re.compile(r"^docker://\S+@sha256:[0-9a-f]{64}$")


def unpinned(name: str, text: str) -> list[str]:
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        match = USES.match(line)
        if match is None:
            continue
        ref = match.group("ref").strip("'\"")
        if ref.startswith("./") or PINNED.match(ref) or DIGEST.match(ref):
            continue
        problems.append(f"{name}:{number}: '{ref}' is not pinned to a commit SHA")
    return problems


def violations(root: Path) -> list[str]:
    files = sorted((root / ".github").rglob("*.yml")) + sorted(
        (root / ".github").rglob("*.yaml")
    )
    problems: list[str] = []
    for path in files:
        problems += unpinned(
            str(path.relative_to(root)), path.read_text(encoding="utf-8")
        )
    return problems


def self_test() -> int:
    sha = "11d5960a326750d5838078e36cf38b85af677262"
    cases = {
        f"      - uses: actions/checkout@{sha} # v4\n": 0,
        f"        uses: github/codeql-action/init@{sha}\n": 0,
        "      - uses: ./.github/actions/local\n": 0,
        "      - uses: actions/checkout@v4\n": 1,
        "        uses: pypa/gh-action-pypi-publish@release/v1\n": 1,
        "      - uses: actions/checkout@11d5960 # short sha\n": 1,
        f"      - uses: actions/checkout@{sha[:-1]}g\n": 1,
        "      - uses: actions/checkout\n": 1,
        "      - uses: 'actions/setup-node@v4'\n": 1,
        "      - run: echo uses actions/checkout@v4\n": 0,
    }
    failures = [
        f"{text.strip()!r}: expected {want}, found {len(unpinned('t', text))}"
        for text, want in cases.items()
        if len(unpinned("t", text)) != want
    ]
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(f"workflow_pins_check self-test: {len(cases)} cases discriminate")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--self-test"]:
        return self_test()
    problems = violations(ROOT)
    for problem in problems:
        print(f"VIOLATION: {problem}", file=sys.stderr)
    if not problems:
        print("workflow pins: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
