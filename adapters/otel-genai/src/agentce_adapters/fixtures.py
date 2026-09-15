"""Discover and run the adapter's ``fixtures/`` (SPEC 12.3).

Each fixture is a directory under ``fixtures/`` holding three files:

* ``input.json`` -- a native OTLP/JSON GenAI or OpenInference trace export;
* ``adapt.json`` -- the deployment context ``adapt`` is called with (``subject``, ``source_class``,
  and an optional ``source``), the properties a collector supplies rather than the export;
* ``expected.jsonl`` -- the canonical AgentCE events the export must map to, one JSON object per line.

Both ``check_support_matrix`` and the test-suite load fixtures through this module so they agree on
exactly what "run the adapter over the fixtures" means.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .otel_genai import AdaptResult, adapt


@dataclass(frozen=True)
class Fixture:
    """One native-export fixture and its expected canonical events."""

    name: str
    path: Path
    input_bytes: bytes
    adapt_kwargs: dict[str, Any]
    expected: list[dict[str, Any]]

    def run(self) -> AdaptResult:
        """Adapt this fixture's native export with its recorded deployment context."""
        return adapt(self.input_bytes, **self.adapt_kwargs)


def load_fixture(directory: Path) -> Fixture:
    """Load one fixture directory."""
    input_bytes = (directory / "input.json").read_bytes()
    adapt_kwargs = json.loads((directory / "adapt.json").read_text(encoding="utf-8"))
    expected = [
        json.loads(line)
        for line in (directory / "expected.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    return Fixture(
        name=directory.name,
        path=directory,
        input_bytes=input_bytes,
        adapt_kwargs=adapt_kwargs,
        expected=expected,
    )


def discover_fixtures(fixtures_dir: Path) -> list[Fixture]:
    """Load every fixture under ``fixtures_dir``, sorted by name for deterministic iteration."""
    directories = sorted(
        child
        for child in fixtures_dir.iterdir()
        if child.is_dir() and (child / "input.json").exists()
    )
    return [load_fixture(directory) for directory in directories]
