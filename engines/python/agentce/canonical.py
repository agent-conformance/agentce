"""The AgentCE canonical form (SPEC §6.7): RFC 8785 JCS under the evidence profile.

The evidence profile forbids non-integer JSON numbers, so the hard part of JCS -- shortest
round-trip formatting of IEEE 754 doubles -- never arises, and the canonical form is a small exact
set of rules every engine reproduces byte for byte:

  * literals   null, true, false serialise as those tokens;
  * numbers    integers only, as their exact decimal (a non-integer is refused);
  * strings    NFC-normalised, then minimally escaped as ECMAScript JSON.stringify does;
  * arrays     elements in order, comma-separated, no whitespace;
  * objects    members sorted by the UTF-16 code units of their NFC-normalised keys, no whitespace.

This is the engine's own implementation; ``canonical-form.md`` is the normative specification and the
shared vectors under ``spec/model/test-vectors/`` are the reference it reproduces (a test runs them).
It is the basis of ``integrity.hash`` (SPEC §6.6) and of every byte-for-byte comparison the engine
makes. No network, no learned component.
"""

from __future__ import annotations

import hashlib
import unicodedata

__all__ = ["CanonicalizationError", "canonical_string", "canonicalize", "sha256_hex"]


class CanonicalizationError(ValueError):
    """A value cannot be put in canonical form under the AgentCE profile.

    ``reason`` is a stable key (``non_integer_number``, ``duplicate_key_after_nfc``,
    ``unsupported_type``) so that the vectors and every engine agree on which inputs are refused.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


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
    return nfc_key.encode("utf-16-be")


def _serialise(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(
        value, bool
    ):  # bool is a subclass of int; must precede the int branch
        return "true" if value else "false"
    if isinstance(value, int):
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


def _serialise_object(obj: dict[object, object]) -> str:
    members: list[tuple[str, object]] = []
    seen: set[str] = set()
    for raw_key, raw_value in obj.items():
        if not isinstance(raw_key, str):
            raise CanonicalizationError(
                "unsupported_type", f"object key {type(raw_key).__name__}"
            )
        nfc_key = _nfc(raw_key)
        if nfc_key in seen:
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
    """Return the canonical form of a JSON value as a ``str`` (SPEC §6.7)."""
    return _serialise(value)


def canonicalize(value: object) -> bytes:
    """Return the canonical form of a JSON value as UTF-8 bytes (SPEC §6.7)."""
    return canonical_string(value).encode("utf-8")


def sha256_hex(value: object) -> str:
    """Return the lowercase hex SHA-256 of the canonical form (basis of ``integrity.hash``, §6.6)."""
    return hashlib.sha256(canonicalize(value)).hexdigest()
