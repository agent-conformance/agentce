"""Repository check: a real Code of Conduct and real GitHub contribution templates exist.

``CODE_OF_CONDUCT.md`` must carry real Contributor-Covenant-shaped Commitment/Standards/Enforcement
content -- each section a substantive paragraph, never a bare heading -- and its Enforcement section
must never name a fabricated, plausible-looking contact: an email-shaped address is only accepted
alongside an honest pending/placeholder marker, never confirmed by an agent (that confirmation is a
maintainer action). A real, non-trivial GitHub issue template and pull-request template must both
exist.

Usage:
    code_of_conduct_check.py              check the repository (exit 1 on any violation)
    code_of_conduct_check.py --self-test  prove each rule discriminates on planted bad input
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MIN_SECTION_CHARS = 60
MIN_TEMPLATE_BYTES = 30

SECTION_PATTERNS = {
    # This repository's Code of Conduct titles its intro section "Our Commitment" rather than the
    # more common alternate wording some Contributor-Covenant-derived documents use; either framing
    # is a real, substantive intro section, and this checker only recognises this repository's own.
    "commitment": re.compile(r"^#{1,3}\s*.*commitment", re.IGNORECASE),
    "standards": re.compile(r"^#{1,3}\s*.*standards", re.IGNORECASE),
    "enforcement": re.compile(r"^#{1,3}\s*.*enforcement", re.IGNORECASE),
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PENDING_RE = re.compile(
    r"placeholder|pending|TBD|TODO|\[insert|not yet (confirmed|assigned)",
    re.IGNORECASE,
)


def _body_after(lines: list[str], heading_idx: int) -> str:
    body = []
    j = heading_idx + 1
    while j < len(lines) and not re.match(r"^#{1,3}\s", lines[j]):
        if lines[j].strip():
            body.append(lines[j].strip())
        j += 1
    return " ".join(body)


def _code_of_conduct_violations(root: Path) -> list[str]:
    problems: list[str] = []
    coc_path = root / "CODE_OF_CONDUCT.md"
    if not coc_path.exists():
        return ["CODE_OF_CONDUCT.md does not exist"]

    text = coc_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    section_bodies: dict[str, str] = {}
    missing = []
    for name, pattern in SECTION_PATTERNS.items():
        idx = next((i for i, ln in enumerate(lines) if pattern.match(ln)), None)
        body = _body_after(lines, idx) if idx is not None else ""
        section_bodies[name] = body
        if idx is None or len(body) < MIN_SECTION_CHARS:
            missing.append(name)
    if missing:
        problems.append(
            f"CODE_OF_CONDUCT.md missing a substantive (>= {MIN_SECTION_CHARS} chars body) "
            f"{missing} section -- a real Contributor-Covenant-style CoC needs real "
            f"Commitment/Standards/Enforcement content, not a bare heading or no heading at all"
        )

    enforcement_body = section_bodies.get("enforcement", "")
    email_match = EMAIL_RE.search(enforcement_body)
    if email_match and not PENDING_RE.search(enforcement_body):
        problems.append(
            f"CODE_OF_CONDUCT.md's Enforcement section names a concrete contact "
            f"({email_match.group(0)!r}) with no honest placeholder/pending marker -- do not "
            f"invent a real-looking contact; confirming a real one is a maintainer action"
        )
    return problems


def _template_violations(root: Path) -> list[str]:
    problems: list[str] = []

    issue_dir = root / ".github" / "ISSUE_TEMPLATE"
    issue_single = root / ".github" / "ISSUE_TEMPLATE.md"
    issue_files = []
    if issue_dir.is_dir():
        issue_files = [
            p
            for p in issue_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in (".md", ".yml", ".yaml")
            and p.stat().st_size > MIN_TEMPLATE_BYTES
        ]
    if (
        not issue_files
        and issue_single.is_file()
        and issue_single.stat().st_size > MIN_TEMPLATE_BYTES
    ):
        issue_files = [issue_single]
    if not issue_files:
        problems.append(
            "no non-trivial GitHub issue template found (.github/ISSUE_TEMPLATE/*.md|*.yml|"
            "*.yaml, or .github/ISSUE_TEMPLATE.md)"
        )

    pr_candidates = [
        root / ".github" / "pull_request_template.md",
        root / ".github" / "PULL_REQUEST_TEMPLATE.md",
        root / ".github" / "PULL_REQUEST_TEMPLATE",
    ]
    pr_ok = False
    for candidate in pr_candidates:
        if candidate.is_file() and candidate.stat().st_size > MIN_TEMPLATE_BYTES:
            pr_ok = True
            break
        if candidate.is_dir() and any(
            p.is_file() and p.stat().st_size > MIN_TEMPLATE_BYTES
            for p in candidate.iterdir()
        ):
            pr_ok = True
            break
    if not pr_ok:
        problems.append(
            "no non-trivial GitHub pull-request template found (.github/pull_request_template.md, "
            "PULL_REQUEST_TEMPLATE.md, or PULL_REQUEST_TEMPLATE/*)"
        )
    return problems


def violations(root: Path) -> list[str]:
    return _code_of_conduct_violations(root) + _template_violations(root)


def check(root: Path) -> int:
    problems = violations(root)
    if problems:
        print("RED:", file=sys.stderr)
        for p in problems:
            print(" -", p, file=sys.stderr)
        return 1
    print(
        "GREEN: CODE_OF_CONDUCT.md carries real Commitment/Standards/Enforcement content with no "
        "fabricated contact, a real issue template exists, and a real PR template exists"
    )
    return 0


# --- self-test ---------------------------------------------------------------------------------------

_GOOD_COC = """# Code of Conduct

## Our Commitment

We as maintainers and contributors commit to making participation in this project a harassment-free
experience for everyone, regardless of background or experience level.

## Our Standards

Examples of positive behavior include using welcoming language and giving constructive feedback.
Examples of unacceptable behavior include harassment and derogatory comments of any kind.

## Enforcement

Instances of unacceptable behavior may be reported once a real contact is confirmed and published
here; that confirmation is still pending.
"""

_BARE_HEADINGS_COC = "# Code of Conduct\n\n## Our Commitment\nBe nice.\n\n## Our Standards\nBe nice.\n\n## Enforcement\nBe nice.\n"

_UNFLAGGED_CONTACT_COC = """# Code of Conduct

## Our Commitment

We as maintainers and contributors commit to making participation in this project a harassment-free
experience for everyone, regardless of background or experience level.

## Our Standards

Examples of positive behavior include using welcoming language and giving constructive feedback.
Examples of unacceptable behavior include harassment and derogatory comments of any kind.

## Enforcement

Report incidents to conduct@agent-conformance.org, which is monitored by the maintainers at all
times.
"""


def _write_fixture(
    root: Path, coc_text: str | None, issue_template: bool, pr_template: bool
) -> None:
    if coc_text is not None:
        (root / "CODE_OF_CONDUCT.md").write_text(coc_text, encoding="utf-8")
    github = root / ".github"
    if issue_template:
        issue_dir = github / "ISSUE_TEMPLATE"
        issue_dir.mkdir(parents=True, exist_ok=True)
        (issue_dir / "bug_report.md").write_text(
            "---\nname: Bug\n---\n\n## What happened\n", encoding="utf-8"
        )
    if pr_template:
        github.mkdir(parents=True, exist_ok=True)
        (github / "pull_request_template.md").write_text(
            "## Summary\n\nDescribe the change.\n\n## Test plan\n\n- [ ] tests pass\n",
            encoding="utf-8",
        )


_CASES = [
    # (name, coc_text, issue_template, pr_template, want_exit)
    ("good-fixture-passes", _GOOD_COC, True, True, 0),
    ("missing-coc-fails", None, True, True, 1),
    ("bare-headings-fail", _BARE_HEADINGS_COC, True, True, 1),
    ("unflagged-contact-fails", _UNFLAGGED_CONTACT_COC, True, True, 1),
    ("missing-issue-template-fails", _GOOD_COC, False, True, 1),
    ("missing-pr-template-fails", _GOOD_COC, True, False, 1),
]


def self_test() -> int:
    ok = True
    for name, coc_text, issue_template, pr_template, want_exit in _CASES:
        with tempfile.TemporaryDirectory(prefix="agentce-coc-selftest-") as tmp:
            root = Path(tmp)
            _write_fixture(root, coc_text, issue_template, pr_template)
            got = check(root)
            good = got == want_exit
            print(f"self-test {name}: {'ok' if good else 'FAIL'}")
            if not good:
                ok = False
                print(f"  wanted exit {want_exit}, got {got}", file=sys.stderr)
    print("code_of_conduct_check self-test:", "ok" if ok else "FAILED")
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
