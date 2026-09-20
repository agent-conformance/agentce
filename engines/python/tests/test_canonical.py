"""The engine's canonical form reproduces the shared test vectors byte for byte (SPEC §6.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentce import canonical

_VECTORS = sorted(
    (Path(__file__).resolve().parents[3] / "spec" / "model" / "test-vectors").glob(
        "*.json"
    )
)


def test_vectors_are_present() -> None:
    assert len(_VECTORS) >= 50


@pytest.mark.parametrize("vector_file", _VECTORS, ids=lambda p: p.stem)
def test_vector(vector_file: Path) -> None:
    vector = json.loads(vector_file.read_text(encoding="utf-8"))
    # A vector that carries ``input_json`` gives the input as JSON text, so a number token such as
    # ``1.0`` or ``1e2`` reaches the engine as written.
    value = (
        json.loads(vector["input_json"]) if "input_json" in vector else vector["input"]
    )
    if "error" in vector:
        with pytest.raises(canonical.CanonicalizationError) as excinfo:
            canonical.canonical_string(value)
        assert excinfo.value.reason == vector["error"]
    else:
        assert canonical.canonical_string(value) == vector["canonical"]
        assert canonical.sha256_hex(value) == vector["sha256"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("1.0", "non_integer_number"),
        ("1e2", "non_integer_number"),
        ("-0.0", "non_integer_number"),
        ("9007199254740992", "integer_out_of_range"),
        ("-9007199254740992", "integer_out_of_range"),
        ("10000000000000001", "integer_out_of_range"),
    ],
)
def test_number_tokens_outside_the_grammar_are_refused(text: str, reason: str) -> None:
    with pytest.raises(canonical.CanonicalizationError) as excinfo:
        canonical.canonical_string(json.loads(text))
    assert excinfo.value.reason == reason


def test_the_extreme_accepted_integers_are_exact() -> None:
    assert (
        canonical.canonical_string(json.loads("9007199254740991")) == "9007199254740991"
    )
    assert (
        canonical.canonical_string(json.loads("-9007199254740991"))
        == "-9007199254740991"
    )
    assert canonical.canonical_string(json.loads("-0")) == "0"
