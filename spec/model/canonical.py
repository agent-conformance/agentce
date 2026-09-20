#!/usr/bin/env python3
"""Reference implementation of the AgentCE canonical form (SPEC 6.7).

The canonical form is RFC 8785 JSON Canonicalization Scheme (JCS) under the AgentCE evidence profile,
which forbids non-integer JSON numbers. Because floating-point numbers never appear in canonical
evidence, the one genuinely hard part of JCS -- shortest round-trip formatting of IEEE 754 doubles --
does not arise, and the canonical form is defined by a small, exact set of rules that any language can
implement identically:

  * literals             null, true, false serialise as those three tokens;
  * numbers              integers only, serialised as their exact decimal (a non-integer, or an integer beyond
                         2^53 - 1 in magnitude, is refused);
  * strings              NFC-normalised, then minimally escaped as ECMAScript JSON.stringify does;
  * arrays               elements in order, comma-separated, no whitespace;
  * objects              members sorted by the UTF-16 code units of their NFC-normalised keys,
                         "key":value, comma-separated, no whitespace.

The output is UTF-8 bytes. `canonical-form.md` is the normative specification; this module and the
vectors in `test-vectors/` are the reference the TypeScript and Java engines must reproduce byte for
byte. No network, no learned component.
"""

from __future__ import annotations

import hashlib
import unicodedata

__all__ = ["CanonicalizationError", "canonical_string", "canonicalize", "sha256_hex"]


_MAX_SAFE_INTEGER = 2**53 - 1  # the range every engine represents exactly


class CanonicalizationError(ValueError):
    """A value cannot be put in canonical form under the AgentCE profile.

    `reason` is a stable key (`non_integer_number`, `integer_out_of_range`,
    `duplicate_key_after_nfc`, `unsupported_type`) so that the test vectors and every engine agree
    on which inputs are refused.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


# Two-character escapes RFC 8785 / ECMAScript use for these C0 control characters; every other
# character below U+0020 is escaped as \u00xx (lowercase), and nothing at or above U+0020 is escaped
# except the quotation mark and the reverse solidus. The forward slash is NOT escaped.
_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _string(text: str) -> str:
    """Serialise a string: NFC-normalise, wrap in quotes, minimally escape (SPEC 6.7)."""
    out = ['"']
    for ch in _nfc(text):
        code = ord(ch)
        escape = _SHORT_ESCAPES.get(code)
        if escape is not None:
            out.append(escape)
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _utf16_key(nfc_key: str) -> bytes:
    """Sort key giving UTF-16 code-unit order (RFC 8785 object member ordering).

    Comparing the big-endian UTF-16 encodings byte by byte is exactly comparing the sequences of
    UTF-16 code units, which is what JCS requires -- and which differs from Python's native
    code-point ordering for characters outside the Basic Multilingual Plane.
    """
    return nfc_key.encode("utf-16-be")


def _serialise(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(
        value, bool
    ):  # bool is a subclass of int; must precede the int branch
        return "true" if value else "false"
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise CanonicalizationError("integer_out_of_range", str(value))
        return str(value)
    if isinstance(value, float):
        raise CanonicalizationError("non_integer_number", repr(value))
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialise(item) for item in value) + "]"
    if isinstance(value, dict):
        return _serialise_object(value)
    raise CanonicalizationError("unsupported_type", type(value).__name__)


def _serialise_object(obj: dict) -> str:
    members = []
    seen: set[str] = set()
    for raw_key, raw_value in obj.items():
        if not isinstance(raw_key, str):
            raise CanonicalizationError(
                "unsupported_type", f"object key {type(raw_key).__name__}"
            )
        nfc_key = _nfc(raw_key)
        if nfc_key in seen:
            # Two keys that are distinct before normalisation but equal after it would make the object
            # ambiguous; RFC 8785 forbids duplicate members.
            raise CanonicalizationError("duplicate_key_after_nfc", nfc_key)
        seen.add(nfc_key)
        members.append((nfc_key, raw_value))
    members.sort(key=lambda kv: _utf16_key(kv[0]))
    return (
        "{"
        + ",".join(_string(key) + ":" + _serialise(val) for key, val in members)
        + "}"
    )


def canonical_string(value: object) -> str:
    """Return the canonical form of a JSON value as a str (SPEC 6.7)."""
    return _serialise(value)


def canonicalize(value: object) -> bytes:
    """Return the canonical form of a JSON value as UTF-8 bytes (SPEC 6.7)."""
    return canonical_string(value).encode("utf-8")


def sha256_hex(value: object) -> str:
    """Return the lowercase hex SHA-256 of the canonical form (the basis of integrity.hash, SPEC 6.6)."""
    return hashlib.sha256(canonicalize(value)).hexdigest()


if __name__ == "__main__":
    import sys

    data = sys.stdin.buffer.read()
    import json

    sys.stdout.buffer.write(canonicalize(json.loads(data)))
