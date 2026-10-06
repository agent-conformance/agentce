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
