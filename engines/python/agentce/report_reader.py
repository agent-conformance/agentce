"""The report-file reader contract for the files that are not JSON (SPEC 8.7).

``report --validate`` reads ``report.csv``, ``report.junit.xml``, ``oscal-ar.xml``, ``report.md`` and
``report.html`` by one written contract, the same in every engine, with no library deciding the
outcome: strict UTF-8 with no byte-order mark (:func:`decode_report_text`), CSV by RFC 4180
(:func:`read_csv`), well-formed XML 1.0 with no DTD, no entity beyond the five predefined, nothing after
the root element, no control character, every prefix bound, the reserved ``xml`` and ``xmlns``
prefixes and namespace names kept and no two attributes with one expanded name (:func:`check_xml`),
and one whitespace set for the empty-file check (:func:`is_blank`). JSON and JSON Lines files keep the JSON profile
(``safe_json``). The rule ids are the ``rule`` tokens of ``spec/report/reader-cases.json``;
``reportReader.ts`` and ``ReportReader.java`` apply the same rules.
"""

from __future__ import annotations

import re

from .safe_json import JsonProfileError, _decode


class ReaderError(ValueError):
    """A file outside the reader contract: ``rule`` is a case-table rule token, ``line`` is 1-based
    (1 + the LFs before the fault). ``str()`` is the phrase every engine's problem line carries."""

    def __init__(self, rule: str, line: int) -> None:
        super().__init__(
            f"outside the report-file reader contract: {rule} at line {line} (SPEC 8.7)"
        )
        self.rule = rule
        self.line = line


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def decode_report_text(data: bytes) -> str:
    """``data`` decoded as strict UTF-8: a leading byte-order mark is ``bom``; an invalid or overlong
    sequence, an encoded surrogate, a code point over U+10FFFF or a truncated sequence is
    ``invalid_utf8`` at the line of its first byte."""
    try:
        return _decode(data, "refuse")
    except JsonProfileError as exc:
        raise ReaderError(exc.rule, exc.line) from None


def is_blank(text: str) -> bool:
    """Whether ``text`` holds only space, tab, CR and LF (none at all included). FF, VT, NEL, NBSP and
    U+3000 are text here, not whitespace."""
    return not text.strip(" \t\r\n")


# --- CSV (RFC 4180) --------------------------------------------------------------------------------


def _csv_field(text: str, i: int) -> tuple[str, int]:
    """The field starting at ``i``; return its value and the index of the ``,``, record end or end of
    text after it."""
    n = len(text)
    if text.startswith('"', i):
        opening = i
        parts: list[str] = []
        i += 1
        while True:
            close = text.find('"', i)
            if close < 0:
                raise ReaderError("csv_unclosed_quote", _line(text, opening))
            parts.append(text[i:close])
            if text.startswith('"', close + 1):
                parts.append('"')
                i = close + 2
                continue
            i = close + 1
            break
        if i < n and text[i] not in ",\n" and not text.startswith("\r\n", i):
            raise ReaderError("csv_text_after_quote", _line(text, i))
        return "".join(parts), i
    j = i
    while j < n:
        c = text[j]
        if c == "," or c == "\n":
            break
        if c == "\r":
            if text.startswith("\r\n", j):
                break
            raise ReaderError("csv_bare_cr", _line(text, j))
        if c == '"':
            raise ReaderError("csv_quote_in_field", _line(text, j))
        j += 1
    return text[i:j], j


def read_csv(text: str) -> list[list[str]]:
    """The records of ``text``, each a list of fields. A record ends at LF or CR LF outside quotes; a
    final record end does not start a new record; an empty line is a record with no fields, even
    at the very end (SPEC 8.7: the final line end is optional, an empty line after it is refused).
    Raises :class:`ReaderError` at the first fault."""
    records: list[list[str]] = []
    n = len(text)
    i = 0
    while i < n:
        empty_line = text.startswith(("\n", "\r\n"), i)
        fields: list[str] = []
        while True:
            value, i = _csv_field(text, i)
            fields.append(value)
            if i >= n:
                break
            if text[i] == ",":
                i += 1
                continue
            i += 2 if text[i] == "\r" else 1
            break
        records.append([] if empty_line else fields)
    return records


# --- XML 1.0 well-formedness -----------------------------------------------------------------------

#: XML 1.0 Fifth Edition NameStartChar and NameChar (section 2.3).
_NAME_START = (
    ":A-Z_a-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u02ff\u0370-\u037d\u037f-\u1fff\u200c-\u200d"
    "\u2070-\u218f\u2c00-\u2fef\u3001-\ud7ff\uf900-\ufdcf\ufdf0-\ufffd\U00010000-\U000effff"
)
_NAME_EXTRA = "\\-.0-9\u00b7\u0300-\u036f\u203f-\u2040"
_NAME = re.compile(f"[{_NAME_START}][{_NAME_START}{_NAME_EXTRA}]*")
#: A character outside XML 1.0 ``Char`` (section 2.2).
_NOT_CHAR = re.compile("[^\t\n\r\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
_WS = " \t\r\n"
_PREDEFINED = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_VERSION = re.compile(r"1\.[0-9]+")
_ENC_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._\-]*")
_STANDALONE = re.compile("yes|no")
_HEX_REF = re.compile("[0-9a-fA-F]+;")
_DEC_REF = re.compile("[0-9]+;")
_TEXT_STOP = re.compile("[<&]")
_VALUE_SPACE = re.compile("\r\n?|[\t\n]")
_VALUE_REF = re.compile("&(?:#x([0-9a-fA-F]+)|#([0-9]+)|([^;]+));")
#: The two reserved namespace names (Namespaces in XML 1.0, section 3).
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_XMLNS_NS = "http://www.w3.org/2000/xmlns/"


def _ref_value(m: re.Match[str]) -> str:
    hex_digits, dec_digits, entity = m.groups()
    if entity is not None:
        return _PREDEFINED[entity]
    return chr(int(hex_digits, 16) if hex_digits is not None else int(dec_digits))


def _normalized(raw: str) -> str:
    """An attribute value already checked by the scanner, normalized as XML 1.0 section 3.3.3 reads
    it: each line end, tab or LF becomes a space, then each reference becomes its character."""
    return _VALUE_REF.sub(_ref_value, _VALUE_SPACE.sub(" ", raw))


def _is_char(cp: int) -> bool:
    return (
        cp in (0x9, 0xA, 0xD)
        or 0x20 <= cp <= 0xD7FF
        or 0xE000 <= cp <= 0xFFFD
        or 0x10000 <= cp <= 0x10FFFF
    )


class _Xml:
    """One pass over the decoded text. A fault at index ``at`` is reported as ``xml_control_char``
    instead when a raw character outside ``Char`` comes at or before it, so the first fault in text
    order wins."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.i = 0
        bad = _NOT_CHAR.search(text)
        self.bad_at = bad.start() if bad else None

    def fail(self, rule: str, at: int | None = None) -> ReaderError:
        at = self.i if at is None else at
        if self.bad_at is not None and self.bad_at <= at:
            rule, at = "xml_control_char", self.bad_at
        return ReaderError(rule, _line(self.text, at))

    def startswith(self, s: str) -> bool:
        return self.text.startswith(s, self.i)

    def skip_ws(self) -> bool:
        start = self.i
        while self.i < self.n and self.text[self.i] in _WS:
            self.i += 1
        return self.i > start

    def name(self) -> str:
        m = _NAME.match(self.text, self.i)
        if m is None:
            raise self.fail("xml_syntax")
        self.i = m.end()
        return m.group()

    def qname(self) -> tuple[str, int]:
        """A namespace-conforming name: at most one colon, with both sides non-empty."""
        at = self.i
        name = self.name()
        parts = name.split(":")
        if len(parts) > 2 or not all(parts):
            raise self.fail("xml_syntax", at)
        return name, at

    def expect(self, s: str) -> None:
        if not self.startswith(s):
            raise self.fail("xml_syntax")
        self.i += len(s)

    def comment(self) -> None:
        """At ``<!--``: the comment, with no ``--`` inside."""
        self.i += 4
        dashes = self.text.find("--", self.i)
        if dashes < 0:
            raise self.fail("xml_syntax", self.n)
        if not self.text.startswith("-->", dashes):
            raise self.fail("xml_syntax", dashes)
        self.i = dashes + 3

    def pi(self) -> None:
        """At ``<?`` (not the declaration at offset 0): a processing instruction whose target is not
        ``xml`` in any case and has no colon."""
        self.i += 2
        at = self.i
        target = self.name()
        if target.lower() == "xml" or ":" in target:
            raise self.fail("xml_syntax", at)
        if not self.startswith("?>"):
            if not self.skip_ws():
                raise self.fail("xml_syntax")
            end = self.text.find("?>", self.i)
            if end < 0:
                raise self.fail("xml_syntax", self.n)
            self.i = end
        self.i += 2

    def quoted(self, pattern: re.Pattern[str]) -> str:
        quote = self.text[self.i : self.i + 1]
        if quote not in ("'", '"'):
            raise self.fail("xml_syntax")
        self.i += 1
        end = self.text.find(quote, self.i)
        if end < 0:
            raise self.fail("xml_syntax", self.n)
        value = self.text[self.i : end]
        if pattern.fullmatch(value) is None:
            raise self.fail("xml_syntax")
        self.i = end + 1
        return value

    def eq(self) -> None:
        self.skip_ws()
        self.expect("=")
        self.skip_ws()

    def declaration(self) -> None:
        """At offset 0, ``<?xml`` followed by whitespace: version, optional UTF-8 encoding, optional
        standalone, ``?>``."""
        self.i = 5
        self.skip_ws()
        self.expect("version")
        self.eq()
        self.quoted(_VERSION)
        spaced = self.skip_ws()
        if spaced and self.startswith("encoding"):
            self.i += len("encoding")
            self.eq()
            at = self.i
            if self.quoted(_ENC_NAME).lower() != "utf-8":
                raise self.fail("xml_syntax", at)
            spaced = self.skip_ws()
        if spaced and self.startswith("standalone"):
            self.i += len("standalone")
            self.eq()
            self.quoted(_STANDALONE)
            self.skip_ws()
        self.expect("?>")

    def reference(self) -> None:
        """At ``&``: a character reference to a ``Char`` or one of the five predefined entities."""
        at = self.i
        text = self.text
        if self.startswith("&#x"):
            m = _HEX_REF.match(text, self.i + 3)
            base = 16
        elif self.startswith("&#"):
            m = _DEC_REF.match(text, self.i + 2)
            base = 10
        else:
            m = _NAME.match(text, self.i + 1)
            if m is None or not text.startswith(";", m.end()):
                raise self.fail("xml_syntax", at)
            if m.group() not in _PREDEFINED:
                raise self.fail("xml_entity", at)
            self.i = m.end() + 1
            return
        if m is None:
            raise self.fail("xml_syntax", at)
        digits = m.group()[:-1]
        # Past U+10FFFF is outside Char whatever its length; the length cap keeps int() cheap.
        if len(digits.lstrip("0")) > 8 or not _is_char(int(digits, base)):
            raise self.fail("xml_control_char", at)
        self.i = m.end()

    def start_tag(self, scopes: list[dict[str, str]]) -> tuple[str, bool]:
        """At ``<`` + a name: the start tag. Return its raw name and whether it was empty (``/>``);
        push its namespace scope (prefix to namespace name) when it was not. A declaration that breaks
        a reserved prefix or namespace name, and two attributes with one expanded name, are
        ``xml_syntax`` (Namespaces in XML 1.0, sections 3 and 6.3)."""
        self.i += 1
        name, name_at = self.qname()
        attributes: list[tuple[str, int, str]] = []
        declared: dict[str, str] = {}
        while True:
            spaced = self.skip_ws()
            if self.startswith("/>") or self.startswith(">"):
                break
            if not spaced:
                raise self.fail("xml_syntax")
            attr, attr_at = self.qname()
            if any(attr == seen for seen, _, _ in attributes):
                raise self.fail("xml_syntax", attr_at)
            self.eq()
            quote = self.text[self.i : self.i + 1]
            if quote not in ("'", '"'):
                raise self.fail("xml_syntax")
            self.i += 1
            value_at = self.i
            while True:
                if self.i >= self.n:
                    raise self.fail("xml_syntax")
                c = self.text[self.i]
                if c == quote:
                    break
                if c == "<":
                    raise self.fail("xml_syntax")
                if c == "&":
                    self.reference()
                else:
                    self.i += 1
            value = self.text[value_at : self.i]
            if attr.startswith("xmlns:"):
                if not value:
                    raise self.fail("xml_syntax", attr_at)
                declared[attr[6:]] = _normalized(value)
            self.i += 1
            attributes.append((attr, attr_at, value))
        scope = {**scopes[-1], **declared} if declared else scopes[-1]
        prefix = name.partition(":")[0] if ":" in name else None
        if prefix == "xmlns":
            raise self.fail("xml_syntax", name_at)
        if prefix is not None and prefix != "xml" and prefix not in scope:
            raise self.fail("xml_unbound_prefix", name_at)
        expanded: set[tuple[str, str]] = set()
        for attr, attr_at, value in attributes:
            if ":" not in attr:
                if attr == "xmlns" and _normalized(value) in (_XML_NS, _XMLNS_NS):
                    raise self.fail("xml_syntax", attr_at)
                continue
            prefix, _, local = attr.partition(":")
            if prefix == "xmlns":
                ns = declared[local]
                if (
                    local == "xmlns"
                    or ns == _XMLNS_NS
                    or (local == "xml") != (ns == _XML_NS)
                ):
                    raise self.fail("xml_syntax", attr_at)
                continue
            bound = _XML_NS if prefix == "xml" else scope.get(prefix)
            if bound is None:
                raise self.fail("xml_unbound_prefix", attr_at)
            if (bound, local) in expanded:
                raise self.fail("xml_syntax", attr_at)
            expanded.add((bound, local))
        empty = self.startswith("/>")
        self.i += 2 if empty else 1
        if not empty:
            scopes.append(scope)
        return name, empty

    def misc(self) -> bool:
        """One Misc item (whitespace, comment, PI) or a DOCTYPE; False when the next thing is none."""
        if self.skip_ws():
            return True
        if self.startswith("<!--"):
            self.comment()
        elif self.startswith("<!DOCTYPE"):
            raise self.fail("xml_dtd")
        elif self.startswith("<?"):
            self.pi()
        else:
            return False
        return True

    def document(self) -> None:
        text = self.text
        if text.startswith("<?xml") and text[5:6] != "" and text[5:6] in _WS:
            self.declaration()
        while self.misc():
            pass
        if self.i >= self.n or not self.startswith("<"):
            raise self.fail("xml_syntax")
        names: list[str] = []
        scopes: list[dict[str, str]] = [{}]
        name, empty = self.start_tag(scopes)
        if not empty:
            names.append(name)
        while names:
            if self.i >= self.n:
                raise self.fail("xml_syntax")
            if self.startswith("<"):
                if self.startswith("</"):
                    self.i += 2
                    at = self.i
                    if self.name() != names[-1]:
                        raise self.fail("xml_syntax", at)
                    self.skip_ws()
                    self.expect(">")
                    names.pop()
                    scopes.pop()
                elif self.startswith("<!--"):
                    self.comment()
                elif self.startswith("<![CDATA["):
                    end = text.find("]]>", self.i + 9)
                    if end < 0:
                        raise self.fail("xml_syntax", self.n)
                    self.i = end + 3
                elif self.startswith("<!DOCTYPE"):
                    raise self.fail("xml_dtd")
                elif self.startswith("<?"):
                    self.pi()
                else:
                    name, empty = self.start_tag(scopes)
                    if not empty:
                        names.append(name)
            elif self.startswith("&"):
                self.reference()
            else:
                stop = _TEXT_STOP.search(text, self.i)
                end = stop.start() if stop else self.n
                cdata_end = text.find("]]>", self.i, end)
                if cdata_end >= 0:
                    raise self.fail("xml_syntax", cdata_end)
                self.i = end
        while self.i < self.n:
            if not self.misc():
                raise self.fail("xml_after_root")
        if self.bad_at is not None:
            raise self.fail("xml_control_char", self.bad_at)


def check_xml(text: str) -> None:
    """Raise :class:`ReaderError` unless ``text`` is one well-formed, namespace-conforming XML 1.0
    document with no DTD, no entity reference beyond the five predefined, nothing after the root
    element but whitespace, comments and processing instructions, every character a ``Char`` (raw or
    referenced), every prefix bound, the reserved prefixes and namespace names kept, no attribute's
    expanded name twice on one element and no colon in a processing-instruction target. The first
    fault in text order is the one named."""
    _Xml(text).document()
