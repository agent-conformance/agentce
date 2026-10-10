"""Shared producers for the TypeScript and Java engines' parity goldens (SPEC §6.3, §6.5, §6.6, §9.6).

Each engine's ``generate_goldens.py`` builds one map, golden file name -> the exact text to commit, from the
live Python reference engine, and hands it to :func:`write_or_check`. The producers here cover the golden sets
both engines share (the graph triple dump, applicability, coverage, integrity and the state directory); each
engine serialises JSON in its own committed style through the ``dump`` it passes in. ``--check`` compares the
map with the committed files byte for byte and writes nothing; VG-ENGINE-GOLDENS-FRESH runs it for both engines.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentce.applicability import ControlMeta, resolve
from agentce.coverage import compute_coverage
from agentce.domain import DomainBinding
from agentce.graph import build_graph
from agentce.integrity import verify_bundle
from agentce.profile import Profile
from agentce.state import StateDir

Dump = Callable[[object], str]

#: The state golden's inputs (``state-fixture.json``), copied into the golden so the engines' tests read one file.
STATE_INPUTS = (
    "manifest_bytes",
    "bundle_a",
    "bundle_b",
    "window_a",
    "window_b",
    "events_b",
)


def _load(testdata: Path, name: str) -> Any:
    return json.loads((testdata / name).read_text(encoding="utf-8"))


def graph_golden(testdata: Path) -> str:
    """Every edge, literal and class-closure row of the graph built from ``graph-fixture.json``, one per
    line, sorted by UTF-8 bytes: the format of the ports' ``dumpTriples()``."""
    fixture = _load(testdata, "graph-fixture.json")
    conn = build_graph(
        fixture["events"], domain=DomainBinding.from_dict(fixture["domain"])
    ).conn
    lines = [
        f"E\t{s}\t{p}\t{o}" for s, p, o in conn.execute("SELECT s, p, o FROM edges")
    ]
    lines += [
        f"L\t{s}\t{p}\t{val}\t{datatype}"
        for s, p, val, datatype in conn.execute(
            "SELECT s, p, val, datatype FROM literals"
        )
    ]
    lines += [
        f"C\t{descendant}\t{ancestor}"
        for descendant, ancestor in conn.execute(
            "SELECT descendant, ancestor FROM class_closure"
        )
    ]
    return "\n".join(sorted(lines, key=lambda line: line.encode("utf-8"))) + "\n"


def applicability_golden(testdata: Path) -> list[dict[str, Any]]:
    fixture = _load(testdata, "applicability-fixture.json")
    controls = [
        ControlMeta(
            id=c["id"],
            applies_to_roles=c.get("appliesToRoles", []),
            family=c.get("family", ""),
        )
        for c in fixture["controls"]
    ]
    return resolve(Profile.from_dict(fixture["profile"]), fixture["events"], controls)


def coverage_golden(testdata: Path) -> dict[str, Any]:
    fixture = _load(testdata, "coverage-fixture.json")
    return compute_coverage(
        fixture["events"],
        Profile.from_dict(fixture["profile"]),
        testdata / "coverage-bundle",
    )


def integrity_golden(testdata: Path) -> list[dict[str, Any]]:
    fixture = _load(testdata, "integrity-fixture.json")
    return [
        result.to_json()
        for result in verify_bundle(fixture["events"], fixture["manifest"])
    ]


def state_golden(testdata: Path) -> dict[str, Any]:
    """Record a report, then plan a re-assessment against a changed bundle with a late event."""
    inputs = _load(testdata, "state-fixture.json")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = root / "manifest.json"
        manifest.write_text(inputs["manifest_bytes"], encoding="utf-8")
        state = StateDir.load(root / "state")
        digest = state.record(inputs["bundle_a"], manifest, inputs["window_a"])
        state_json = (root / "state" / "state.json").read_text(encoding="utf-8")
        supersedes, late = StateDir.load(root / "state").plan(
            inputs["bundle_b"], inputs["events_b"], inputs["window_b"]
        )
    return {
        **{key: inputs[key] for key in STATE_INPUTS},
        "manifest_digest": digest,
        "state_json_after_record": state_json,
        "supersedes": supersedes,
        "late_events": late,
    }


def shared_goldens(testdata: Path, dump: Dump) -> dict[str, str]:
    return {
        "graph-golden.txt": graph_golden(testdata),
        "applicability-golden.json": dump(applicability_golden(testdata)),
        "coverage-golden.json": dump(coverage_golden(testdata)),
        "integrity-golden.json": dump(integrity_golden(testdata)),
        "state-golden.json": dump(state_golden(testdata)),
    }


def stale(testdata: Path, goldens: dict[str, str]) -> list[str]:
    """Each golden whose committed bytes differ from ``goldens`` (or is missing), and each committed
    ``*-golden.*`` file no producer writes."""
    problems = [
        f"{name}: {'missing' if not (testdata / name).is_file() else 'differs from the live Python engine'}"
        for name, text in sorted(goldens.items())
        if not (testdata / name).is_file()
        or (testdata / name).read_text(encoding="utf-8") != text
    ]
    problems += [
        f"{path.name}: committed but no generator produces it"
        for path in sorted(testdata.glob("*-golden.*"))
        if path.name not in goldens
    ]
    return problems


def write_or_check(testdata: Path, goldens: dict[str, str], argv: list[str]) -> int:
    """No argument: write every golden. ``--check``: compare, write nothing, exit 1 on any problem."""
    if argv not in ([], ["--check"]):
        print(f"usage: generate_goldens.py [--check] (got {argv!r})", file=sys.stderr)
        return 2
    if argv:
        problems = stale(testdata, goldens)
        for problem in problems:
            print(f"STALE {testdata.name}/{problem}", file=sys.stderr)
        return 1 if problems else 0
    for name, text in goldens.items():
        (testdata / name).write_text(text, encoding="utf-8")
        print(f"wrote {testdata / name}")
    return 0
