"""The error-key registry, its loader, the trust-root shape causes and the digest-read key (18.80).

TypeScript (``src/errorCatalogue.test.ts``) and Java (``ErrorCatalogueTest``) pin the same behaviour;
``VG-I18N-ERROR-CATALOGUE`` runs all three.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentce import bundled, i18n_format, signing
from agentce.commands import _digest_of
from agentce.error_catalogue import MESSAGE_KEYS
from agentce.errors import InputError

_PACKAGE = Path(__file__).resolve().parents[1] / "agentce"
# A literal key passed as the first argument of an error constructor. Keys composed at run time
# (``f"input.{key}_not_a_file"``) carry braces and are not matched, as in the other engines.
_RAISED = re.compile(r'(?:InputError|AgentceError|UnreadableError)\(\s*"([^"{}]+)"')


def test_every_literal_raised_key_is_in_the_catalogue() -> None:
    raised = {
        match.group(1)
        for path in _PACKAGE.rglob("*.py")
        for match in _RAISED.finditer(path.read_text("utf-8"))
    }
    assert raised, "the scan found no raise sites"
    assert sorted(raised - set(MESSAGE_KEYS)) == []


@pytest.mark.parametrize(
    ("body", "error"),
    [("{not json", json.JSONDecodeError), ("[1, 2]", AttributeError)],
)
def test_load_catalog_refuses_a_corrupt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, error: type
) -> None:
    (tmp_path / "messages.en.json").write_text(body, "utf-8")
    monkeypatch.setattr(bundled, "i18n_dir", lambda: tmp_path)
    with pytest.raises(error):
        i18n_format.load_catalog("en")


def test_load_catalog_missing_language_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bundled, "i18n_dir", lambda: tmp_path)
    assert i18n_format.load_catalog("en") == {}


@pytest.mark.parametrize(
    ("data", "tail"),
    [
        ({"keys": "x"}, "keys is not a mapping of key id to key entry"),
        ({"keys": {"abc": "s"}}, "keys entry 'abc' is not a mapping"),
        ({"keys": {"abc": {"identity": "x"}}}, "keys entry 'abc' has no public_key"),
        (
            {"certificate_authorities": "x"},
            "certificate_authorities is not a mapping of id to entry",
        ),
        (
            {"certificate_authorities": {"ca1": "x"}},
            "certificate_authorities entry 'ca1' is not a mapping",
        ),
        (
            {"certificate_authorities": {"ca1": {}}},
            "certificate_authorities entry 'ca1' has no public_key",
        ),
        ({"keys": {"a'b\nc": "x"}}, 'keys entry "a\'b\\nc" is not a mapping'),
    ],
)
def test_trust_root_shape_causes(tmp_path: Path, data: object, tail: str) -> None:
    path = tmp_path / "root.json"
    path.write_text(json.dumps(data), "utf-8")
    with pytest.raises(signing.VerificationError) as caught:
        signing.load_trust_root(path)
    assert str(caught.value) == f"{path} is not a usable trust root: {tail}"


def test_empty_trust_root_mappings_are_no_entries() -> None:
    root = signing.TrustRoot.from_dict({"keys": {}, "certificate_authorities": None})
    assert root.keys == {} and root.authorities == {}


def test_digest_read_failure_is_keyed(tmp_path: Path) -> None:
    with pytest.raises(InputError) as caught:
        _digest_of(tmp_path)  # a folder: the read fails with an OSError
    assert caught.value.key == "input.digest_unreadable"
    assert caught.value.cause.startswith(
        f"{tmp_path} could not be read to record its digest: "
    )
    assert caught.value.fix == "make the file readable, then re-run."
