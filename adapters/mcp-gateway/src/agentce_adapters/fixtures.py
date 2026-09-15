"""Discover and run the adapter's ``fixtures/`` (SPEC 12.3).

Each fixture is a directory under ``fixtures/`` holding:

* ``input.jsonl`` -- a native MCP gateway log (JSON Lines, one interaction per line);
* ``adapt.json`` -- the deployment context ``adapt`` is called with (``subject``, ``source_class``,
  and an optional ``source``);
* ``expected.jsonl`` -- the canonical AgentCE events the log must map to, one JSON object per line;
* ``expected-manifest.json`` -- the source manifest the gateway log must produce (SPEC 6.5, 6.6).

Both ``check_support_matrix`` and the test-suite load fixtures through this module so they agree on
exactly what "run the adapter over the fixtures" means.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mcp_gateway import AdaptResult, adapt


@dataclass(frozen=True)
class Fixture:
    """One native-log fixture with its expected canonical events and source manifest."""

    name: str
    path: Path
    input_bytes: bytes
    adapt_kwargs: dict[str, Any]
    expected: list[dict[str, Any]]
    expected_manifest: dict[str, Any]

    def run(self) -> AdaptResult:
        """Adapt this fixture's native log with its recorded deployment context."""
        return adapt(self.input_bytes, **self.adapt_kwargs)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_fixture(directory: Path) -> Fixture:
    """Load one fixture directory."""
    manifest_path = directory / "expected-manifest.json"
    expected_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    return Fixture(
        name=directory.name,
        path=directory,
        input_bytes=(directory / "input.jsonl").read_bytes(),
        adapt_kwargs=json.loads((directory / "adapt.json").read_text(encoding="utf-8")),
        expected=_read_jsonl(directory / "expected.jsonl"),
        expected_manifest=expected_manifest,
    )


def discover_fixtures(fixtures_dir: Path) -> list[Fixture]:
    """Load every fixture under ``fixtures_dir``, sorted by name for deterministic iteration."""
    directories = sorted(
        child
        for child in fixtures_dir.iterdir()
        if child.is_dir() and (child / "input.jsonl").exists()
    )
    return [load_fixture(directory) for directory in directories]
