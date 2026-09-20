"""Repository check: governance text does not claim structure the project does not have, and the
fresh-machine proof workflow gates every change.

Two checks, stdlib only (no network, no learned component):

* ``governance``: every heading-delimited section of every ``governance/**/*.md`` that names a working
  group, committee, steering body, council, or plural "maintainers" must, in that same section, carry an
  aspirational label (planned, not yet, aspirational, ...). A label elsewhere in the file does not
  excuse a claim in a different section.
* ``workflow``: the top-level ``on:`` block of ``.github/workflows/quickstart.yml`` (read by
  indentation, so a comment mentioning ``push`` cannot satisfy it) declares ``push`` or
  ``pull_request``, so the fresh-machine proof runs on every change and not only on manual dispatch.

Usage:
    governance_check.py              check the repository (exit 1 on any violation)
    governance_check.py --self-test  prove each check discriminates on planted bad input
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

OVERCLAIM = re.compile(
    r"\bworking groups?\b|\bcommittees?\b|\bsteering\b|\bcouncils?\b|\bmaintainers\b",
    re.I,
)
LABEL = re.compile(
    r"aspirational|planned|not yet|once the project|does not yet exist", re.I
)
GATING_TRIGGERS = frozenset({"push", "pull_request"})
WORKFLOW = Path(".github/workflows/quickstart.yml")


def _sections(text: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s", line) and current:
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def governance_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted((root / "governance").rglob("*.md")):
        for section in _sections(path.read_text(encoding="utf-8")):
            if OVERCLAIM.search(section) and not LABEL.search(section):
                heading = section.splitlines()[0][:80]
                violations.append(
                    f"{path.relative_to(root)}: unlabelled governance claim near '{heading}'"
                )
    return violations


def workflow_violations(root: Path) -> list[str]:
    path = root / WORKFLOW
    if not path.is_file():
        return [f"{WORKFLOW}: missing"]
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if re.match(r"^on:\s*(#.*)?$", line)), None
    )
    if start is None:
        return [f"{WORKFLOW}: no top-level 'on:' block"]
    triggers: set[str] = set()
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\S", line):
            break
        match = re.match(r"^  ([A-Za-z_]+):", line)
        if match:
            triggers.add(match.group(1))
    if not GATING_TRIGGERS & triggers:
        return [
            f"{WORKFLOW}: triggers {sorted(triggers) or ['(none)']} include neither push nor pull_request"
        ]
    return []


def check(root: Path) -> list[str]:
    return governance_violations(root) + workflow_violations(root)


_GOOD_GOVERNANCE = "# Governance\n\nOne maintainer decides.\n\n## Working groups (planned)\n\nNone exist yet.\n"
_GOOD_WORKFLOW = (
    "name: q\n\non:\n  push:\n    branches: [main]\n  workflow_dispatch:\n\njobs: {}\n"
)


def _tree(governance: str, workflow: str) -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "governance").mkdir()
    (root / "governance" / "README.md").write_text(governance, encoding="utf-8")
    (root / WORKFLOW).parent.mkdir(parents=True)
    (root / WORKFLOW).write_text(workflow, encoding="utf-8")
    return tmp


def self_test() -> int:
    cases: list[tuple[str, str, str, int, str]] = [
        ("good", _GOOD_GOVERNANCE, _GOOD_WORKFLOW, 0, ""),
        (
            "unlabelled group",
            "# G\n\n## Working groups\n\nEach working group owns an area.\n",
            _GOOD_WORKFLOW,
            1,
            "unlabelled",
        ),
        (
            "label in a different section",
            "# G\n\nMore catalogs are planned.\n\n## Working groups\n\nEach working group owns an area.\n",
            _GOOD_WORKFLOW,
            1,
            "unlabelled",
        ),
        (
            "plural maintainers",
            "# S\n\nThe maintainers follow this process.\n",
            _GOOD_WORKFLOW,
            1,
            "unlabelled",
        ),
        (
            "dispatch only",
            _GOOD_GOVERNANCE,
            "name: q\n\non:\n  workflow_dispatch:\n",
            1,
            "neither push nor pull_request",
        ),
        (
            "comment mentions push",
            _GOOD_GOVERNANCE,
            "# runs on push: never\nname: q\n\non:\n  workflow_dispatch:\n",
            1,
            "neither push nor pull_request",
        ),
        (
            "push under another key",
            _GOOD_GOVERNANCE,
            "name: q\n\non:\n  workflow_dispatch:\n\njobs:\n  push:\n    runs-on: x\n",
            1,
            "neither push nor pull_request",
        ),
    ]
    failed = 0
    for name, governance, workflow, expected_exit, expected_text in cases:
        with _tree(governance, workflow) as tmp:
            problems = check(Path(tmp))
        got = 1 if problems else 0
        ok = got == expected_exit and all(expected_text in p for p in problems)
        print(
            f"{'ok  ' if ok else 'FAIL'} {name}: exit {got} (expected {expected_exit})"
        )
        failed += 0 if ok else 1
    if failed:
        print(f"GOVERNANCE SELF-TEST FAILED: {failed} case(s)", file=sys.stderr)
        return 1
    print("GOVERNANCE SELF-TEST OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    problems = check(ROOT)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"GOVERNANCE CHECK FAILED: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("GOVERNANCE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
