"""Check the canonical-form reference implementation against the vectors (SPEC 6.7, eval P0.5).

Two kinds of check run here:

  * every ``*.json`` vector in this directory is replayed through ``canonical`` -- an accepted vector
    must reproduce its ``canonical`` string and ``sha256``; an error vector must raise the stated
    reason. This pins the cross-language contract and guards the implementation against regression.
  * a set of hand-written assertions state the tricky outcomes directly (UTF-16 key ordering versus
    code-point ordering, the escaping rules, NFC, integer exactness, float refusal). These do not go
    through the vector generator, so they catch an error that the generator and the vectors would
    otherwise share.

Unicode-sensitive characters are built with chr() rather than written as string escapes, so the two
forms of an accented letter stay visibly distinct in the source and an auto-formatter cannot fold a
decomposed sequence into a precomposed one (or hide an invisible control character).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import canonical  # made importable by conftest.py in this directory

HERE = Path(__file__).resolve().parent
VECTORS = sorted(HERE.glob("*.json"))

E_ACUTE = chr(0x00E9)  # precomposed  U+00E9  é
E_ACUTE_NFD = "e" + chr(0x0301)  # decomposed   e + combining acute
C_ACUTE_NFD = "c" + chr(0x0301)  # decomposed   c + combining acute
DEL = chr(0x7F)  # U+007F, not escaped
FULLWIDTH_Z = chr(0xFF3A)  # BMP    U+FF3A
MATH_A = chr(0x1D400)  # astral U+1D400 (surrogate pair in UTF-16)


def test_there_are_at_least_fifty_vectors():
    assert len(VECTORS) >= 50


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_vector(path: Path):
    vector = json.loads(path.read_text(encoding="utf-8"))
    value = (
        json.loads(vector["input_json"]) if "input_json" in vector else vector["input"]
    )
    if "error" in vector:
        with pytest.raises(canonical.CanonicalizationError) as excinfo:
            canonical.canonicalize(value)
        assert excinfo.value.reason == vector["error"]
        return
    out = canonical.canonicalize(value)
    assert out.decode("utf-8") == vector["canonical"]
    assert hashlib.sha256(out).hexdigest() == vector["sha256"]


# --- Independent assertions (not routed through gen_vectors.py) ------------------------------------


def canon(value: object) -> str:
    return canonical.canonical_string(value)


def test_object_keys_sort_by_utf16_code_unit_not_code_point():
    # U+FF3A (BMP) has a larger code point than the first UTF-16 unit of U+1D400 (a surrogate pair),
    # so by UTF-16 code units the astral key sorts FIRST -- the opposite of code-point ordering.
    assert (
        canon({FULLWIDTH_Z: 1, MATH_A: 2})
        == '{"' + MATH_A + '":2,"' + FULLWIDTH_Z + '":1}'
    )


def test_ascii_keys_sort_ascending():
    assert canon({"b": 1, "a": 2, "A": 3}) == '{"A":3,"a":2,"b":1}'


def test_array_order_is_preserved():
    assert canon([3, 1, 2]) == "[3,1,2]"


def test_short_form_control_escapes():
    assert (
        canon("".join(map(chr, (0x08, 0x09, 0x0A, 0x0C, 0x0D)))) == '"\\b\\t\\n\\f\\r"'
    )


def test_quote_and_backslash_are_escaped():
    assert canon('a"b\\c') == '"a\\"b\\\\c"'


def test_forward_slash_is_not_escaped():
    assert canon("a/b") == '"a/b"'


def test_low_controls_without_short_form_use_lowercase_u_escape():
    assert canon(chr(0x00) + chr(0x1F)) == '"\\u0000\\u001f"'


def test_del_and_high_characters_are_not_escaped():
    assert canon("x" + DEL + "y") == '"x' + DEL + 'y"'


def test_nfc_normalisation_composes_combining_marks():
    # A decomposed input composes to the precomposed form, and both canonicalise identically.
    assert canon(E_ACUTE_NFD) == '"' + E_ACUTE + '"'
    assert canon(E_ACUTE_NFD) == canon(E_ACUTE)
    assert (
        len(E_ACUTE_NFD) == 2 and len(E_ACUTE) == 1
    )  # guard: the two forms are genuinely different


def test_integers_are_exact_decimal():
    assert canon(9007199254740991) == "9007199254740991"
    assert canon(-42) == "-42"


def test_float_is_refused():
    with pytest.raises(canonical.CanonicalizationError) as excinfo:
        canonical.canonicalize(1.5)
    assert excinfo.value.reason == "non_integer_number"


def test_integer_beyond_the_exactly_representable_range_is_refused():
    for value in (2**53, -(2**53), 10000000000000001, 10**30):
        with pytest.raises(canonical.CanonicalizationError) as excinfo:
            canonical.canonicalize(value)
        assert excinfo.value.reason == "integer_out_of_range"


def test_whole_number_floats_are_refused_not_collapsed():
    for text in ("1.0", "1e2", "1E2", "-0.0"):
        with pytest.raises(canonical.CanonicalizationError) as excinfo:
            canonical.canonicalize(json.loads(text))
        assert excinfo.value.reason == "non_integer_number"


def test_duplicate_key_after_nfc_is_refused():
    with pytest.raises(canonical.CanonicalizationError) as excinfo:
        canonical.canonicalize({E_ACUTE: 1, E_ACUTE_NFD: 2})
    assert excinfo.value.reason == "duplicate_key_after_nfc"


def test_true_is_not_treated_as_one():
    assert canon(True) == "true"
    assert canon(1) == "1"


def test_canonicalisation_is_idempotent_and_deterministic():
    value = {"z": [1, 2], "a": {C_ACUTE_NFD: E_ACUTE_NFD}}
    once = canonical.canonicalize(value)
    again = canonical.canonicalize(json.loads(once.decode("utf-8")))
    assert once == again
