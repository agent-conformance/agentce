#!/usr/bin/env python3
"""Generate the canonical-form test vectors in test-vectors/*.json (SPEC 6.7, 5.3).

Each value vector records an `input` JSON value, its `canonical` form (a string), and the `sha256` of
the canonical UTF-8 bytes; each error vector records an `input` and the stable `error` reason the
canonicaliser must raise. The vectors are the cross-language contract: the Python reference here and
the TypeScript and Java engines must all reproduce `canonical`/`sha256` and refuse the error inputs.

The expected values are computed by the reference implementation, so the committed files also serve as
a regression guard on it. Independent correctness is pinned separately, by the hand-written assertions
in test-vectors/test_vectors.py (UTF-16 ordering, escaping, NFC), which do not go through this
generator. Run `python gen_vectors.py` after changing canonical.py or the vector list; the output is
deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import canonical

HERE = Path(__file__).resolve().parent
OUT = HERE / "test-vectors"
EVENTS = HERE / "examples" / "appendix-g"

# Control-character strings are built with chr() so no raw control byte ever appears in this source.
CONTROLS_BFNTR = "".join(map(chr, (0x08, 0x09, 0x0A, 0x0C, 0x0D)))
CONTROLS_NULL_US = "".join(map(chr, (0x00, 0x01, 0x1F)))
BELL = "ring" + chr(0x07) + "bell"
DEL = "x" + chr(0x7F) + "y"

# NFC-sensitive strings are built from explicit code points so that the composed and decomposed forms
# stay visibly distinct in this source and survive auto-formatting (a formatter turns \u escapes into
# literal characters, which would make an "é" written two ways indistinguishable on screen).
E_ACUTE = chr(0x00E9)  # precomposed  U+00E9  é
E_ACUTE_NFD = "e" + chr(0x0301)  # decomposed   e + combining acute
A_RING_NFD = "A" + chr(0x030A)  # decomposed   A + combining ring
HANGUL_NFD = chr(0x1100) + chr(0x1161)  # decomposed   Hangul choseong + jungseong -> 가
EMOJI = chr(0x1F600)  # astral       grinning face (surrogate pair in UTF-16)
MATH_A = chr(0x1D400)  # astral       mathematical bold capital A
FULLWIDTH_Z = chr(0xFF3A)  # BMP          fullwidth Latin capital Z

# (id, description, input) -- the canonicaliser must accept these.
VALUE_VECTORS: list[tuple[str, str, object]] = [
    # Literals
    ("literal-null", "The null literal.", None),
    ("literal-true", "The true literal.", True),
    ("literal-false", "The false literal.", False),
    # Integers (all within the JS-safe range, <= 2^53-1, so every engine is exact)
    ("int-zero", "Zero.", 0),
    ("int-one", "A small positive integer.", 1),
    ("int-neg-one", "A small negative integer.", -1),
    ("int-42", "A two-digit integer.", 42),
    ("int-neg-42", "A negative two-digit integer.", -42),
    ("int-255", "A byte boundary.", 255),
    ("int-million", "A larger integer.", 1000000),
    (
        "int-2pow53-minus-1",
        "The largest exactly representable double integer.",
        9007199254740991,
    ),
    # Basic strings
    ("str-empty", "The empty string.", ""),
    ("str-a", "A single ASCII letter.", "a"),
    ("str-ascii", "An ASCII phrase with a space.", "hello world"),
    ("str-bmp-cafe", "A precomposed accented BMP string.", "caf" + E_ACUTE),
    ("str-bmp-naive", "A diaeresis in the BMP.", "na" + chr(0x00EF) + "ve"),
    ("str-cjk", "CJK characters (BMP).", chr(0x65E5) + chr(0x672C) + chr(0x8A9E)),
    ("str-emoji", "An astral-plane emoji (surrogate pair).", "grin " + EMOJI),
    (
        "str-combining-multiple",
        "Several combining marks that NFC composes.",
        "a" + chr(0x0301) + "e" + chr(0x0301),
    ),
    ("str-del", "U+007F is at or above U+0020 and is not escaped.", DEL),
    # String escaping
    ("esc-quote", "A quotation mark is escaped.", 'say "hi"'),
    ("esc-backslash", "A reverse solidus is escaped.", "a\\b"),
    ("esc-solidus", "A forward solidus is NOT escaped.", "a/b/c"),
    ("esc-controls-bfnrt", "The five short-form control escapes.", CONTROLS_BFNTR),
    (
        "esc-null-and-us",
        "C0 controls without a short form use the backslash-u form.",
        CONTROLS_NULL_US,
    ),
    ("esc-bell", "The bell control has no short form.", BELL),
    # NFC normalisation
    ("nfc-e-acute", "Decomposed e + combining acute composes to U+00E9.", E_ACUTE_NFD),
    ("nfc-a-ring", "Decomposed A + combining ring composes to U+00C5.", A_RING_NFD),
    ("nfc-hangul", "Conjoining Hangul jamo compose to a syllable.", HANGUL_NFD),
    ("nfc-idempotent", "Already-NFC text is unchanged.", "caf" + E_ACUTE),
    (
        "nfc-in-key",
        "A decomposed object key is normalised before sorting.",
        {E_ACUTE_NFD: 1, "b": 2},
    ),
    # Arrays
    ("arr-empty", "The empty array.", []),
    ("arr-single", "A one-element array.", [1]),
    ("arr-ints", "Integers in order.", [1, 2, 3]),
    ("arr-nested", "Nested arrays.", [[1], [2, [3]]]),
    ("arr-mixed", "Mixed element types.", [None, True, "x", 1]),
    ("arr-order-preserved", "Array order is preserved, never sorted.", [3, 1, 2]),
    ("arr-of-objects", "An array of objects.", [{"b": 1}, {"a": 2}]),
    # Objects
    ("obj-empty", "The empty object.", {}),
    ("obj-single", "A one-member object.", {"a": 1}),
    ("obj-sort-ascii", "Members are sorted by key.", {"b": 1, "a": 2, "A": 3}),
    (
        "obj-unicode-keys",
        "Unicode BMP keys sorted by code unit.",
        {E_ACUTE: 1, "e": 2, "z": 3},
    ),
    (
        "obj-astral-key-order",
        "An astral key sorts before a BMP key by UTF-16 code unit, not code point.",
        {FULLWIDTH_Z: 1, MATH_A: 2},
    ),
    (
        "obj-numeric-string-keys",
        "Numeric-looking keys sort as strings, not numbers.",
        {"10": 1, "2": 2, "1": 3},
    ),
    ("obj-empty-key", "The empty string is a valid key.", {"": 1, "a": 2}),
    ("obj-key-with-space", "A key containing a space.", {"a b": 1, "a": 2}),
    ("obj-nested", "A nested object with keys out of order.", {"z": {"y": 1}, "a": 2}),
    (
        "obj-deep",
        "Deeper nesting of objects and arrays.",
        {"a": {"b": {"c": [1, {"d": 2}]}}},
    ),
    (
        "obj-values-mixed",
        "Mixed value types in one object.",
        {"n": None, "b": True, "i": 5, "s": "t", "a": [1]},
    ),
]


def _load_event(name: str) -> object:
    return json.loads((EVENTS / name).read_text(encoding="utf-8"))


# (id, description, input, expected reason) -- the canonicaliser must refuse these.
ERROR_VECTORS: list[tuple[str, str, object, str]] = [
    (
        "err-float-simple",
        "A non-integer number is not permitted.",
        1.5,
        "non_integer_number",
    ),
    (
        "err-float-exp",
        "A number in exponent form parses as a float and is refused.",
        1e30,
        "non_integer_number",
    ),
    (
        "err-float-zero-point",
        "A fractional value below one is refused.",
        0.1,
        "non_integer_number",
    ),
    (
        "err-float-in-array",
        "A float nested in an array is refused.",
        [1, 2.5],
        "non_integer_number",
    ),
    (
        "err-dup-key-after-nfc",
        "Two keys equal after NFC (precomposed and decomposed) make the object ambiguous.",
        {E_ACUTE: 1, E_ACUTE_NFD: 2},
        "duplicate_key_after_nfc",
    ),
]


# (id, description, the input as JSON text, expected reason or None when it must be accepted). The
# text is written into the vector file verbatim as `input`: re-serialising it would fold `1e2` to
# `100.0`, and a JSON parser folds `1.0`, `1e2` and `-0.0` to whole numbers and an integer above 2^53
# to a nearby double, so the number token has to reach each engine as written for the accept/refuse
# decision to be tested at all. Every engine reads the vector files with a parser that keeps the token.
TOKEN_VECTORS: list[tuple[str, str, str, str | None]] = [
    (
        "err-token-whole-float",
        "A whole number written with a fractional part is a float token and is refused.",
        "1.0",
        "non_integer_number",
    ),
    (
        "err-token-exponent-whole",
        "A whole number written in exponent form is a float token and is refused.",
        "1e2",
        "non_integer_number",
    ),
    (
        "err-token-exponent-capital",
        "The capital exponent marker is refused the same way.",
        "1E2",
        "non_integer_number",
    ),
    (
        "err-token-negative-zero-float",
        "Negative zero written as a float is refused, never collapsed to 0.",
        "-0.0",
        "non_integer_number",
    ),
    (
        "err-token-whole-float-in-object",
        "A whole-number float nested in an object is refused.",
        '{"count": 2.0}',
        "non_integer_number",
    ),
    (
        "err-token-int-2pow53",
        "The first integer above the exactly representable range is refused.",
        "9007199254740992",
        "integer_out_of_range",
    ),
    (
        "err-token-int-neg-2pow53",
        "The same bound applies to negative integers.",
        "-9007199254740992",
        "integer_out_of_range",
    ),
    (
        "err-token-int-2pow53-plus-1",
        "An integer a double cannot tell from a neighbour is refused, not rounded.",
        "10000000000000001",
        "integer_out_of_range",
    ),
    (
        "err-token-int-far-beyond-double",
        "An arbitrarily large integer is refused.",
        "123456789012345678901234567890",
        "integer_out_of_range",
    ),
    (
        "err-token-int-in-array",
        "An out-of-range integer nested in an array is refused.",
        "[1, 9007199254740993]",
        "integer_out_of_range",
    ),
    (
        "token-int-negative-zero",
        "Negative zero written as an integer token is the integer zero.",
        "-0",
        None,
    ),
    (
        "token-int-neg-2pow53-minus-1",
        "The most negative accepted integer.",
        "-9007199254740991",
        None,
    ),
]


_RAW = "\0raw-input\0"


def _write(path: Path, obj: dict, raw_input: str | None = None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    if raw_input is not None:
        text = text.replace(json.dumps(_RAW), raw_input)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    n = 0
    order = 1

    def emit(vid: str, body: dict, raw_input: str | None = None) -> None:
        nonlocal order, n
        _write(OUT / f"{order:04d}-{vid}.json", body, raw_input)
        order += 1
        n += 1

    for vid, desc, value in VALUE_VECTORS:
        emit(
            vid,
            {
                "id": vid,
                "description": desc,
                "input": value,
                "canonical": canonical.canonical_string(value),
                "sha256": canonical.sha256_hex(value),
            },
        )

    for name, vid in (
        ("event-1.json", "event-appendix-g-1"),
        ("event-2.json", "event-appendix-g-2"),
    ):
        value = _load_event(name)
        emit(
            vid,
            {
                "id": vid,
                "description": f"A complete evidence event from SPEC Appendix G ({name}).",
                "input": value,
                "canonical": canonical.canonical_string(value),
                "sha256": canonical.sha256_hex(value),
            },
        )

    for vid, desc, value, reason in ERROR_VECTORS:
        try:
            canonical.canonicalize(value)
        except canonical.CanonicalizationError as e:
            assert e.reason == reason, f"{vid}: expected {reason}, got {e.reason}"
        else:  # pragma: no cover - a mislabelled error vector must fail generation
            raise SystemExit(f"error vector {vid} did not raise")
        emit(vid, {"id": vid, "description": desc, "input": value, "error": reason})

    for vid, desc, text, reason in TOKEN_VECTORS:
        value = json.loads(text)
        body: dict = {"id": vid, "description": desc, "input": _RAW}
        if reason is None:
            body["canonical"] = canonical.canonical_string(value)
            body["sha256"] = canonical.sha256_hex(value)
        else:
            try:
                canonical.canonicalize(value)
            except canonical.CanonicalizationError as e:
                assert e.reason == reason, f"{vid}: expected {reason}, got {e.reason}"
            else:  # pragma: no cover - a mislabelled error vector must fail generation
                raise SystemExit(f"error vector {vid} did not raise")
            body["error"] = reason
        emit(vid, body, raw_input=text)

    print(f"WROTE {n} vectors to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
