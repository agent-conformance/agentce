"""JSON loading with one integer-literal rule in every interpreter setting.

CPython's ``json.loads`` refuses an integer literal longer than ``sys.int_max_str_digits`` with a bare
``ValueError`` (not a ``JSONDecodeError``), and the limit is whatever the interpreter was started with
(``PYTHONINTMAXSTRDIGITS``). The TypeScript and Java engines refuse a literal over 4300 digits, the
interpreter's default, as invalid JSON (``json.ts``, ``Json.java``). :func:`load_json` applies that one
rule here too, so a hostile evidence line or report artifact is invalid JSON, never a crash, and reads
the same on every machine.
"""

from __future__ import annotations

import json
import math
import re
import sys
from typing import Any

#: The most digits an integer literal may carry (sign not counted): CPython's default
#: ``sys.int_max_str_digits``, the rule ``Json.java`` and ``json.ts`` follow.
MAX_INT_STR_DIGITS = 4300
TOO_LONG = f"integer literal exceeds {MAX_INT_STR_DIGITS} digits"
#: ``int()`` reads a string this long under every interpreter limit (the lowest non-zero one is 640).
_CHUNK = 600


class JSONError(ValueError):
    """Text that is not JSON: a syntax error, or an integer literal over :data:`MAX_INT_STR_DIGITS`.

    ``msg`` is the short reason (``Expecting value``, or :data:`TOO_LONG`); ``str()`` is the full text,
    with the line and column when the decoder gives one.
    """

    def __init__(self, msg: str, detail: str) -> None:
        super().__init__(detail)
        self.msg = msg


class _TooLong(Exception):
    """Raised by :func:`_parse_int`; not a ``ValueError``, so nothing in ``json`` can swallow it."""


def _parse_int(token: str) -> int:
    negative = token.startswith("-")
    if len(token) - negative > MAX_INT_STR_DIGITS:
        raise _TooLong
    value = 0
    for start in range(negative, len(token), _CHUNK):
        chunk = token[start : start + _CHUNK]
        value = value * 10 ** len(chunk) + int(chunk)
    return -value if negative else value


def refuse_constant(token: str) -> Any:
    """A ``parse_constant`` hook that refuses ``NaN``, ``Infinity`` and ``-Infinity``: they are not
    JSON, and the TypeScript and Java parsers refuse them."""
    raise ValueError(f"{token} is not JSON")


def jsonl_lines(text: str) -> list[tuple[int, str]]:
    """The JSON Lines of ``text`` as (1-based line number, line), blank lines left out: LF alone ends a
    line, and a line holding only space, tab or CR is blank. Every other line is returned as it stands
    for a strict JSON parser, which accepts only JSON's whitespace around a value (so a CRLF pair reads
    as LF) and refuses a VT, FF, NEL, U+2028, no-break space or byte-order mark outside a string, and
    two values joined by a lone CR. ``json.ts`` and ``Json.java`` apply the same rule. Report files are the
    engines' own JSON Lines output; evidence input keeps ingest's wider rule (18.65), where a lone CR also
    ends a line."""
    return [
        (n, line)
        for n, line in enumerate(text.split("\n"), start=1)
        if line.strip(" \t\r")
    ]


def load_json(text: str | bytes) -> Any:
    """``json.loads(text)``, raising :class:`JSONError` for any invalid JSON, including an integer
    literal over :data:`MAX_INT_STR_DIGITS` digits whatever the interpreter's own limit.

    ``UnicodeDecodeError`` (bytes that are not UTF-8) and ``RecursionError`` pass through unchanged.
    """
    if sys.get_int_max_str_digits() == MAX_INT_STR_DIGITS:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise JSONError(exc.msg, str(exc)) from exc
        except UnicodeDecodeError:
            raise
        except ValueError:
            pass  # an integer literal past the limit: the strict pass below finds and names it
    try:
        return json.loads(text, parse_int=_parse_int)
    except json.JSONDecodeError as exc:
        raise JSONError(exc.msg, str(exc)) from exc
    except _TooLong:
        raise JSONError(TOO_LONG, TOO_LONG) from None


#: The deepest container nesting a strict reader accepts (Jackson's own default limit); all three engines
#: enforce it with the same byte pre-scan, so they refuse at the same depth.
MAX_JSON_DEPTH = 1000

_STRUCTURE = re.compile(rb'["\\\[\]{}]')


def too_deep(raw: bytes) -> bool:
    """Whether ``raw`` opens more than :data:`MAX_JSON_DEPTH` containers at once, counting ``[``
    and ``{`` up and ``]`` and ``}`` down outside strings (a backslash in a string skips the next
    byte). Run before parsing, so depth is found first whatever else is wrong with the bytes."""
    depth = 0
    in_string = False
    escaped_until = -1
    for match in _STRUCTURE.finditer(raw):
        i = match.start()
        if i < escaped_until:
            continue
        byte = raw[i]
        if in_string:
            if byte == 0x5C:
                escaped_until = i + 2
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return True
        elif byte in (0x5D, 0x7D):
            depth -= 1
    return False


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """An ``object_pairs_hook`` that refuses a key written twice in one object. Keys are compared
    decoded, so ``"st\\u0061tus"`` repeats ``"status"``."""
    members = dict(pairs)
    if len(members) != len(pairs):
        raise ValueError("a key is repeated in one object")
    return members


def load_report_json(text: str) -> Any:
    """Parse one report file (or one JSON Lines line of one) by the rule every engine's readiness, sign and
    ``report --validate`` share (SPEC §13.3.4 stage 4): containers nested at most :data:`MAX_JSON_DEPTH`
    deep, no ``NaN``/``Infinity``/``-Infinity``, integer literals of at most :data:`MAX_INT_STR_DIGITS`
    digits, and no key repeated within one object. ``json.ts``'s ``parseReportJson`` and
    ``Json.parseReport`` apply the same rule. Anything else raises :class:`JSONError`."""
    if too_deep(text.encode("utf-8", "surrogatepass")):
        raise JSONError(
            "nested too deep", f"containers nested more than {MAX_JSON_DEPTH} deep"
        )
    try:
        return json.loads(
            text,
            parse_constant=refuse_constant,
            parse_int=_parse_int,
            object_pairs_hook=_unique_keys,
        )
    except json.JSONDecodeError as exc:
        raise JSONError(exc.msg, str(exc)) from exc
    except _TooLong:
        raise JSONError(TOO_LONG, TOO_LONG) from None
    except ValueError as exc:
        raise JSONError(str(exc), str(exc)) from exc


# --- The JSON profile (SPEC 8.7) -------------------------------------------------------------------
#
# One hand-written check, the same rules as ``json.ts`` and ``Json.java``, runs before ``json.loads``
# sees the text, so a file outside the profile is refused with the rule and line whatever the library
# would have done. Rule ids are the ``rule`` values of ``spec/report/json-cases.json``.

_BOM = b"\xef\xbb\xbf"
_WS = " \t\n\r"
_ESCAPES = '"\\/bfnrt'
_LINE_BREAKS = "\f\x85  "


class JsonProfileError(ValueError):
    """Text outside the JSON profile: ``rule`` is a case-table rule id, ``line`` is 1-based."""

    def __init__(self, rule: str, line: int) -> None:
        super().__init__(f"{rule} at line {line}")
        self.rule = rule
        self.line = line


def _hex4(text: str, i: int) -> int | None:
    digits = text[i : i + 4]
    if len(digits) != 4 or any(c not in "0123456789abcdefABCDEF" for c in digits):
        return None
    return int(digits, 16)


def _string(text: str, i: int, line: int) -> tuple[str, int]:
    """Scan the string starting at the quote at ``i``; return its decoded value and the index after it."""
    out: list[str] = []
    i += 1
    n = len(text)
    while True:
        if i >= n:
            raise JsonProfileError("syntax", line)
        c = text[i]
        if c == '"':
            return "".join(out), i + 1
        if c < " ":
            raise JsonProfileError("syntax", line)
        if c != "\\":
            out.append(c)
            i += 1
            continue
        e = text[i + 1 : i + 2]
        if e in _ESCAPES and e:
            out.append(json.loads(f'"\\{e}"'))
            i += 2
            continue
        if e != "u":
            raise JsonProfileError("syntax", line)
        cp = _hex4(text, i + 2)
        if cp is None:
            raise JsonProfileError("syntax", line)
        i += 6
        if 0xDC00 <= cp <= 0xDFFF:
            raise JsonProfileError("lone_surrogate", line)
        if 0xD800 <= cp <= 0xDBFF:
            low = _hex4(text, i + 2) if text[i : i + 2] == "\\u" else None
            if low is None or not 0xDC00 <= low <= 0xDFFF:
                raise JsonProfileError("lone_surrogate", line)
            cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00)
            i += 6
        out.append(chr(cp))


def _number(text: str, i: int, line: int) -> int:
    """Scan the number starting at ``i`` (a ``-`` or a digit); return the index after it."""
    start = i
    n = len(text)
    if text[i] == "-":
        i += 1
        if text[i : i + 8] == "Infinity":
            raise JsonProfileError("infinity", line)
    if i >= n or not text[i].isascii() or not text[i].isdigit():
        raise JsonProfileError("syntax", line)
    if text[i] == "0" and text[i + 1 : i + 2].isdigit():
        raise JsonProfileError("leading_zero", line)
    while i < n and "0" <= text[i] <= "9":
        i += 1
    is_float = False
    if text[i : i + 1] == ".":
        is_float = True
        i += 1
        if not ("0" <= text[i : i + 1] <= "9") or i >= n:
            raise JsonProfileError("syntax", line)
        while i < n and "0" <= text[i] <= "9":
            i += 1
    if text[i : i + 1] in ("e", "E"):
        is_float = True
        i += 1
        if text[i : i + 1] in ("+", "-"):
            i += 1
        if i >= n or not "0" <= text[i] <= "9":
            raise JsonProfileError("syntax", line)
        while i < n and "0" <= text[i] <= "9":
            i += 1
    lexeme = text[start:i]
    if is_float:
        if math.isinf(float(lexeme)):
            raise JsonProfileError("infinity", line)
    elif len(lexeme) - lexeme.startswith("-") > MAX_INT_STR_DIGITS:
        raise JsonProfileError("integer_digit_limit", line)
    return i


def check_profile(text: str, *, jsonl_line: int | None = None) -> None:
    """Raise :class:`JsonProfileError` when ``text`` (one JSON document, or one JSON Lines line when
    ``jsonl_line`` gives its number) is outside the profile. Iterative, so depth costs no recursion."""
    line = jsonl_line or 1
    n = len(text)
    i = 0
    # Each open container: ("o", keys seen) or ("a", None). ``expect`` is what may come next:
    # "value", "first" (a value or the closing bracket), "key", "first_key", "colon", "after", "end".
    stack: list[tuple[str, set[str] | None]] = []
    expect = "value"
    saw_cr = False
    while True:
        while i < n and text[i] in _WS:
            if text[i] == "\n":
                line += 1
            elif text[i] == "\r":
                saw_cr = True
            i += 1
        if i >= n:
            if expect != "end":
                raise JsonProfileError("syntax", line)
            return
        c = text[i]
        if c == "﻿":
            raise JsonProfileError("bom", line)
        if c == "/":
            raise JsonProfileError("comment", line)
        if jsonl_line is not None and c in _LINE_BREAKS:
            raise JsonProfileError("jsonl_line_split", line)
        if expect == "end":
            rule = (
                "jsonl_line_split"
                if jsonl_line is not None and saw_cr
                else "extra_value"
            )
            raise JsonProfileError(rule, line)
        if expect in ("value", "first", "member"):
            if c == "]" and expect == "first" and stack and stack[-1][0] == "a":
                stack.pop()
                i += 1
            elif c in "]}":
                raise JsonProfileError(
                    "trailing_comma" if expect == "value" and stack else "syntax", line
                )
            elif c in "[{":
                stack.append(("a", None) if c == "[" else ("o", set()))
                if len(stack) > MAX_JSON_DEPTH:
                    raise JsonProfileError("depth_over_limit", line)
                i += 1
                expect = "first" if c == "[" else "first_key"
                continue
            elif c == '"':
                _, i = _string(text, i, line)
            elif c == "-" or "0" <= c <= "9":
                i = _number(text, i, line)
            elif c == "+":
                raise JsonProfileError("leading_plus", line)
            else:
                for word, rule in (
                    ("true", ""),
                    ("false", ""),
                    ("null", ""),
                    ("NaN", "nan"),
                    ("Infinity", "infinity"),
                ):
                    if text.startswith(word, i):
                        if rule:
                            raise JsonProfileError(rule, line)
                        i += len(word)
                        break
                else:
                    raise JsonProfileError("syntax", line)
            expect = "after" if stack else "end"
        elif expect in ("key", "first_key"):
            if c == "}" and expect == "first_key":
                stack.pop()
                i += 1
                expect = "after" if stack else "end"
            elif c == "}":
                raise JsonProfileError("trailing_comma", line)
            elif c == '"':
                key, i = _string(text, i, line)
                keys = stack[-1][1]
                assert keys is not None
                if key in keys:
                    raise JsonProfileError("duplicate_key", line)
                keys.add(key)
                expect = "colon"
            else:
                raise JsonProfileError("syntax", line)
        elif expect == "colon":
            if c != ":":
                raise JsonProfileError("syntax", line)
            i += 1
            expect = "member"
        else:  # after a value inside a container
            kind = stack[-1][0]
            if c == ",":
                i += 1
                expect = "value" if kind == "a" else "key"
            elif (c == "]" and kind == "a") or (c == "}" and kind == "o"):
                stack.pop()
                i += 1
                expect = "after" if stack else "end"
            else:
                raise JsonProfileError("syntax", line)


def _decode(raw: bytes, bom: str) -> str:
    if raw.startswith(_BOM):
        if bom == "refuse":
            raise JsonProfileError("bom", 1)
        raw = raw[len(_BOM) :]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JsonProfileError(
            "invalid_utf8", raw[: exc.start].count(b"\n") + 1
        ) from None


def _value(text: str) -> Any:
    # The profile check has already refused every form ``json.loads`` would read loosely.
    return json.loads(text, parse_int=_parse_int)


def read_json(raw: bytes, *, bom: str) -> Any:
    """One JSON document under the profile. ``bom`` is ``"ignore"`` for an input an adopter's tool
    may have written and ``"refuse"`` for a file an engine wrote. Raises :class:`JsonProfileError`."""
    text = _decode(raw, bom)
    check_profile(text)
    return _value(text)


def read_jsonl(raw: bytes, *, bom: str) -> list[tuple[int, Any]]:
    """JSON Lines under the profile: (line number, value) per record, LF alone ending a line, a blank
    line (empty, spaces, tabs) skipped. A ``JsonProfileError`` takes the place of the value of a line
    outside the profile, so a caller can quarantine that line and read on; bytes that are not UTF-8
    or a byte-order mark refused by ``bom`` raise for the whole file."""
    text = _decode(raw, bom)
    out: list[tuple[int, Any]] = []
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip(" \t"):
            continue
        try:
            check_profile(line, jsonl_line=number)
            out.append((number, _value(line)))
        except JsonProfileError as exc:
            out.append((number, exc))
    return out


def read_bundled(raw: bytes) -> Any:
    """Data shipped inside the package, under the same profile; a failure is a build defect."""
    return read_json(raw, bom="refuse")


def render_number(value: int | float) -> str:
    """The one rendering of a JSON number in reason and report text (SPEC 8.7): an integer as its
    digits, a float as the shortest decimal that reads back to it, ``.0`` when integral, exponent form
    at 1e16 and above or below 1e-4, ``-0.0`` for negative zero. ``repr`` is that rule."""
    if isinstance(value, bool):
        raise TypeError("a boolean is not a JSON number")
    return str(value) if isinstance(value, int) else repr(value)


def json_equal(left: Any, right: Any) -> bool:
    """Equality of two JSON values (SPEC 8.7): numbers by value, values of different JSON types
    never equal (``1`` is not ``true``), arrays element-wise, objects by key set and values."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return bool(left == right)  # Python compares an int and a float exactly
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            json_equal(left[k], right[k]) for k in left
        )
    return bool(left == right)
