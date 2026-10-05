#!/usr/bin/env python3
"""Repository check: the landing page and the README carry the approved product copy word for word.

Reads the *built* site (``website/dist``), not the templates that produce it. On the landing page the
approved tagline, headline and subline must be the visible eyebrow, heading and lead paragraph of the
first section, in that order. Nothing in the built site, in any file or tag attribute, may carry the wording
they replaced. In the README the three strings must follow the title directly, before the status line.
``--also`` sweeps any further plain-text file (an engine README, for example) for the same retired
wording, without the hero/README structural checks. Stdlib only.

    locked_copy_check.py [--dist website/dist] [--readme README.md] [--also PATH ...]
                                                                       check the built site, the README,
                                                                       and any extra files for retired wording
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

DISCLAIMER = "A conformant result is not a certification."
QUICKSTART = "$ agentce quickstart --out ./out"

#: The wording the approved copy replaced; it must not come back anywhere in the built site, the README,
#: or an extra ``--also`` file. "conforming engine" is the defined CP-1 term
#: (``governance/CONFORMANCE-PROGRAM.md``): no implementation meets it until a signed report is registered
#: and a release is tagged, so consumer-facing copy may not call an engine "conforming" (loophole L18.10).
RETIRED = (
    "The open conformance standard for AI agents",
    "Assess any AI agent against the same standard.",
    "an engine that turns evidence into a verdict anyone can reproduce",
    "conforming engine",
)

#: The landing page's hero elements, in reading order, and the approved string each carries.
HERO = (
    ("eyebrow", TAGLINE),
    ("h1", HEADLINE),
    ("lede", SUBLINE),
    ("disclaimer", DISCLAIMER),
    ("try-it-cmd", QUICKSTART),
)

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
#: Elements whose content a reader never sees, and the class names that hide an element from sight.
_UNSEEN_TAGS = {"template", "script", "style", "noscript"}
_UNSEEN_CLASSES = {"hidden", "sr-only", "visually-hidden"}
_TEXT_FILES = {".html", ".txt", ".json", ".xml", ".md", ".webmanifest"}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_hidden(attrs: dict[str, str | None]) -> bool:
    style = (attrs.get("style") or "").replace(" ", "").lower()
    return (
        "hidden" in attrs
        or bool(_UNSEEN_CLASSES & set((attrs.get("class") or "").lower().split()))
        or (attrs.get("aria-hidden") or "").lower() == "true"
        or "display:none" in style
        or "visibility:hidden" in style
    )


class _Hero(HTMLParser):
    """Finds the hero's eyebrow, ``h1`` and lead paragraph: visible, inside the page's first ``section``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []
        self._stack: list[tuple[str, bool]] = []  # (tag, hidden) for every open element
        self._first_section_depth: int | None = None
        self._sections = 0
        self._role: str | None = None
        self._role_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            return
        table = dict(attrs)
        hidden = _is_hidden(table) or tag in _UNSEEN_TAGS
        visible = not hidden and not any(h for _, h in self._stack)
        self._stack.append((tag, hidden))
        if tag == "section":
            self._sections += 1
            if self._sections == 1:
                self._first_section_depth = len(self._stack)
        in_first_section = (
            self._first_section_depth is not None
            and len(self._stack) >= self._first_section_depth
        )
        if self._role is not None or not in_first_section or not visible:
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
        if tag == "section" and len(self._stack) == self._first_section_depth:
            self._first_section_depth = (
                None  # closed: nothing after it is in the first section
            )
        self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._role is not None and not any(hidden for _, hidden in self._stack):
            role, text = self.found[-1]
            self.found[-1] = (role, text + data)


def check_landing(html: str) -> list[str]:
    parser = _Hero()
    parser.feed(html)
    found = [(role, _squash(text)) for role, text in parser.found]
    expected = list(HERO)
    if found == expected:
        return []
    return [
        f"landing page hero: expected {expected!r} (visible, in the first section, in this order), found {found!r}"
    ]


def check_retired(name: str, text: str) -> list[str]:
    lowered = _squash(text).lower()
    return [
        f"{name} still carries {old!r}" for old in RETIRED if old.lower() in lowered
    ]


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
    parts = {
        "eyebrow": '<p class="eyebrow">{t}</p>',
        "h1": "<h1>{h}</h1>",
        "lede": '<p class="lede">{s}</p>',
        "disclaimer": '<p class="disclaimer">{d}</p>',
        "cmd": '<pre class="try-it-cmd"><code>{q}</code></pre>',
    }

    def page(**over: str) -> str:
        body = "".join({**parts, **over}.values()).format(
            t=TAGLINE,
            h=HEADLINE,
            s=SUBLINE.replace("can't", "can&#39;t"),
            d=DISCLAIMER,
            q=QUICKSTART,
        )
        return f"<html><body><section>{body}<br></section><section><h2>x</h2></section></body></html>"

    def readme(
        order: tuple[str, ...] = (TAGLINE, HEADLINE, SUBLINE),
        status_first: bool = False,
    ) -> str:
        copy = "\n\n".join(order)
        tail = "**Status:** x\n\n**License:** y\n"
        if status_first:
            return f"# (AgentCE)\n\n{tail}\n{copy}\n"
        return f"# (AgentCE)\n\n{copy}\n\n{tail}"

    def hidden(role: str, wrapper: str) -> str:
        return wrapper.format(parts[role])

    cases: dict[str, tuple[list[str], bool]] = {
        "the approved page passes": (check_landing(page()), False),
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
        "a screen-reader-only eyebrow fails": (
            check_landing(page(eyebrow='<p class="eyebrow sr-only">{t}</p>')),
            True,
        ),
        "an eyebrow in a template fails": (
            check_landing(page(eyebrow=hidden("eyebrow", "<template>{}</template>"))),
            True,
        ),
        "a disclaimer in a script fails": (
            check_landing(page(disclaimer=hidden("disclaimer", "<script>{}</script>"))),
            True,
        ),
        "a copy in a hidden parent fails": (
            check_landing(
                page(lede=hidden("lede", '<div style="display: none">{}</div>'))
            ),
            True,
        ),
        "a hidden span inside the headline fails": (
            check_landing(page(h1="<h1><span hidden>{h}</span></h1>")),
            True,
        ),
        "a screen-reader-only span inside the eyebrow fails": (
            check_landing(
                page(eyebrow='<p class="eyebrow"><span class="SR-only">{t}</span></p>')
            ),
            True,
        ),
        "a template inside the disclaimer fails": (
            check_landing(
                page(disclaimer='<p class="disclaimer"><template>{d}</template></p>')
            ),
            True,
        ),
        "a script inside the lede fails": (
            check_landing(page(lede='<p class="lede"><script>{s}</script></p>')),
            True,
        ),
        "a changed disclaimer fails": (
            check_landing(
                page(
                    disclaimer='<p class="disclaimer">A conformant result is a certification.</p>'
                )
            ),
            True,
        ),
        "a changed quickstart command fails": (
            check_landing(page().replace("./out", "./elsewhere")),
            True,
        ),
        "the copy outside the first section fails": (
            check_landing(f"<section><h2>x</h2></section>{page()}"),
            True,
        ),
        "a section nested in the hero does not end it": (
            check_landing(
                page(eyebrow="<section><b>x</b></section>" + parts["eyebrow"]).replace(
                    "<br></section>", '<br></section><p class="lede">late</p>', 1
                )
            ),
            False,
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
        "an extra file calling an engine 'conforming' fails": (
            check_retired("engines/go/README.md", "proven by three conforming engines"),
            True,
        ),
        "a capitalised 'Conforming Engines' still fails": (
            check_retired("engines/README.md", "Our Conforming Engines ship today."),
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
    ap.add_argument(
        "--also",
        type=Path,
        nargs="*",
        default=(),
        help="extra plain-text files to sweep for retired wording (no structural checks)",
    )
    ap.add_argument("--self-test", action="store_true")
    ns = ap.parse_args(argv)
    if ns.self_test:
        wrong = self_test()
        for name in wrong:
            print(f"SELF-TEST FAIL: {name}")
        if not wrong:
            print("locked_copy_check self-test: 24 cases discriminate")
        return 1 if wrong else 0
    problems = check_dist(ns.dist) + check_readme(ns.readme.read_text(encoding="utf-8"))
    for extra in ns.also:
        problems += check_retired(str(extra), extra.read_text(encoding="utf-8"))
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print("locked copy: the landing page and the README carry the approved copy")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
