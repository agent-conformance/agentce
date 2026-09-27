#!/usr/bin/env python3
"""Repository check: the landing page and the README carry the approved product copy word for word.

Reads the *built* landing page (``website/dist/index.html``), not the template that produces it, and
compares what a reader sees: the eyebrow, the headline and the subline. It also checks the README's
opening. Every approved string must appear, and the wording it replaced must not. Stdlib only.

    locked_copy_check.py [--dist website/dist] [--readme README.md]   check the built site and the README
    locked_copy_check.py --self-test                                   prove the check fails on old or altered copy
"""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

TAGLINE = "Open, repeatable checks of AI agent behavior against AI rules and standards."
HEADLINE = "Check how your AI agents behave against the AI rules and standards you follow, then fix the gaps."
SUBLINE = (
    "Point AgentCE at the records you already keep. It shows where your agents meet the standard you follow, "
    "where they fall short, and where your records can't show. Your team gets reports written for different "
    "roles, and the AI assistant your developers use gets a skill for the fixes."
)

#: The wording the approved copy replaced; it must not come back.
RETIRED = (
    "The open conformance standard for AI agents",
    "Assess any AI agent against the same standard.",
    "an engine that turns evidence into a verdict anyone can reproduce",
)

#: The landing page's hero elements that must carry each approved string.
LANDING_ROLES = {"eyebrow": TAGLINE, "h1": HEADLINE, "lede": SUBLINE}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _Hero(HTMLParser):
    """Collects the text of the first element per role: ``h1`` and ``class="eyebrow"`` / ``"lede"``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: dict[str, str] = {}
        self._role: str | None = None
        self._tag = ""
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._role is not None:
            self._depth += 1
            return
        classes = (dict(attrs).get("class") or "").split()
        role = (
            "h1"
            if tag == "h1"
            else next((c for c in classes if c in LANDING_ROLES), None)
        )
        if role and role not in self.text:
            self._role, self._tag, self._depth = role, tag, 0
            self.text[role] = ""

    def handle_endtag(self, tag: str) -> None:
        if self._role is None:
            return
        if self._depth == 0 and tag == self._tag:
            self._role = None
        else:
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._role is not None:
            self.text[self._role] += data


def check_landing(html: str) -> list[str]:
    parser = _Hero()
    parser.feed(html)
    problems = []
    for role, expected in LANDING_ROLES.items():
        found = _squash(parser.text.get(role, ""))
        if found != expected:
            problems.append(
                f"landing page {role}: expected {expected!r}, found {found!r}"
            )
    visible = _squash(re.sub(r"<[^>]+>", " ", html))
    problems += [
        f"landing page still carries {old!r}" for old in RETIRED if old in visible
    ]
    return problems


def check_readme(text: str) -> list[str]:
    body = _squash(text)
    problems = [
        f"README is missing {label}: {expected!r}"
        for label, expected in (
            ("the tagline", TAGLINE),
            ("the headline", HEADLINE),
            ("the subline", SUBLINE),
        )
        if expected not in body
    ]
    problems += [f"README still carries {old!r}" for old in RETIRED if old in body]
    if not (
        body.find(TAGLINE) < body.find(HEADLINE) < body.find(SUBLINE)
        and body.find(SUBLINE) != -1
    ):
        problems.append("README states the tagline, headline and subline out of order")
    for line in ("**Status:**", "**License:**"):
        if line not in text:
            problems.append(f"README lost its {line} line")
    return problems


def self_test() -> list[str]:
    good_html = (
        f'<p class="eyebrow">{TAGLINE}</p><h1 id="t">{HEADLINE}</h1>'
        f'<p class="lede">\n  {SUBLINE.replace("can't", "can&#39;t")}\n</p>'
    )
    good_readme = f"# (AgentCE)\n\n**{TAGLINE}**\n\n**{HEADLINE}**\n\n{SUBLINE}\n\n**Status:** x\n\n**License:** y\n"
    cases = {
        "the approved page passes": (check_landing(good_html), False),
        "the retired headline fails": (
            check_landing(good_html.replace(HEADLINE, RETIRED[1])),
            True,
        ),
        "an edited subline fails": (
            check_landing(good_html.replace("skill", "plugin")),
            True,
        ),
        "a missing eyebrow fails": (
            check_landing(good_html.replace("eyebrow", "kicker")),
            True,
        ),
        "the approved README passes": (check_readme(good_readme), False),
        "a README without the subline fails": (
            check_readme(good_readme.replace(SUBLINE, "")),
            True,
        ),
        "a README with the copy out of order fails": (
            check_readme(
                good_readme.replace(TAGLINE, "X").replace(
                    "**X**", f"{HEADLINE}\n\n**{TAGLINE}**", 1
                )
            ),
            True,
        ),
    }
    return [
        name
        for name, (problems, should_fail) in cases.items()
        if bool(problems) != should_fail
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", type=Path, default=Path("website/dist"))
    ap.add_argument("--readme", type=Path, default=Path("README.md"))
    ap.add_argument("--self-test", action="store_true")
    ns = ap.parse_args(argv)
    if ns.self_test:
        wrong = self_test()
        for name in wrong:
            print(f"SELF-TEST FAIL: {name}")
        if not wrong:
            print("locked_copy_check self-test: 7 cases discriminate")
        return 1 if wrong else 0
    index = ns.dist / "index.html"
    if not index.is_file():
        print(f"FAIL: {index} not found; build the site first (pnpm build in website/)")
        return 1
    problems = check_landing(index.read_text(encoding="utf-8")) + check_readme(
        ns.readme.read_text(encoding="utf-8")
    )
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print("locked copy: the landing page and the README carry the approved copy")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
