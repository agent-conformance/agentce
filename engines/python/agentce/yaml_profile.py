"""The YAML profile every YAML input is checked against before it is parsed (SPEC 8.7).

A small hand-written lexical pass, the same rules in all three engines (TypeScript ``yamlProfile.ts``, Java
``YamlProfile.java``), and not a port of any YAML library's scanner. It finds the first construct outside the profile
in document order and names its rule; the caller refuses the file under its role's existing ``input.*`` key. Repeated
keys need the parsed tree, so :func:`duplicate_key_line` checks them on the composed nodes. The rules and their edge
cases are pinned by the shared case table ``spec/rules/yaml-profile-cases.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

MAX_DEPTH = 100

# One hint per rule, the same words in every engine (the case table's ``hints`` holds the same text).
HINTS: dict[str, str] = {
    "anchor": "write the value out in full instead of using an anchor",
    "alias": "write the value out in full instead of using an alias",
    "merge_key": "write the merged keys out in full instead of using <<",
    "tag": "remove the tag; quote the value to keep it as text",
    "directive": "remove the directive line",
    "explicit_key": "write the key as a plain or quoted key followed by a colon",
    "second_document": "keep one document in the file and remove the extra --- or ... line",
    "tab": "replace the tab with spaces",
    "non_string_key": "quote the key to make it text",
    "duplicate_key": "write each key once",
    "yaml11_bool": "quote the value to keep it as text",
    "underscore_number": "quote the value to keep it as text, or write the number without _",
    "sexagesimal": "quote the value to keep it as text",
    "octal": "quote the value to keep it as text",
    "exponent_no_dot": "quote the value to keep it as text, or write the number with a dot (1.0e3)",
    "flow_scalar_indicator_start": "quote the value to keep it as text",
    "flow_line_not_indented": "indent the line further than the key the list or mapping belongs to",
    "depth_over_100": "flatten the nesting to at most 100 levels",
}

_YAML11_BOOL = re.compile(r"(?i)y|n|yes|no|on|off")
_UNDERSCORE_NUMBER = re.compile(
    r"[-+]?(0b[01_]+|0x[0-9a-fA-F_]+|0[0-7_]+|[0-9][0-9_]*(\.[0-9_]*)?([eE][-+][0-9]+)?|\.[0-9_]+([eE][-+][0-9]+)?)"
)
_SEXAGESIMAL = re.compile(r"[-+]?[0-9][0-9_]*(:[0-5]?[0-9])+(\.[0-9_]*)?")
_OCTAL = re.compile(r"[-+]?0[0-7]+|0o[0-7]+")
_EXPONENT_NO_DOT = re.compile(r"[-+]?[0-9]+[eE][-+]?[0-9]+")
# A plain key in this list does not read as a string (the written list of SPEC 8.7's "every mapping key is a string").
_NON_STRING_KEY = re.compile(
    r"|~|null|Null|NULL|true|True|TRUE|false|False|FALSE"
    r"|[-+]?(0|[1-9][0-9_]*)|[-+]?0b[01_]+|[-+]?0x[0-9a-fA-F_]+"
    r"|[-+]?[0-9][0-9_]*\.[0-9_]*([eE][-+][0-9]+)?|\.[0-9][0-9_]*([eE][-+][0-9]+)?"
    r"|[-+]?\.(inf|Inf|INF)|\.(nan|NaN|NAN)"
    r"|[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}([Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(\.[0-9]*)?([ \t]*(Z|[-+][0-9]{1,2}(:[0-9]{2})?))?"
)

_FLOW_INDICATORS = ",[]{}"


@dataclass(frozen=True)
class ProfileViolation:
    rule: str
    line: int

    def message(self) -> str:
        """The message tail every engine prints after naming the file, byte for byte."""
        return (
            f"is outside the YAML profile: {self.rule} at line {self.line} (SPEC 8.7)"
        )

    @property
    def hint(self) -> str:
        return HINTS[self.rule]


class _Violation(Exception):
    def __init__(self, rule: str, line: int) -> None:
        super().__init__(rule)
        self.found = ProfileViolation(rule, line)


def scalar_rule(text: str) -> str | None:
    """The rule a plain scalar's text breaks, or ``None``."""
    for rule, pattern in (
        ("yaml11_bool", _YAML11_BOOL),
        ("underscore_number", _UNDERSCORE_NUMBER),
        ("sexagesimal", _SEXAGESIMAL),
        ("octal", _OCTAL),
        ("exponent_no_dot", _EXPONENT_NO_DOT),
    ):
        if pattern.fullmatch(text) and (rule != "underscore_number" or "_" in text):
            return rule
    return None


def _space_or_end(line: str, i: int) -> bool:
    return i >= len(line) or line[i] in " \t"


class _Scanner:
    def __init__(self, text: str) -> None:
        if text.startswith("﻿"):
            text = text[1:]
        self.lines = re.split(r"\r\n|\r|\n", text)
        self.ln = 0
        self.content_seen = False
        self.doc_start_seen = False
        self.stack: list[
            tuple[int, str]
        ] = []  # open block collections: (column, "map" | "seq")
        self.flow: list[str] = []  # open flow collections
        self.flow_indent = (
            -1
        )  # the enclosing block's indentation when the outermost flow collection opened
        self.quote: str | None = None  # a quoted scalar left open at the end of a line
        self.block_scalar: int | None = (
            None  # the parent indentation while inside a block scalar body
        )
        self.pending: tuple[str, int, int] | None = (
            None  # a block plain scalar that may continue: text, line, owner
        )
        self.pending_multi = False

    def fail(self, rule: str, line: int | None = None) -> _Violation:
        return _Violation(rule, self.ln if line is None else line)

    def run(self) -> ProfileViolation | None:
        try:
            for index, line in enumerate(self.lines):
                self.ln = index + 1
                self._line(line)
            self._finish_pending()
        except _Violation as found:
            return found.found
        return None

    # -- depth ---------------------------------------------------------------------------------------------------

    def _check_depth(self) -> None:
        if len(self.stack) + len(self.flow) > MAX_DEPTH:
            raise self.fail("depth_over_100")

    def _block_entry(self, col: int, kind: str) -> None:
        if self.stack and self.stack[-1][0] == col:
            top = self.stack[-1][1]
            if top == kind:
                return
            if top == "seq" and kind == "map":
                self.stack.pop()  # an indentless sequence ends at the next key of its mapping
                if self.stack and self.stack[-1] == (col, "map"):
                    return
        self.stack.append((col, kind))
        self._check_depth()

    def _owner(self) -> int:
        return self.stack[-1][0] if self.stack else -1

    # -- plain scalars -------------------------------------------------------------------------------------------

    def _check_plain(self, text: str, line: int, is_key: bool) -> None:
        if text == "<<":
            raise self.fail("merge_key", line)
        rule = scalar_rule(text)
        if rule is not None:
            raise self.fail(rule, line)
        if is_key and _NON_STRING_KEY.fullmatch(text):
            raise self.fail("non_string_key", line)

    def _finish_pending(self) -> None:
        if self.pending is not None:
            text, line, _ = self.pending
            multi = self.pending_multi
            self.pending = None
            self.pending_multi = False
            if not multi:
                self._check_plain(text, line, False)

    # -- lines ---------------------------------------------------------------------------------------------------

    def _line(self, line: str) -> None:
        if line[:3] in ("---", "...") and _space_or_end(line, 3):
            if self.block_scalar is not None:
                self.block_scalar = None
            if (
                line[:3] == "---"
                and not self.content_seen
                and not self.doc_start_seen
                and self.quote is None
            ):
                self.doc_start_seen = True
                self._scan(line, 3)
                return
            self._finish_pending()
            raise self.fail("second_document")
        if line[:1] == "%" and self.quote is None and self.block_scalar is None:
            self._finish_pending()
            raise self.fail("directive")
        stripped = line.lstrip(" ")
        col = len(line) - len(stripped)
        if self.block_scalar is not None:
            if stripped == "" or col > self.block_scalar:
                return  # the block scalar's body: text, tabs included
            self.block_scalar = None
        if stripped == "":
            return
        if self.flow and col <= self.flow_indent:
            raise self.fail("flow_line_not_indented")
        if self.quote is not None:
            end = self._quoted_end(line, 0, self.quote)
            if end is None:
                return
            self.quote = None
            end = self._after_scalar(line, end, key_col=None)
            if end >= 0:
                self._scan(line, end)
            return
        if self.pending is not None:
            if stripped[0] != "#" and col > self.pending[2]:
                self.pending_multi = True
                self._continuation(line, col)
                return
            self._finish_pending()
        if not self.flow:
            while self.stack and self.stack[-1][0] > col:
                self.stack.pop()
        self._scan(line, col)

    def _continuation(self, line: str, i: int) -> None:
        """A block plain scalar continued on a more indented line: text up to a comment."""
        while i < len(line):
            ch = line[i]
            if ch == "\t":
                raise self.fail("tab")
            if ch == "#" and line[i - 1] == " ":
                self._comment(line, i)
                self._finish_pending()
                return
            i += 1

    def _comment(self, line: str, i: int) -> None:
        if "\t" in line[i:]:
            raise self.fail("tab")

    def _skip_spaces(self, line: str, i: int) -> int:
        while i < len(line) and line[i] in " \t":
            if line[i] == "\t":
                raise self.fail("tab")
            i += 1
        return i

    def _quoted_end(self, line: str, i: int, quote: str) -> int | None:
        """The index after the closing ``quote`` from ``i`` on, or ``None`` when the scalar runs past the line."""
        while i < len(line):
            ch = line[i]
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if ch == quote:
                if quote == "'" and line[i + 1 : i + 2] == "'":
                    i += 2
                    continue
                return i + 1
            i += 1
        return None

    def _scan(self, line: str, i: int) -> None:
        """Read tokens from ``i`` to the end of the line; every position reached after spaces is a token start."""
        while True:
            i = self._skip_spaces(line, i)
            if i >= len(line):
                return
            ch = line[i]
            nxt = line[i + 1] if i + 1 < len(line) else ""
            if ch == "#":
                self._comment(line, i)
                return
            if ch == "&":
                raise self.fail("anchor")
            if ch == "*":
                raise self.fail("alias")
            if ch == "!":
                raise self.fail("tag")
            self.content_seen = True
            if ch == "?":
                if _space_or_end(line, i + 1) or (
                    self.flow and nxt in _FLOW_INDICATORS
                ):
                    raise self.fail("explicit_key")
                if self.flow:
                    raise self.fail("flow_scalar_indicator_start")
            if ch == ":":
                if _space_or_end(line, i + 1) or (
                    self.flow and nxt in _FLOW_INDICATORS
                ):
                    raise self.fail(
                        "non_string_key"
                    )  # a value with no key: the key is empty
                if self.flow:
                    raise self.fail("flow_scalar_indicator_start")
            if self.flow:
                if ch in "[{":
                    self._open_flow(ch)
                    i += 1
                    continue
                if ch in "]}":
                    self.flow.pop()
                    i = self._after_scalar(line, i + 1, key_col=None, collection=True)
                    if i < 0:
                        return
                    continue
                if ch == ",":
                    i += 1
                    continue
            else:
                if ch == "-" and _space_or_end(line, i + 1):
                    self._block_entry(i, "seq")
                    i += 1
                    continue
                if ch in "|>":
                    self.block_scalar = self._owner()
                    self._comment(line, i)
                    return
                if ch in "[{":
                    self.flow_indent = self._owner()
                    self._open_flow(ch)
                    i += 1
                    continue
            if ch in "'\"":
                end = self._quoted_end(line, i + 1, ch)
                if end is None:
                    self.quote = ch
                    return
                i = self._after_scalar(line, end, key_col=i)
                if i < 0:
                    return
                continue
            i = self._plain(line, i)
            if i < 0:
                return

    def _open_flow(self, ch: str) -> None:
        self.flow.append(ch)
        self._check_depth()

    def _after_scalar(
        self, line: str, i: int, key_col: int | None, collection: bool = False
    ) -> int:
        """After a quoted scalar or a closed flow collection: a ``:`` makes it a key. Returns where to go on, or -1."""
        j = self._skip_spaces(line, i)
        if (
            j < len(line)
            and line[j] == ":"
            and (self.flow or _space_or_end(line, j + 1))
        ):
            if collection:
                raise self.fail("non_string_key")
            if not self.flow and key_col is not None:
                self._block_entry(key_col, "map")
            return j + 1
        if j < len(line) and line[j] == "#":
            self._comment(line, j)
            return -1
        return j

    def _plain(self, line: str, start: int) -> int:
        """A plain scalar from ``start``: checked when it ends (a key at once). Returns where to go on, or -1."""
        i = start
        n = len(line)
        while i < n:
            ch = line[i]
            if ch == "\t":
                raise self.fail("tab")
            if ch == "#" and line[i - 1] == " ":
                break
            if ch == ":" and (
                _space_or_end(line, i + 1)
                or (self.flow and line[i + 1] in _FLOW_INDICATORS)
            ):
                text = line[start:i].rstrip(" ")
                self._check_plain(text, self.ln, True)
                if not self.flow:
                    self._block_entry(start, "map")
                return i + 1
            if self.flow and ch in ",?[]{}":
                break
            i += 1
        text = line[start:i].rstrip(" ")
        if self.flow:
            self._check_plain(text, self.ln, False)
            return i
        if i < n:  # ended by a comment
            self._check_plain(text, self.ln, False)
            self._comment(line, i)
            return -1
        self.pending = (text, self.ln, self._owner())
        self.pending_multi = False
        return -1


def profile_violation(text: str) -> ProfileViolation | None:
    """The first construct in ``text`` outside the SPEC 8.7 YAML profile, or ``None`` (repeated keys excepted, see
    :func:`duplicate_key_line`)."""
    return _Scanner(text).run()


def duplicate_key_line(node: yaml.Node | None) -> int | None:
    """The line (1-based) of the earliest key that repeats an earlier key of its mapping, or ``None``."""
    lines: list[int] = []
    pending = [node] if node is not None else []
    while pending:
        current = pending.pop()
        if isinstance(current, yaml.MappingNode):
            seen: set[str] = set()
            for key_node, value_node in current.value:
                key = key_node.value if isinstance(key_node, yaml.ScalarNode) else None
                if key is not None:
                    if key in seen:
                        lines.append(key_node.start_mark.line + 1)
                    seen.add(key)
                pending.append(value_node)
        elif isinstance(current, yaml.SequenceNode):
            pending.extend(current.value)
    return min(lines) if lines else None
