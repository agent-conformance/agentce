"""The late-event / supersession gate (SPEC §5.4 B7, §9.6, HR-10, P3.6).

Builds a minimal two-assessment scenario against a shared state directory: a first bundle over an
observation window, then the same window with one late-arriving event appended (a changed bundle
digest). The engine re-assesses and the second report must list the first in ``supersedes`` — a window
is never silently closed. ``late_event.py --json`` prints ``{"supersedes_produced": <bool>, …}``, the
interface the phase-3 P3.6 check reads. Everything is built in a temporary directory; nothing is
committed.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any

from agentce.cli import main as agentce_main

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
CATALOG_ID = "eu-ai-act@2026.09"
CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"
SUBJECT = "spiffe://corp/agents/late-event-agent"
AGENT_SOURCE = "urn:agentce:source:late-agent:eu-1"
GATEWAY_SOURCE = "urn:agentce:source:late-gateway:eu-1"


def _event(
    eid: str, source: str, etype: str, sclass: str, time: str, data: dict[str, Any]
) -> dict:
    return {
        "specversion": "1.0",
        "id": eid,
        "source": source,
        "type": f"org.agent-conformance.evidence.{etype}.v1",
        "time": time,
        "subject": SUBJECT,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": sclass,
        "data": {"@context": CONTEXT, "@type": etype, **data},
    }


def _base_events() -> list[dict[str, Any]]:
    agent = {"id": SUBJECT, "name": "late-event-agent"}
    return [
        _event(
            "dec1",
            AGENT_SOURCE,
            "Decision",
            "self_report",
            "2026-05-02T09:00:00.000Z",
            {
                "decision_type": "dom:CreditDecision",
                "oversight_modality": "review_before",
                "affects_natural_person": True,
                "legal_or_significant_effect": True,
                "agent": agent,
            },
        ),
        _event(
            "tc1",
            GATEWAY_SOURCE,
            "ToolCall",
            "enforcement_point",
            "2026-05-02T09:00:01.000Z",
            {
                "agent": agent,
                "tool": {"name": "x", "protocol": "mcp"},
                "side_effect": "read",
            },
        ),
    ]


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in events
    )
    path.write_text(body, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_bundle(bundle: Path, events: list[dict[str, Any]]) -> None:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_source.setdefault(str(event["source"]), []).append(event)
    files = []
    for source in sorted(by_source):
        rel = f"events/{source.replace(':', '-').replace('/', '-')}.jsonl"
        files.append(
            {"path": rel, "sha256": _write_jsonl(bundle / rel, by_source[source])}
        )
    manifest = {
        "agentce_bundle_version": 1,
        "domain": "credit",
        "sources": [{"id": s} for s in sorted(by_source)],
        "files": sorted(files, key=lambda f: f["path"]),
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _write_profile(path: Path) -> None:
    lines = [
        "profile_version: 1",
        "observation_window:",
        '  start: "2026-05-01T00:00:00Z"',
        '  end: "2026-08-29T00:00:00Z"',
        "catalogs:",
        f'  - "{CATALOG_ID}"',
        "subjects:",
        f'  - id: "{SUBJECT}"',
        '    role: "both"',
        "    declared_decision_types:",
        '      - "dom:CreditDecision"',
        "    declared_oversight:",
        '      "dom:CreditDecision": "review_before"',
        "    evidence_sources:",
        '      - adapter: "agent"',
        f'        source: "{AGENT_SOURCE}"',
        '        class: "self_report"',
        '      - adapter: "gateway"',
        f'        source: "{GATEWAY_SOURCE}"',
        '        class: "enforcement_point"',
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_domain(path: Path) -> None:
    path.write_text(
        "decision_types:\n"
        '  - id: "dom:CreditDecision"\n'
        '    subclass_of: "agentce:ConsequentialDecision"\n'
        "    consequential: true\n"
        '    required_oversight_modality: "review_before"\n',
        encoding="utf-8",
    )


def _assess(bundle: Path, profile: Path, domain: Path, out: Path, state: Path) -> int:
    argv = [
        "assess",
        "--bundle",
        str(bundle),
        "--profile",
        str(profile),
        "--domain",
        str(domain),
        "--catalog",
        CATALOG_ID,
        "--catalog-dir",
        str(CATALOG_DIR),
        "--out",
        str(out),
        "--state",
        str(state),
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        return agentce_main(argv)


def compute_late_event() -> dict[str, Any]:
    """Assess a bundle, then the same window with a late event; report whether supersedes was produced."""
    work = Path(tempfile.mkdtemp(prefix="late-event-"))
    profile = work / "applicability.yaml"
    domain = work / "domain.linkml.yaml"
    _write_profile(profile)
    _write_domain(domain)
    state = work / "state"

    bundle_a = work / "bundle-a"
    _write_bundle(bundle_a, _base_events())
    _assess(bundle_a, profile, domain, work / "out-a", state)
    manifest_a = json.loads(
        (work / "out-a" / "manifest.json").read_text(encoding="utf-8")
    )

    # A late-arriving event inside the already-assessed window: a changed bundle (new digest).
    agent = {"id": SUBJECT, "name": "late-event-agent"}
    late = _event(
        "late1",
        GATEWAY_SOURCE,
        "ToolCall",
        "enforcement_point",
        "2026-05-02T09:00:02.000Z",
        {
            "agent": agent,
            "tool": {"name": "y", "protocol": "mcp"},
            "side_effect": "read",
        },
    )
    bundle_b = work / "bundle-b"
    _write_bundle(bundle_b, [*_base_events(), late])
    _assess(bundle_b, profile, domain, work / "out-b", state)
    manifest_b = json.loads(
        (work / "out-b" / "manifest.json").read_text(encoding="utf-8")
    )

    supersedes = manifest_b.get("supersedes", [])
    return {
        "supersedes_produced": len(supersedes) > 0,
        "supersedes": supersedes,
        "first_report_supersedes": manifest_a.get("supersedes", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="late_event",
        description="Late-event / supersession gate over a two-assessment scenario (P3.6).",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)
    result = compute_late_event()
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"supersedes_produced={result['supersedes_produced']} "
            f"supersedes={result['supersedes']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
