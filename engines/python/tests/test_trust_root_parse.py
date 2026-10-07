"""Trust-root parse parity (18.81): ``load_trust_root`` parses with ``parse_untrusted_json``, the rule
TypeScript's ``loadTrustRoot`` and Java's ``Verify.loadTrustRoot`` share, so a trust root nested past
``MAX_JSON_DEPTH`` gets one fixed text and the three engines accept the same files.

TypeScript (``src/trustRootParse.test.ts``) and Java (``TrustRootParseTest``) pin the same behaviour;
``VG-TRUST-ROOT-PARSE-PARITY`` runs all three.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentce import signing

TOO_DEEP = "is not readable JSON: it nests containers more than 1000 levels deep"


def _refusal(tmp_path: Path, name: str, raw: bytes) -> str:
    path = tmp_path / f"{name}.json"
    path.write_bytes(raw)
    with pytest.raises(signing.VerificationError) as caught:
        signing.load_trust_root(path)
    return str(caught.value).replace(str(path), "<path>")


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("deep-array-10000", b"[" * 10000 + b"]" * 10000),
        ("deep-object-10000", b'{"a":' * 10000 + b"1" + b"}" * 10000),
        ("keys-1001", b'{"keys":' + b"[" * 1000 + b"]" * 1000 + b"}"),
        ("truncated-1001", b"[" * 1001),
    ],
)
def test_past_the_limit_is_one_fixed_text(
    tmp_path: Path, name: str, raw: bytes
) -> None:
    assert _refusal(tmp_path, name, raw) == f"<path> {TOO_DEEP}"


def test_at_the_limit_reaches_the_shape_stage(tmp_path: Path) -> None:
    raw = b'{"keys":' + b"[" * 999 + b"]" * 999 + b"}"
    assert _refusal(tmp_path, "keys-999", raw) == (
        "<path> is not a usable trust root: keys is not a mapping of key id to key entry"
    )


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("nan", b'{"keys":{},"x":NaN}'),
        ("lone-surrogate", b'{"keys":{},"x":"\\ud800"}'),
        ("invalid-utf8", b'{"keys":{},"x":"\xff"}'),
        ("bom", b'\xef\xbb\xbf{"keys":{}}'),
        ("not-json", b"{not json"),
    ],
)
def test_other_parse_refusals_name_the_file(
    tmp_path: Path, name: str, raw: bytes
) -> None:
    text = _refusal(tmp_path, name, raw)
    assert text.startswith("<path> is not readable JSON"), text
    assert "not readable JSON: not readable JSON" not in text
    assert "1000 levels" not in text


def test_a_lone_surrogate_has_no_parser_suffix(tmp_path: Path) -> None:
    assert (
        _refusal(tmp_path, "s", b'{"keys":{},"x":"\\ud800"}')
        == "<path> is not readable JSON"
    )
