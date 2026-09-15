"""Shared test helpers: valid example events and a minimal evidence-bundle builder."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLES = _REPO_ROOT / "spec" / "model" / "examples" / "appendix-g"


def sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_example(name: str) -> dict[str, Any]:
    return json.loads((_EXAMPLES / name).read_text(encoding="utf-8"))


def write_bundle(
    root: Path,
    lines: Sequence[str],
    *,
    sources: Sequence[str] | None = None,
    write_manifest: bool = True,
) -> Path:
    """Write a bundle at ``root`` whose single events file carries ``lines`` (already serialised)."""
    (root / "events").mkdir(parents=True, exist_ok=True)
    events_file = root / "events" / "stream.jsonl"
    events_file.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    if write_manifest:
        manifest: dict[str, Any] = {
            "agentce_bundle_version": 1,
            "files": [
                {"path": "events/stream.jsonl", "sha256": sha256_hex(events_file)}
            ],
        }
        if sources is not None:
            manifest["sources"] = [{"id": src} for src in sources]
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture
def example_event() -> dict[str, Any]:
    return load_example("event-1.json")


@pytest.fixture
def example_events() -> list[dict[str, Any]]:
    return [load_example("event-1.json"), load_example("event-2.json")]


@pytest.fixture
def make_bundle(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that writes a valid bundle from event objects (or raw strings)."""

    def _make(items: Sequence[Any], *, sources: Sequence[str] | None = None) -> Path:
        lines = [item if isinstance(item, str) else json.dumps(item) for item in items]
        return write_bundle(tmp_path / "bundle", lines, sources=sources)

    return _make


@pytest.fixture
def clone() -> Callable[[dict[str, Any]], dict[str, Any]]:
    return copy.deepcopy
