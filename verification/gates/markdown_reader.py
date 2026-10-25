"""Read a Markdown file into CommonMark blocks with their source line numbers (18.211).

The gates that hold copy rules over Markdown (VG-INGEST-SUPPORT-MATRIX, VG-UX-ONE-QUESTION, VG-UX-COPY)
read it here, so a claim the site renders cannot escape a gate that reads lines differently from the
renderer. The blocks come from markdown-it-py's CommonMark parser with the GFM table rule turned on
(the site renders GFM tables); this module adds the line bookkeeping the gates report with, and one
correction: markdown-it-py ends a table at a line Python's ``str.strip()`` empties, so a line holding
only a form feed ended it while the site draws that line as an empty row and keeps the table going.
Here a table, like everything else, ends only at a line of spaces and tabs (CommonMark 4.9).

What that means for a gate, each held by a case in ``--self-test``:

- lines end at LF, CR and CRLF only (CommonMark 0.31.2 section 2.1); form feed, U+000B, U+001C to
  U+001E, U+0085, U+2028 and U+2029 are ordinary characters, unlike ``str.splitlines()``;
- a blank line holds only spaces and tabs, so a line holding a form feed ends no paragraph or table;
- a paragraph is its first line plus its soft-wrapped and lazy continuation lines (4.8, 5.1);
- block quotes and list items keep their continuation, and their markers are not part of the text;
- a fenced code block closes only on a run of the same character at least as long as its opener (4.5);
- a code span never crosses a paragraph break (6.1).

YAML front matter (a first line ``---`` up to the next ``---`` line) is not CommonMark; the site strips
it, so ``blocks`` reads it as blank lines and ``front_matter`` hands its lines to the caller.

    markdown_reader.py --self-test   run the CommonMark 0.31.2 and GFM 0.29 table spec examples through
                                     blocks(), and the reader's own cases
"""

from __future__ import annotations

import argparse
import html
import importlib
import os
import re
import sys
import types
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

try:
    from markdown_it import MarkdownIt
    from markdown_it.token import Token

    # the module, not the function markdown_it.rules_block re-exports under the same name
    _table_rule = importlib.import_module("markdown_it.rules_block.table")
except ImportError:  # run directly outside the tools environment: main() re-runs in it
    MarkdownIt = None  # type: ignore[assignment,misc]

_RERUN = "AGENTCE_MARKDOWN_READER_RERUN"
LINE_ENDING = re.compile(r"\r\n|\r|\n")
_HTML_HIDDEN = re.compile(r"<!--.*?-->|<[^>]*>", re.DOTALL)


class _CommonMarkLine(str):
    """A source line whose ``strip()`` trims spaces and tabs only, CommonMark's whitespace for blank
    lines (4.9); markdown-it-py's table rule calls ``strip()`` to find the line that ends a table."""

    def strip(self, chars: str | None = None) -> str:
        return str.strip(self, " \t" if chars is None else chars)


def _parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark").enable("table")
    rule = _table_rule.table
    get_line = _table_rule.getLine
    table = types.FunctionType(
        rule.__code__,
        {**rule.__globals__, "getLine": lambda s, n: _CommonMarkLine(get_line(s, n))},
        rule.__name__,
        rule.__defaults__,
        rule.__closure__,
    )
    parser.block.ruler.at("table", table, {"alt": ["paragraph", "reference"]})
    return parser


_PARSER = _parser() if MarkdownIt is not None else None


@dataclass(frozen=True)
class Line:
    number: int  # 1-based source line
    text: str


@dataclass(frozen=True)
class Block:
    """One leaf block. ``lines`` is the block's own text by source line: a paragraph's or heading's inline
    source (container markers and leading indentation removed), a code or HTML block's content, a table's
    raw rows. ``visible`` is what a reader sees of it: code spans, inline HTML, comments and autolinks
    blanked, link destinations dropped, emphasis markers, escapes and entities resolved; for a table, one
    line per row, its cells joined by " | ". ``rows`` holds a table's cells (header first) as inline
    source. ``source`` is the raw source lines the block spans, container markers included."""

    kind: str  # paragraph, heading, fence, indented_code, html, table, rule
    start: int
    end: int
    containers: tuple[str, ...]  # "blockquote" or "list_item", outermost first
    level: int = 0  # a heading's level
    lines: tuple[Line, ...] = ()
    visible: tuple[Line, ...] = ()
    rows: tuple[tuple[Line, tuple[str, ...]], ...] = ()
    source: tuple[Line, ...] = field(default=(), repr=False)

    def visible_text(self) -> str:
        return " ".join(line.text for line in self.visible)


def split_lines(text: str) -> list[str]:
    """The source lines, split at LF, CR and CRLF only."""
    lines = LINE_ENDING.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def front_matter(text: str) -> list[Line]:
    """The YAML front matter's lines, both ``---`` fences included; empty when the file has none."""
    lines = split_lines(text)
    if not lines or lines[0].rstrip() != "---":
        return []
    end = next(
        (i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), len(lines) - 1
    )
    return [Line(n, t) for n, t in enumerate(lines[: end + 1], start=1)]


def _inline_visible(token: Token, first: int) -> list[Line]:
    """A paragraph's, heading's or cell's visible text by source line."""
    raw = token.content
    texts = [""]
    cursor = 0  # where the walk is in ``raw``, kept to count the line breaks inside code spans and HTML
    autolink = False
    for child in token.children or []:
        kind = child.type
        if kind in ("softbreak", "hardbreak"):
            texts.append("")
            cursor = raw.find("\n", cursor) + 1
        elif autolink:
            autolink = kind != "link_close"
        elif kind == "link_open" and child.markup == "autolink":
            autolink = True
            texts[-1] += " "
        elif kind in ("text", "image"):  # an image's content is its alt text
            texts[-1] += child.content
        elif kind == "code_inline":
            run = re.compile(rf"(?<!`){re.escape(child.markup)}(?!`)")
            opener = run.search(raw, cursor)
            closer = run.search(raw, opener.end()) if opener else None
            texts[-1] += " "
            if opener and closer:
                texts.extend([""] * raw.count("\n", opener.start(), closer.end()))
                cursor = closer.end()
        elif kind == "html_inline":
            at = raw.find(child.content, cursor)
            texts[-1] += " "
            texts.extend([""] * child.content.count("\n"))
            if at >= 0:
                cursor = at + len(child.content)
    return [Line(first + i, text) for i, text in enumerate(texts)]


def blocks(text: str) -> list[Block]:
    """The file's leaf blocks in source order; front matter is read as blank lines."""
    assert _PARSER is not None, (
        "markdown_reader needs markdown-it-py (uv run --project tools)"
    )
    source = split_lines(text)
    body = [""] * len(front_matter(text)) + source[len(front_matter(text)) :]
    tokens = _PARSER.parse("\n".join(body) + "\n")
    out: list[Block] = []
    containers: list[str] = []

    def first_line(token: Token) -> int:
        return (token.map or (0, 0))[0] + 1

    def leaf(kind: str, token: Token, **extra: object) -> None:
        begin, end = token.map or (0, 0)
        raw = tuple(Line(n + 1, body[n]) for n in range(begin, end))
        out.append(
            Block(
                kind,
                begin + 1,
                max(begin + 1, end),
                tuple(containers),
                source=raw,
                **extra,  # type: ignore[arg-type]
            )
        )

    def content_lines(first: int, content: str) -> tuple[Line, ...]:
        return tuple(
            Line(first + i, part) for i, part in enumerate(content.split("\n")[:-1])
        )

    index = 0
    while index < len(tokens):
        token = tokens[index]
        kind = token.type
        if kind in ("blockquote_open", "list_item_open"):
            containers.append(kind.removesuffix("_open"))
        elif kind in ("blockquote_close", "list_item_close"):
            containers.pop()
        elif kind in ("paragraph_open", "heading_open"):
            inline, first = tokens[index + 1], first_line(token)
            leaf(
                kind.removesuffix("_open"),
                token,
                level=int(token.tag[1:]) if kind == "heading_open" else 0,
                lines=tuple(
                    Line(first + i, p) for i, p in enumerate(inline.content.split("\n"))
                ),
                visible=tuple(_inline_visible(inline, first)),
            )
            index += 2
        elif kind == "fence":
            leaf(
                "fence",
                token,
                lines=content_lines(first_line(token) + 1, token.content),
            )
        elif kind == "code_block":
            lines = content_lines(first_line(token), token.content)
            leaf("indented_code", token, lines=lines, visible=lines)
        elif kind == "html_block":
            first = first_line(token)
            hidden = _HTML_HIDDEN.sub(
                lambda m: " " + "\n" * m.group().count("\n"), token.content
            )
            leaf(
                "html",
                token,
                lines=content_lines(first, token.content),
                visible=tuple(
                    Line(line.number, html.unescape(line.text))
                    for line in content_lines(first, hidden)
                ),
            )
        elif kind == "table_open":
            rows: list[tuple[Line, tuple[str, ...]]] = []
            seen: list[Line] = []
            close = index
            while tokens[close].type != "table_close":
                if tokens[close].type == "tr_open":
                    number = first_line(tokens[close])
                    cells = []
                    while tokens[close].type != "tr_close":
                        if tokens[close].type == "inline":
                            cells.append(tokens[close])
                        close += 1
                    rows.append(
                        (
                            Line(number, body[number - 1]),
                            tuple(c.content for c in cells),
                        )
                    )
                    seen.append(
                        Line(
                            number,
                            " | ".join(
                                " ".join(v.text for v in _inline_visible(c, number))
                                for c in cells
                            ),
                        )
                    )
                close += 1
            leaf(
                "table",
                token,
                lines=tuple(r[0] for r in rows),
                visible=tuple(seen),
                rows=tuple(rows),
            )
            index = close
        elif kind == "hr":
            leaf("rule", token)
        index += 1
    return out


# --- self-test -----------------------------------------------------------------------------------------

# CommonMark 0.31.2 spec examples (https://spec.commonmark.org/0.31.2/, CC BY-SA 4.0), one or more per
# construct the reader's docstring lists, then the GFM 0.29 table examples 198 to 205
# (https://github.github.com/gfm/, CC BY-SA 4.0): (example number, section, markdown, expected HTML).
SPEC_EXAMPLES: tuple[tuple[int, str, str, str], ...] = (
    (62, "ATX headings", "# foo\n## foo\n### foo\n#### foo\n##### foo\n###### foo\n", "<h1>foo</h1>\n<h2>foo</h2>\n<h3>foo</h3>\n<h4>foo</h4>\n<h5>foo</h5>\n<h6>foo</h6>\n"),
    (80, "Setext headings", "Foo *bar*\n=========\n\nFoo *bar*\n---------\n", "<h1>Foo <em>bar</em></h1>\n<h2>Foo <em>bar</em></h2>\n"),
    (119, "Fenced code blocks", "```\n<\n >\n```\n", "<pre><code>&lt;\n &gt;\n</code></pre>\n"),
    (122, "Fenced code blocks", "```\naaa\n~~~\n```\n", "<pre><code>aaa\n~~~\n</code></pre>\n"),
    (123, "Fenced code blocks", "~~~\naaa\n```\n~~~\n", "<pre><code>aaa\n```\n</code></pre>\n"),
    (124, "Fenced code blocks", "````\naaa\n```\n``````\n", "<pre><code>aaa\n```\n</code></pre>\n"),
    (125, "Fenced code blocks", "~~~~\naaa\n~~~\n~~~~\n", "<pre><code>aaa\n~~~\n</code></pre>\n"),
    (126, "Fenced code blocks", "```\n", "<pre><code></code></pre>\n"),
    (128, "Fenced code blocks", "> ```\n> aaa\n\nbbb\n", "<blockquote>\n<pre><code>aaa\n</code></pre>\n</blockquote>\n<p>bbb</p>\n"),
    (219, "Paragraphs", "aaa\n\nbbb\n", "<p>aaa</p>\n<p>bbb</p>\n"),
    (220, "Paragraphs", "aaa\nbbb\n\nccc\nddd\n", "<p>aaa\nbbb</p>\n<p>ccc\nddd</p>\n"),
    (221, "Paragraphs", "aaa\n\n\nbbb\n", "<p>aaa</p>\n<p>bbb</p>\n"),
    (222, "Paragraphs", "  aaa\n bbb\n", "<p>aaa\nbbb</p>\n"),
    (227, "Blank lines", "  \n\naaa\n  \n\n# aaa\n\n  \n", "<p>aaa</p>\n<h1>aaa</h1>\n"),
    (228, "Block quotes", "> # Foo\n> bar\n> baz\n", "<blockquote>\n<h1>Foo</h1>\n<p>bar\nbaz</p>\n</blockquote>\n"),
    (232, "Block quotes", "> # Foo\n> bar\nbaz\n", "<blockquote>\n<h1>Foo</h1>\n<p>bar\nbaz</p>\n</blockquote>\n"),
    (233, "Block quotes", "> bar\nbaz\n> foo\n", "<blockquote>\n<p>bar\nbaz\nfoo</p>\n</blockquote>\n"),
    (234, "Block quotes", "> foo\n---\n", "<blockquote>\n<p>foo</p>\n</blockquote>\n<hr />\n"),
    (235, "Block quotes", "> - foo\n- bar\n", "<blockquote>\n<ul>\n<li>foo</li>\n</ul>\n</blockquote>\n<ul>\n<li>bar</li>\n</ul>\n"),
    (236, "Block quotes", ">     foo\n    bar\n", "<blockquote>\n<pre><code>foo\n</code></pre>\n</blockquote>\n<pre><code>bar\n</code></pre>\n"),
    (237, "Block quotes", "> ```\nfoo\n```\n", "<blockquote>\n<pre><code></code></pre>\n</blockquote>\n<p>foo</p>\n<pre><code></code></pre>\n"),
    (238, "Block quotes", "> foo\n    - bar\n", "<blockquote>\n<p>foo\n- bar</p>\n</blockquote>\n"),
    (253, "List items", "A paragraph\nwith two lines.\n\n    indented code\n\n> A block quote.\n", "<p>A paragraph\nwith two lines.</p>\n<pre><code>indented code\n</code></pre>\n<blockquote>\n<p>A block quote.</p>\n</blockquote>\n"),
    (254, "List items", "1.  A paragraph\n    with two lines.\n\n        indented code\n\n    > A block quote.\n", "<ol>\n<li>\n<p>A paragraph\nwith two lines.</p>\n<pre><code>indented code\n</code></pre>\n<blockquote>\n<p>A block quote.</p>\n</blockquote>\n</li>\n</ol>\n"),
    (256, "List items", "- one\n\n  two\n", "<ul>\n<li>\n<p>one</p>\n<p>two</p>\n</li>\n</ul>\n"),
    (286, "List items", " 1.  A paragraph\n     with two lines.\n\n         indented code\n\n     > A block quote.\n", "<ol>\n<li>\n<p>A paragraph\nwith two lines.</p>\n<pre><code>indented code\n</code></pre>\n<blockquote>\n<p>A block quote.</p>\n</blockquote>\n</li>\n</ol>\n"),
    (290, "List items", "  1.  A paragraph\nwith two lines.\n\n          indented code\n\n      > A block quote.\n", "<ol>\n<li>\n<p>A paragraph\nwith two lines.</p>\n<pre><code>indented code\n</code></pre>\n<blockquote>\n<p>A block quote.</p>\n</blockquote>\n</li>\n</ol>\n"),
    (291, "List items", "  1.  A paragraph\n    with two lines.\n", "<ol>\n<li>A paragraph\nwith two lines.</li>\n</ol>\n"),
    (328, "Code spans", "`foo`\n", "<p><code>foo</code></p>\n"),
    (335, "Code spans", "``\nfoo\nbar  \nbaz\n``\n", "<p><code>foo bar   baz</code></p>\n"),
    (338, "Code spans", "`foo\\`bar`\n", "<p><code>foo\\</code>bar`</p>\n"),
    (341, "Code spans", "*foo`*`\n", "<p>*foo<code>*</code></p>\n"),
    (345, "Code spans", "`<https://foo.bar.`baz>`\n", "<p><code>&lt;https://foo.bar.</code>baz&gt;`</p>\n"),
    (347, "Code spans", "```foo``\n", "<p>```foo``</p>\n"),
    (348, "Code spans", "`foo\n", "<p>`foo</p>\n"),
    (349, "Code spans", "`foo``bar``\n", "<p>`foo<code>bar</code></p>\n"),
    (648, "Soft line breaks", "foo\nbaz\n", "<p>foo\nbaz</p>\n"),
    (198, "GFM tables", "| foo | bar |\n| --- | --- |\n| baz | bim |\n", "<table>\n<thead>\n<tr>\n<th>foo</th>\n<th>bar</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td>baz</td>\n<td>bim</td>\n</tr>\n</tbody>\n</table>\n"),
    (199, "GFM tables", "| abc | defghi |\n:-: | -----------:\nbar | baz\n", '<table>\n<thead>\n<tr>\n<th align="center">abc</th>\n<th align="right">defghi</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td align="center">bar</td>\n<td align="right">baz</td>\n</tr>\n</tbody>\n</table>\n'),
    (200, "GFM tables", "| f\\|oo  |\n| ------ |\n| b `\\|` az |\n| b **\\|** im |\n", "<table>\n<thead>\n<tr>\n<th>f|oo</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td>b <code>|</code> az</td>\n</tr>\n<tr>\n<td>b <strong>|</strong> im</td>\n</tr>\n</tbody>\n</table>\n"),
    (201, "GFM tables", "| abc | def |\n| --- | --- |\n| bar | baz |\n> bar\n", "<table>\n<thead>\n<tr>\n<th>abc</th>\n<th>def</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td>bar</td>\n<td>baz</td>\n</tr>\n</tbody>\n</table>\n<blockquote>\n<p>bar</p>\n</blockquote>\n"),
    (202, "GFM tables", "| abc | def |\n| --- | --- |\n| bar | baz |\nbar\n\nbar\n", "<table>\n<thead>\n<tr>\n<th>abc</th>\n<th>def</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td>bar</td>\n<td>baz</td>\n</tr>\n<tr>\n<td>bar</td>\n<td></td>\n</tr>\n</tbody>\n</table>\n<p>bar</p>\n"),
    (203, "GFM tables", "| abc | def |\n| --- |\n| bar |\n", "<p>| abc | def |\n| --- |\n| bar |</p>\n"),
    (204, "GFM tables", "| abc | def |\n| --- | --- |\n| bar |\n| bar | baz | boo |\n", "<table>\n<thead>\n<tr>\n<th>abc</th>\n<th>def</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td>bar</td>\n<td></td>\n</tr>\n<tr>\n<td>bar</td>\n<td>baz</td>\n</tr>\n</tbody>\n</table>\n"),
    (205, "GFM tables", "| abc | def |\n| --- | --- |\n", "<table>\n<thead>\n<tr>\n<th>abc</th>\n<th>def</th>\n</tr>\n</thead>\n</table>\n"),
)  # fmt: skip

# Characters CommonMark reads as ordinary that str.splitlines() ends a line at (section 2.1).
NOT_LINE_ENDINGS = "\x0b\x0c\x1c\x1d\x1e\x85  "
_LEAVES = {"p": "paragraph", "pre": "code", "hr": "rule", "table": "table"} | {
    f"h{n}": "heading" for n in range(1, 7)
}
_CONTAINERS = {"blockquote": "blockquote", "li": "list_item"}

Shape = tuple[str, tuple[str, ...], object]


class _ExpectedShape(HTMLParser):
    """The leaf blocks an example's expected HTML holds, each with its containers and, for a heading its
    level, for a table its cells per row; a tight list item's text is its paragraph."""

    def __init__(self) -> None:
        super().__init__()
        self.shape: list[Shape] = []
        self.stack: list[str] = []
        self.leaf: str | None = None
        self.rows: list[int] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _CONTAINERS:
            self.stack.append(_CONTAINERS[tag])
        elif tag in _LEAVES and self.leaf is None:
            self.leaf = tag
            extra: object = int(tag[1]) if _LEAVES[tag] == "heading" else ()
            self.shape.append((_LEAVES[tag], tuple(self.stack), extra))
            self.rows = []
        elif tag == "tr":
            self.rows.append(0)
        elif tag in ("td", "th"):
            self.rows[-1] += 1
        if tag == "hr":
            self.leaf = None

    def handle_endtag(self, tag: str) -> None:
        if tag in _CONTAINERS:
            self.stack.pop()
            self.leaf = None if self.leaf == "li" else self.leaf
        elif tag == self.leaf:
            if tag == "table":
                kind, containers, _ = self.shape[-1]
                self.shape[-1] = (kind, containers, tuple(self.rows))
            self.leaf = None

    def handle_data(self, data: str) -> None:
        if data.strip() and self.leaf is None and self.stack[-1:] == ["list_item"]:
            self.leaf = "li"
            self.shape.append(("paragraph", tuple(self.stack), ()))


def _expected_shape(expected_html: str) -> list[Shape]:
    parser = _ExpectedShape()
    parser.feed(expected_html)
    return parser.shape


def _shape(text: str) -> list[Shape]:
    out: list[Shape] = []
    for b in blocks(text):
        kind = "code" if b.kind in ("fence", "indented_code") else b.kind
        extra: object = b.level if b.kind == "heading" else ()
        if b.kind == "table":
            extra = tuple(len(cells) for _, cells in b.rows)
        out.append((kind, b.containers, extra))
    return out


ReaderCase = tuple[str, str, list[tuple[str, int, int, tuple[str, ...], str]]]


def _reader_cases() -> list[ReaderCase]:
    table = "| a | b |\n|---|---|\n| c | d |\n"
    cases: list[ReaderCase] = []
    cases += [
        (
            f"U+{ord(ch):04X} inside a paragraph is not a line ending, and a line of it is not blank",
            f"one\n{ch}\ntwo{ch}three\n",
            [("paragraph", 1, 3, (), f"one {ch} two{ch}three")],
        )
        for ch in NOT_LINE_ENDINGS
    ]
    cases += [
        (
            f"a line of U+{ord(ch):04X} does not end a table",
            f"{table}{ch}\n| e | f |\n",
            [("table", 1, 5, (), "a | b c | d  |  e | f")],
        )
        for ch in NOT_LINE_ENDINGS
    ]
    cases += [
        ("CR and CRLF end lines", "a\rb\r\n\r\nc", [("paragraph", 1, 2, (), "a b"), ("paragraph", 4, 4, (), "c")]),
        ("soft wrap joins a paragraph", "**Supported** (3): a,\nb\n\nnext", [("paragraph", 1, 2, (), "Supported (3): a, b"), ("paragraph", 4, 4, (), "next")]),
        ("lazy line in a block quote", "> a\nb\n", [("paragraph", 1, 2, ("blockquote",), "a b")]),
        ("list item keeps its continuation", "- a\n  b\nlazy\n", [("paragraph", 1, 3, ("list_item",), "a b lazy")]),
        ("nested containers", "> - a\n>   b\n", [("paragraph", 1, 2, ("blockquote", "list_item"), "a b")]),
        ("a shorter fence does not close", "````\nx\n```\nhidden\n````\nshown\n", [("fence", 1, 5, (), ""), ("paragraph", 6, 6, (), "shown")]),
        ("indented code is read as text", "    records prove it\n", [("indented_code", 1, 1, (), "records prove it")]),
        ("a code span stops at a paragraph break", "a ` tick\n\nrecords prove it\n\nanother ` tick", [("paragraph", 1, 1, (), "a ` tick"), ("paragraph", 3, 3, (), "records prove it"), ("paragraph", 5, 5, (), "another ` tick")]),
        ("code spans, HTML and autolinks blanked; link and alt text kept", "a `b` <!-- c --> [d](https://e) <https://f> &mdash; *g* ![h](i.png)", [("paragraph", 1, 1, (), "a     d   \u2014 g h")]),
        ("front matter reads as blank lines", "---\ntitle: x\n---\nbody\n", [("paragraph", 4, 4, (), "body")]),
        ("heading and setext heading", "# h\n\nt\n===\n", [("heading", 1, 1, (), "h"), ("heading", 3, 4, (), "t")]),
        ("table rows keep their own lines", table, [("table", 1, 3, (), "a | b c | d")]),
        ("table cells blank code spans", "| `x` | **y** |\n|---|---|\n", [("table", 1, 2, (), "  | y")]),
        ("HTML block keeps its text, not its tags", "<div>\nsee <b>x</b>\n</div>\n", [("html", 1, 3, (), "  see  x   ")]),
    ]  # fmt: skip
    return cases


def self_test() -> int:
    failed: list[str] = []
    for number, section, markdown, want in SPEC_EXAMPLES:
        if _shape(markdown) != _expected_shape(want):
            failed.append(
                f"spec example {number} ({section}): blocks() reads {_shape(markdown)!r}, "
                f"the spec {_expected_shape(want)!r}"
            )
    for name, text, expected in _reader_cases():
        got = [
            (b.kind, b.start, b.end, b.containers, b.visible_text())
            for b in blocks(text)
        ]
        if got != expected:
            failed.append(f"{name}: {got!r} != {expected!r}")
    line_cases = [
        ("a code span spanning lines keeps later lines numbered", "a `b\nc` d\ne prove", 3, "e prove"),
        ("a comment spanning lines keeps later lines numbered", "a <!-- b\nc --> d\ne prove", 3, "e prove"),
        ("a table row keeps its line", "| a | b |\n|---|---|\n| c | d |\n", 3, "c | d"),
    ]  # fmt: skip
    for name, text, number, line_text in line_cases:
        visible = {line.number: line.text for b in blocks(text) for line in b.visible}
        if visible.get(number) != line_text:
            failed.append(f"{name}: {visible!r}")
    rows = blocks("| a | b |\n|---|---|\n| c | d |\n")[0].rows
    if [(line.number, cells) for line, cells in rows] != [
        (1, ("a", "b")),
        (3, ("c", "d")),
    ]:
        failed.append(f"table rows: {rows!r}")
    split = split_lines("a\x0cb c\r\nd\re\n")
    if split != ["a\x0cb c", "d", "e"]:
        failed.append(f"split_lines: {split!r}")
    if [ln.number for ln in front_matter("---\na: 1\n---\nx")] != [
        1,
        2,
        3,
    ] or front_matter("x\n---\n"):
        failed.append("front_matter")
    total = len(SPEC_EXAMPLES) + len(_reader_cases()) + len(line_cases) + 3
    for line in failed:
        print(f"self-test FAIL {line}", file=sys.stderr)
    print(f"markdown-reader self-test: {total - len(failed)}/{total} cases pass")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="markdown_reader.py", allow_abbrev=False)
    parser.add_argument("--self-test", action="store_true", required=True)
    parser.parse_args(argv)
    if _PARSER is None:
        if os.environ.get(_RERUN):
            print(
                "markdown_reader.no_parser: markdown-it-py is not installed",
                file=sys.stderr,
            )
            return 2
        tools = Path(__file__).resolve().parents[2] / "tools"
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"} | {
            _RERUN: "1"
        }
        command = [
            "uv",
            "run",
            "--project",
            str(tools),
            "--frozen",
            "python3",
            __file__,
            *argv,
        ]
        os.execvpe("uv", command, env)
    return self_test()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
