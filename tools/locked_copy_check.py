#!/usr/bin/env python3
"""Repository check: the landing page and the README carry the approved product copy word for word.

Reads the *built* site (``website/dist``), not the templates that produce it. On the landing page the
approved tagline, headline and subline must be the visible eyebrow, heading and lead paragraph of the
first section, in that order. Nothing in the built site, in any file or tag attribute, may carry the wording
they replaced. In the README the three strings must follow the title directly, before the status line.
Stdlib only.

    locked_copy_check.py [--dist website/dist] [--readme README.md]   check the built site and the README
    locked_copy_check.py --self-test                                   show the check fails on old or altered copy
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

#: What stays beside the copy, unchanged: the disclaimer and the quickstart command.
KEPT = ("A conformant result is not a certification.", "agentce quickstart --out ./out")

#: The wording the approved copy replaced; it must not come back anywhere in the built site or the README.
RETIRED = (
    "The open conformance standard for AI agents",
    "Assess any AI agent against the same standard.",
    "an engine that turns evidence into a verdict anyone can reproduce",
)

#: The landing page's hero elements, in reading order, and the approved string each carries.
HERO = (("eyebrow", TAGLINE), ("h1", HEADLINE), ("lede", SUBLINE))

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}
_TEXT_FILES = {".html", ".txt", ".json", ".xml", ".md", ".webmanifest"}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_hidden(attrs: dict[str, str | None]) -> bool:
    style = (attrs.get("style") or "").replace(" ", "").lower()
    return (
        "hidden" in attrs
        or attrs.get("aria-hidden") == "true"
        or "display:none" in style
        or "visibility:hidden" in style
    )


class _Hero(HTMLParser):
    """Finds the hero's eyebrow, ``h1`` and lead paragraph: visible, inside the page's first ``section``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []
        self._stack: list[tuple[str, bool]] = []  # (tag, hidden) for every open element
        self._sections = 0
        self._in_first_section = False
        self._role: str | None = None
        self._role_depth = 0
        self.section_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            return
        table = dict(attrs)
        hidden = _is_hidden(table)
        if tag == "section":
            self._sections += 1
            if self._sections == 1:
                self._in_first_section = True
        visible = not hidden and not any(h for _, h in self._stack)
        self._stack.append((tag, hidden))
        if self._role is not None or not self._in_first_section or not visible:
            return
        classes = (table.get("class") or "").split()
        wanted = {role for role, _ in HERO} - {r for r, _ in self.found}
        role = "h1" if tag == "h1" else next((c for c in classes if c in wanted), None)
        if role in wanted:
            self._role, self._role_depth = role, len(self._stack)
            self.found.append((role, ""))

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or not self._stack:
            return
        while self._stack and self._stack[-1][0] != tag:
            self._stack.pop()
        if not self._stack:
            return
        if self._role is not None and len(self._stack) == self._role_depth:
            self._role = None
        self._stack.pop()
        if (
            tag == "section"
            and self._sections == 1
            and not any(t == "section" for t, _ in self._stack)
        ):
            self._in_first_section = False

    def handle_data(self, data: str) -> None:
        if self._in_first_section and not any(h for _, h in self._stack):
            self.section_text += data
        if self._role is not None:
            role, text = self.found[-1]
            self.found[-1] = (role, text + data)


def check_landing(html: str) -> list[str]:
    parser = _Hero()
    parser.feed(html)
    found = [(role, _squash(text)) for role, text in parser.found]
    expected = [(role, text) for role, text in HERO]
    problems = []
    if found != expected:
        problems.append(
            f"landing page hero: expected {expected!r} (visible, in the first section, in this order), found {found!r}"
        )
    shown = _squash(parser.section_text)
    problems += [
        f"landing page hero lost {text!r}" for text in KEPT if text not in shown
    ]
    return problems


def check_retired(name: str, text: str) -> list[str]:
    squashed = _squash(text)
    return [f"{name} still carries {old!r}" for old in RETIRED if old in squashed]


def check_dist(dist: Path) -> list[str]:
    index = dist / "index.html"
    if not index.is_file():
        return [f"{index} not found; build the site first (pnpm build in website/)"]
    problems = check_landing(index.read_text(encoding="utf-8"))
    for path in sorted(dist.rglob("*")):
        if path.is_file() and path.suffix.lower() in _TEXT_FILES:
            problems += check_retired(
                str(path.relative_to(dist)),
                path.read_text(encoding="utf-8", errors="replace"),
            )
    return problems


def check_readme(text: str) -> list[str]:
    _, _, below_title = text.replace("**", "").partition(
        "\n"
    )  # the first line is the title
    after_title = _squash(below_title)
    problems = []
    if not after_title.startswith(TAGLINE):
        problems.append(
            "README does not open, directly under its title, with the tagline"
        )
    elif not after_title[len(TAGLINE) :].strip().startswith(HEADLINE):
        problems.append("README's headline does not follow its tagline")
    elif (
        not after_title[len(TAGLINE) + len(HEADLINE) + 1 :].strip().startswith(SUBLINE)
    ):
        problems.append("README's subline does not follow its headline")
    problems += check_retired("README", text)
    for line in ("**Status:**", "**License:**"):
        if line not in text:
            problems.append(f"README lost its {line} line")
    return problems


def self_test() -> list[str]:
    def page(
        eyebrow: str = '<p class="eyebrow">{t}</p>',
        h1: str = "<h1>{h}</h1>",
        lede: str = '<p class="lede">{s}</p>',
    ) -> str:
        body = (eyebrow + h1 + lede + f"<p>{KEPT[0]}</p><pre>$ {KEPT[1]}</pre>").format(
            t=TAGLINE, h=HEADLINE, s=SUBLINE.replace("can't", "can&#39;t")
        )
        return f"<html><head></head><body><section>{body}<br></section><section><h2>x</h2></section></body></html>"

    def readme(
        order: tuple[str, ...] = (TAGLINE, HEADLINE, SUBLINE),
        status_first: bool = False,
    ) -> str:
        copy = "\n\n".join(order)
        tail = "**Status:** x\n\n**License:** y\n"
        return (
            f"# (AgentCE)\n\n{tail}\n{copy}\n"
            if status_first
            else f"# (AgentCE)\n\n{copy}\n\n{tail}"
        )

    cases: dict[str, tuple[list[str], bool]] = {
        "the approved page passes": (check_landing(page()), False),
        "a lost disclaimer fails": (check_landing(page().replace(KEPT[0], "")), True),
        "a lost quickstart command fails": (
            check_landing(page().replace(KEPT[1], "")),
            True,
        ),
        "the retired headline fails": (
            check_landing(
                page(h1="<h1>Assess any AI agent against the same standard.</h1>")
            ),
            True,
        ),
        "an edited subline fails": (
            check_landing(page().replace("skill", "plugin")),
            True,
        ),
        "a hidden eyebrow fails": (
            check_landing(page(eyebrow='<p class="eyebrow" hidden>{t}</p>')),
            True,
        ),
        "a copy in a hidden parent fails": (
            check_landing(
                page(lede='<div style="display: none"><p class="lede">{s}</p></div>')
            ),
            True,
        ),
        "the copy outside the first section fails": (
            check_landing(f"<section><h2>x</h2></section>{page()}"),
            True,
        ),
        "the copy in the wrong order fails": (
            check_landing(
                page(eyebrow="<h1>{h}</h1>", h1='<p class="eyebrow">{t}</p>')
            ),
            True,
        ),
        "retired copy in a tag attribute fails": (
            check_retired(
                "index.html", f'<meta name="description" content="{RETIRED[1]}">'
            ),
            True,
        ),
        "the approved README passes": (check_readme(readme()), False),
        "a README without the subline fails": (
            check_readme(readme((TAGLINE, HEADLINE))),
            True,
        ),
        "a README with the copy out of order fails": (
            check_readme(readme((HEADLINE, TAGLINE, SUBLINE))),
            True,
        ),
        "a README with the copy under the status line fails": (
            check_readme(readme(status_first=True)),
            True,
        ),
    }
    return [
        name
        for name, (problems, should_fail) in cases.items()
        if bool(problems) != should_fail
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--dist", type=Path, default=Path("website/dist"))
    ap.add_argument("--readme", type=Path, default=Path("README.md"))
    ap.add_argument("--self-test", action="store_true")
    ns = ap.parse_args(argv)
    if ns.self_test:
        wrong = self_test()
        for name in wrong:
            print(f"SELF-TEST FAIL: {name}")
        if not wrong:
            print("locked_copy_check self-test: 14 cases discriminate")
        return 1 if wrong else 0
    problems = check_dist(ns.dist) + check_readme(ns.readme.read_text(encoding="utf-8"))
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print("locked copy: the landing page and the README carry the approved copy")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
