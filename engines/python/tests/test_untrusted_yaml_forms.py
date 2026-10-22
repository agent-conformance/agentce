"""The untrusted YAML loaders read odd YAML as the TypeScript and Java readers do (18.184p, census odd-inputs lines
95, 101 and 103): no SafeLoader-only tag in any file, one Unicode 15.0 digit set on every Python, and the last of two
merges wins where repeated keys are allowed."""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

import pytest
import yaml

from agentce.safe_yaml import (
    UNICODE_15_DIGITS,
    _PermissiveTimestampSafeLoader,
    _UniqueKeySafeLoader,
)

_LOADERS = [_PermissiveTimestampSafeLoader, _UniqueKeySafeLoader]

_REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("loader", _LOADERS)
@pytest.mark.parametrize(
    "text",
    [
        "a: !!set {x: null}\n",
        "a: !!binary aGVsbG8=\n",
        "a: !!omap [b: 1]\n",
        "a: !!pairs [b: 1]\n",
        "a: {<<: !!set {x: null}}\n",
        "a: {<<: [{b: 1}, !!set {x: null}]}\n",
    ],
)
def test_a_safe_loader_only_tag_is_refused_in_every_file(
    loader: type[yaml.SafeLoader], text: str
) -> None:
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load(text, Loader=loader)


@pytest.mark.parametrize("loader", _LOADERS)
@pytest.mark.parametrize(
    ("text", "want"),
    [
        ('a: !!null ""\n', None),
        ('a: !!float "10"\n', 10.0),
        ('a: !!int "10"\n', 10),
        ('a: !!bool "yes"\n', True),
        ("a: !!str 10\n", "10"),
        ('a: !!int "١٠"\n', 10),
    ],
)
def test_a_core_tag_reads_as_safe_loader_reads_it(
    loader: type[yaml.SafeLoader], text: str, want: object
) -> None:
    assert yaml.load(text, Loader=loader) == {"a": want}


@pytest.mark.parametrize("loader", _LOADERS)
@pytest.mark.parametrize(
    "text", ['a: !!int "𐵁𐵀"\n', 'a: !!float "𐵁.5"\n', 'a: !!int "𖵱"\n']
)
def test_a_digit_newer_than_unicode_15_is_refused_on_every_python(
    loader: type[yaml.SafeLoader], text: str
) -> None:
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load(text, Loader=loader)


def test_two_merges_read_last_wins_where_repeated_keys_are_allowed() -> None:
    text = "{<<: {a: 1}, <<: {a: 2}}\n"
    assert yaml.load(text, Loader=_PermissiveTimestampSafeLoader) == {"a": 2}
    with pytest.raises(yaml.constructor.ConstructorError, match="'<<' twice"):
        yaml.load(text, Loader=_UniqueKeySafeLoader)


def _decimal_ranges() -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for cp in range(sys.maxunicode + 1):
        if unicodedata.decimal(chr(cp), None) is None:
            continue
        if ranges and ranges[-1][1] == cp - 1:
            ranges[-1] = (ranges[-1][0], cp)
        else:
            ranges.append((cp, cp))
    return ranges


def test_the_digit_table_is_unicode_15() -> None:
    """Python 3.12's own table is the source; a newer Python only adds digits to it."""
    if unicodedata.unidata_version == "15.0.0":
        assert list(UNICODE_15_DIGITS) == _decimal_ranges()
    else:
        assert all(
            unicodedata.decimal(chr(cp), None) is not None
            for start, end in UNICODE_15_DIGITS
            for cp in range(start, end + 1)
        )


@pytest.mark.parametrize(
    "source",
    [
        "engines/typescript/src/readiness.ts",
        "engines/java/src/main/java/org/agentce/Readiness.java",
    ],
)
def test_the_ports_carry_the_same_digit_table(source: str) -> None:
    text = (_REPO / source).read_text(encoding="utf-8")
    block = text[text.index("UNICODE_15_DIGITS") :]
    block = block[: block.index(";")]
    found = [
        (int(a, 16), int(b, 16))
        for a, b in re.findall(r"0x([0-9A-Fa-f]+),\s*0x([0-9A-Fa-f]+)", block)
    ]
    assert found == list(UNICODE_15_DIGITS)
