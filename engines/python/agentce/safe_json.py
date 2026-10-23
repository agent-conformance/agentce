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
