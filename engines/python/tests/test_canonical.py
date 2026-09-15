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
    if "error" in vector:
        with pytest.raises(canonical.CanonicalizationError) as excinfo:
            canonical.canonical_string(vector["input"])
        assert excinfo.value.reason == vector["error"]
    else:
        assert canonical.canonical_string(vector["input"]) == vector["canonical"]
        assert canonical.sha256_hex(vector["input"]) == vector["sha256"]
