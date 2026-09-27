#!/usr/bin/env python3
"""support_matrix_sync_check - every vendored event_producers.json matches the live adapters (RFC 0008
Sec.5, item 18.5).

Each engine vendors its own copy of a small, per-event table (`event_producers.json`) naming which
adapters produce that event and each adapter's default trust class (SPEC 12.3; the default itself was
reviewed adapter by adapter in item 18.1 and is unlikely to change casually, so it is a fact this
script carries directly, not derived). `agentce.blind_spots` reads that table to derive a missing
requirement's evidence-ladder rung and which adapters could supply it. This check parses every
`adapters/*/support-matrix.yaml` directly and fails when an adapter declares an event no vendored copy
knows, or a vendored copy names an event no adapter declares -- so a real adapter change is caught
instead of silently drifting from what the report tells a reader to go instrument.

    support_matrix_sync_check.py             check every vendored event_producers.json found under
                                              engines/*/ against the live adapters/*/support-matrix.yaml
                                              files; exit 1 on any drift
    support_matrix_sync_check.py --self-test prove the checker catches a live-only event and a
                                              vendored-only event
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: Each adapter's default trust class for the events it declares (SPEC 12.3; item 18.1 reviewed each
#: one) -- support-matrix.yaml lists member paths, never a default trust class, so this table cannot
#: be derived from it alone. Update this by hand the day a real adapter's default changes.
_ADAPTER_DEFAULT_CLASS = {
    "otel-genai": "self_report",
    "mcp-gateway": "enforcement_point",
    "policy-engines": "enforcement_point",
    "oversight": "self_report",
    "supply-chain": "enforcement_point",
    "identity": "enforcement_point",
}


def live_event_producers(
    adapters_dir: Path,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """The ``event -> {adapter: default_class}`` table the live support-matrix.yaml files declare,
    plus any adapter this checker has no reviewed default class for (never silently dropped: a new
    adapter must be added to ``_ADAPTER_DEFAULT_CLASS`` before it can be checked at all)."""
    producers: dict[str, dict[str, str]] = {}
    unreviewed: list[str] = []
    for matrix_path in sorted(adapters_dir.glob("*/support-matrix.yaml")):
        data = yaml.safe_load(matrix_path.read_text(encoding="utf-8")) or {}
        adapter = str(data.get("adapter", matrix_path.parent.name))
        default_class = _ADAPTER_DEFAULT_CLASS.get(adapter)
        if default_class is None:
            unreviewed.append(adapter)
            continue
        for event in data.get("events") or {}:
            producers.setdefault(str(event), {})[adapter] = default_class
    return producers, sorted(set(unreviewed))


def vendored_event_producers_files(repo_root: Path) -> list[Path]:
    """Every vendored copy an engine ships, wherever it lives -- Python, TypeScript, and Java each
    keep their own under ``engines/<language>/.../support_matrices/event_producers.json``."""
    return sorted(repo_root.glob("engines/*/**/event_producers.json"))


def compare(
    live: dict[str, dict[str, str]], vendored: dict[str, Any], *, label: str
) -> list[str]:
    problems: list[str] = []
    live_events, vendored_events = set(live), set(vendored)
    for event in sorted(live_events - vendored_events):
        problems.append(
            f"{label}: adapter(s) declare {event!r} but it is missing from the vendored table"
        )
    for event in sorted(vendored_events - live_events):
        problems.append(
            f"{label}: the vendored table names {event!r} but no adapter declares it"
        )
    for event in sorted(live_events & vendored_events):
        live_adapters = live[event]
        vendored_adapters = {str(a): str(c) for a, c in vendored[event].items()}
        if live_adapters != vendored_adapters:
            problems.append(
                f"{label}: {event!r} live={live_adapters} vendored={vendored_adapters}"
            )
    return problems


def check(repo_root: Path = ROOT) -> list[str]:
    adapters_dir = repo_root / "adapters"
    live, unreviewed = live_event_producers(adapters_dir)
    problems = [
        f"adapter {adapter!r} has no reviewed default trust class in "
        "_ADAPTER_DEFAULT_CLASS -- add it before this check can cover it"
        for adapter in unreviewed
    ]
    files = vendored_event_producers_files(repo_root)
    if not files:
        return [*problems, "no vendored event_producers.json found under engines/*/"]
    for path in files:
        vendored = json.loads(path.read_text(encoding="utf-8"))
        problems += compare(live, vendored, label=str(path.relative_to(repo_root)))
    return problems


def run_check() -> int:
    problems = check()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            f"SUPPORT MATRIX SYNC INCONSISTENT: {len(problems)} problem(s)", file=sys.stderr
        )
        return 1
    print("SUPPORT MATRIX SYNC OK")
    return 0


def _write_matrix(path: Path, events: dict[str, list[str]]) -> None:
    lines = ["adapter: otel-genai", "events:"]
    for event, members in events.items():
        lines.append(f"  {event}:")
        lines.append(f"    members: {members}")
        lines.append("    missing: []")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def self_test() -> int:
    """Prove the checker discriminates: a clean fixture passes; an adapter declaring an event no
    vendored copy knows, and a vendored copy naming an event no adapter declares, are each caught."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        adapter_dir = root / "adapters" / "otel-genai"
        adapter_dir.mkdir(parents=True)
        matrix_path = adapter_dir / "support-matrix.yaml"
        _write_matrix(matrix_path, {"ModelCall": ["agent.id"]})

        vendor_dir = root / "engines" / "python" / "agentce" / "data" / "support_matrices"
        vendor_dir.mkdir(parents=True)
        vendor_path = vendor_dir / "event_producers.json"
        vendor_path.write_text(
            json.dumps({"ModelCall": {"otel-genai": "self_report"}}), encoding="utf-8"
        )

        problems = check(root)
        if problems:
            failures.append(f"good: expected no problems, got {problems}")

        _write_matrix(matrix_path, {"ModelCall": ["agent.id"], "ToolCall": ["agent.id"]})
        problems = check(root)
        if not any(
            "ToolCall" in p and "missing from the vendored" in p for p in problems
        ):
            failures.append(f"missing-from-vendored: expected a ToolCall problem, got {problems}")

        _write_matrix(matrix_path, {"ModelCall": ["agent.id"]})
        vendor_path.write_text(
            json.dumps(
                {
                    "ModelCall": {"otel-genai": "self_report"},
                    "PhantomEvent": {"otel-genai": "self_report"},
                }
            ),
            encoding="utf-8",
        )
        problems = check(root)
        if not any(
            "PhantomEvent" in p and "no adapter declares it" in p for p in problems
        ):
            failures.append(f"phantom-event: expected a PhantomEvent problem, got {problems}")

    if failures:
        print("SUPPORT MATRIX SYNC-CHECK SELF-TEST FAILED:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(
        "SUPPORT MATRIX SYNC-CHECK SELF-TEST PASSED -- catches a live-only event and a "
        "vendored-only event."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_check()


if __name__ == "__main__":
    raise SystemExit(main())
