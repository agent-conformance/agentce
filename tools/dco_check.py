#!/usr/bin/env python3
"""dco_check - offline Developer Certificate of Origin validator for the external-contributor lane.

The required check any pull request's commits must pass: every commit in the range carries at least
one ``Signed-off-by: Name <email>`` trailer whose email exactly matches the *author's own* commit
email (case-insensitive). A missing sign-off, or a sign-off whose email names someone other than the
author -- even when the display name matches, which is the common case for a bot whose author address
differs from its sign-off address -- fails the check. This is the standard, open-source DCO rule; it
makes no reference to any particular contributor's identity, so it is the same check for the
maintainer, the agent, and any outside contributor or bot.

Uses only the standard library, so CI runs it with a bare Python and no install step. No network, no
learned component; the guard reads only the commit range it is given.

    dco_check.py --self-test        prove a matching sign-off is accepted and a bad one is refused
    dco_check.py --diff <BASE>      check every commit in <BASE>..HEAD (CI use)
    dco_check.py --range <SPEC>     check every commit in an arbitrary `git log` revision range
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FIELD_SEP = "\x1f"
REC_SEP = "\x1e"

SIGNOFF_RE = re.compile(r"^signed-off-by:\s*.*<([^>]+)>\s*$", re.IGNORECASE)

KEY_MISSING = "dco.missing_signoff"
KEY_MISMATCH = "dco.signoff_does_not_match_author"


def _git(args: list[str], cwd: str | Path | None = None) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout if r.returncode == 0 else ""


def _commits(revspec: str, cwd: str | Path | None = None) -> list[dict[str, str]]:
    fmt = FIELD_SEP.join(["%H", "%ae", "%B"]) + REC_SEP
    out = _git(["log", revspec, f"--format={fmt}"], cwd=cwd)
    commits = []
    for rec in out.split(REC_SEP):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split(FIELD_SEP)
        if len(parts) < 3:
            continue
        commits.append(
            {"sha": parts[0], "author_email": parts[1].lower(), "body": parts[2]}
        )
    return commits


def _signoff_emails(body: str) -> list[str]:
    out = []
    for line in body.splitlines():
        m = SIGNOFF_RE.match(line.strip())
        if m:
            out.append(m.group(1).strip().lower())
    return out


def violations(commits: list[dict[str, str]]) -> list[str]:
    """Return one 'dco.<key> <sha>: <reason>' string per offending commit; empty means clean."""
    out = []
    for c in commits:
        sha = c["sha"][:12]
        ae = c["author_email"]
        signoffs = _signoff_emails(c["body"])
        if not signoffs:
            out.append(
                f"{KEY_MISSING} {sha}: no Signed-off-by trailer (run `git commit -s`)"
            )
        elif ae not in signoffs:
            out.append(
                f"{KEY_MISMATCH} {sha}: Signed-off-by {signoffs[0]} does not match the "
                f"author's own email {ae}"
            )
    return out


def _report(bad: list[str], scope: str) -> int:
    if not bad:
        print(f"DCO OK: every commit in {scope} carries a matching Signed-off-by")
        return 0
    print(f"DCO FAILED over {scope}:", file=sys.stderr)
    for line in bad:
        print(f"  {line}", file=sys.stderr)
    return 1


def self_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _git(["init", "-q", "-b", "main"], cwd=repo)
        _git(["config", "user.name", "sandbox"], cwd=repo)
        _git(["config", "user.email", "sandbox@example.test"], cwd=repo)

        def commit(msg: str, name: str, email: str) -> None:
            env = dict(os.environ)
            env.update(
                GIT_AUTHOR_NAME=name,
                GIT_AUTHOR_EMAIL=email,
                GIT_COMMITTER_NAME=name,
                GIT_COMMITTER_EMAIL=email,
            )
            subprocess.run(
                ["git", "commit", "-q", "--allow-empty", "-m", msg],
                cwd=str(repo),
                env=env,
                check=True,
            )

        commit("root", "Ada Lovelace", "ada@example.test")
        base = _git(["rev-parse", "HEAD"], cwd=repo).strip()

        commit(
            "feat: a real change\n\nSigned-off-by: Ada Lovelace <ada@example.test>",
            "Ada Lovelace",
            "ada@example.test",
        )
        good = violations(_commits(f"{base}..HEAD", cwd=repo))
        assert not good, f"a correctly signed-off commit must be accepted, got {good}"

        _git(["reset", "-q", "--hard", base], cwd=repo)
        commit("feat: no sign-off at all", "Ada Lovelace", "ada@example.test")
        missing = violations(_commits(f"{base}..HEAD", cwd=repo))
        assert len(missing) == 1 and missing[0].startswith(KEY_MISSING), missing

        _git(["reset", "-q", "--hard", base], cwd=repo)
        commit(
            "feat: signed off by someone else\n\nSigned-off-by: Mallory <mallory@example.test>",
            "Ada Lovelace",
            "ada@example.test",
        )
        mismatch = violations(_commits(f"{base}..HEAD", cwd=repo))
        assert len(mismatch) == 1 and mismatch[0].startswith(KEY_MISMATCH), mismatch

        _git(["reset", "-q", "--hard", base], cwd=repo)
        commit(
            "feat: sign-off shares the author's name but not their email\n\n"
            "Signed-off-by: Ada Lovelace <impostor@example.test>",
            "Ada Lovelace",
            "ada@example.test",
        )
        pinned = violations(_commits(f"{base}..HEAD", cwd=repo))
        assert len(pinned) == 1 and pinned[0].startswith(KEY_MISMATCH), (
            "the match must be pinned to the email, not the display name",
            pinned,
        )

    print(
        "DCO-CHECK SELF-TEST PASSED (matching sign-off accepted; missing/mismatched refused, "
        "email-pinned not name-pinned)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    if "--diff" in argv:
        i = argv.index("--diff")
        base = argv[i + 1] if i + 1 < len(argv) else ""
        if not base:
            print("dco_check.py --diff requires a base commit", file=sys.stderr)
            return 2
        return _report(violations(_commits(f"{base}..HEAD")), f"{base}..HEAD")
    if "--range" in argv:
        i = argv.index("--range")
        spec = argv[i + 1] if i + 1 < len(argv) else ""
        if not spec:
            print("dco_check.py --range requires a revision range", file=sys.stderr)
            return 2
        return _report(violations(_commits(spec)), spec)

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
