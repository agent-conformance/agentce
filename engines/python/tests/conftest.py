"""Shared test helpers: valid example events and a minimal evidence-bundle builder."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
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
    source_classes: Mapping[str, Any] | None = None,
    write_manifest: bool = True,
) -> Path:
    """Write a bundle at ``root`` whose single events file carries ``lines`` (already serialised).

    ``sources`` lists source ids with no declared class; ``source_classes`` maps a source id to the
    trust class its manifest entry declares (for the ``class_mismatch`` check, SPEC §6.4). A caller
    testing a malformed manifest may pass a non-``str`` value (``None``, a number, ...): the manifest
    is JSON, so nothing here enforces the annotation at runtime.
    """
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
        entries = [{"id": src} for src in (sources or [])]
        entries += [
            {"id": src, "class": cls} for src, cls in (source_classes or {}).items()
        ]
        if entries:
            manifest["sources"] = entries
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@contextmanager
def unwritable_dir(path: Path) -> Iterator[Path]:
    """Creates ``path`` mode 0o555 and yields it; skips the test if this user can still write to a
    mode-555 directory (e.g. running as root, where there is nothing to prove). Always restores
    0o755 afterward so `tmp_path`'s own cleanup can remove it."""
    path.mkdir(mode=0o555)
    if os.access(path, os.W_OK):
        pytest.skip("this user can write to a mode-555 directory")
    try:
        yield path
    finally:
        path.chmod(0o755)


@pytest.fixture(autouse=True)
def _no_ci_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`assess --for`'s CI auto-detection (contracts/P18-18.7.md) reads the real `CI` environment
    variable; GitHub Actions sets `CI=true` for every job, which would otherwise make every
    no-`--emit` test in this suite pick up an extra `report.junit.xml` under real CI. The handful of
    tests that exercise CI detection on purpose set `CI` themselves, after this fixture has run.

    `verify --report`'s step 9 (`commands/__init__.py`'s `_verify_report`) also reads the real
    `GITHUB_STEP_SUMMARY` environment variable, which GitHub Actions sets for every job to a real
    file path; left ambient, every test here except the one that sets it on purpose
    (`test_verify_report_happy_path_reproduces`, 18.8.R1) would exercise only the "already set"
    branch under CI and only the "unset" branch locally, so the branch-coverage gate
    (`VG-REPORT-BRANCH-COVERAGE`) passed locally (`FAIL: _verify_report is not at 100% branch
    coverage -- missing branches [[892, 895]]` reproduced it on CI, 18.8.R1). Unset here, like `CI`
    above, so both branches are covered deterministically regardless of which environment runs the
    suite."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.fixture
def example_event() -> dict[str, Any]:
    return load_example("event-1.json")


@pytest.fixture
def example_events() -> list[dict[str, Any]]:
    return [load_example("event-1.json"), load_example("event-2.json")]


@pytest.fixture
def make_bundle(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that writes a valid bundle from event objects (or raw strings)."""

    def _make(
        items: Sequence[Any],
        *,
        sources: Sequence[str] | None = None,
        source_classes: Mapping[str, str] | None = None,
    ) -> Path:
        lines = [item if isinstance(item, str) else json.dumps(item) for item in items]
        return write_bundle(
            tmp_path / "bundle", lines, sources=sources, source_classes=source_classes
        )

    return _make


@pytest.fixture
def clone() -> Callable[[dict[str, Any]], dict[str, Any]]:
    return copy.deepcopy
