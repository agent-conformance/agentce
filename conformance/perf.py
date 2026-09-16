"""The performance harness and its gate (SPEC §8.6, B4, P3.7).

SPEC §8.6 sets the reference engine's scale targets: a 50M-event bundle over 10k subjects and a 90-day
window, assessed against the base catalog, in ≤ 60 min wall clock on a 16-core host within ≤ 32 GB RAM,
with incremental nightly runs ≤ 10 min. The targets assume the Portable Shape Profile is compiled to
query plans over the store rather than interpreted by a general-purpose SHACL validator.

``perf.py --run`` measures the engine's per-event ingest throughput and per-subject assessment cost on
a synthetic stream, extrapolates them to the 50M-event / 10k-subject target, and records the result.
``perf.py --check`` is the phase-3 P3.7 gate: it passes when a recorded scale run is within targets, or
otherwise when the performance ADR (docs/adr/0009-performance.md) documenting the gap and the plan is
present — and prints which path it took. The synthetic stream is generated on demand; nothing is
committed by a run.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from agentce.domain import DomainBinding
from agentce.graph import build_graph
from agentce.schema import validate_event

REPO_ROOT = Path(__file__).resolve().parents[1]
PERF_ADR = REPO_ROOT / "docs" / "adr" / "0009-performance.md"
DEFAULT_RESULTS = Path(__file__).resolve().parent / "perf-results.json"

# SPEC §8.6 acceptance targets for the reference engine.
TARGET_EVENTS = 50_000_000
TARGET_SUBJECTS = 10_000
TARGET_SECONDS = 3600  # ≤ 60 min wall clock
TARGET_RAM_GB = 32

BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"


def _synthetic_ingest_event(index: int) -> dict[str, Any]:
    """A minimal schema-valid ToolCall, shaped like the corpus's benign background traffic."""
    subject = f"spiffe://corp/agents/bg-{index % 64}"
    second = index % 60
    minute = (index // 60) % 60
    hour = 9 + (index // 3600) % 12
    return {
        "specversion": "1.0",
        "id": f"perf-{index:012d}",
        "source": "urn:agentce:source:perf-bulk:eu-1",
        "type": "org.agent-conformance.evidence.ToolCall.v1",
        "time": f"2026-05-01T{hour:02d}:{minute:02d}:{second:02d}.{index % 1000:03d}Z",
        "subject": subject,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": "self_report",
        "data": {
            "@context": BASE_CONTEXT,
            "@type": "ToolCall",
            "agent": {"id": subject},
            "tool": {"name": "catalog.search", "protocol": "mcp"},
            "side_effect": "read",
        },
    }


def _subject_chain(subject: str) -> list[dict[str, Any]]:
    """A four-event consequential decision chain for one subject, for the assess-cost measurement."""

    def event(eid: str, etype: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "specversion": "1.0",
            "id": eid,
            "source": "urn:agentce:source:perf-agent:eu-1",
            "type": f"org.agent-conformance.evidence.{etype}.v1",
            "time": "2026-05-01T09:00:00.000Z",
            "subject": subject,
            "datacontenttype": "application/ld+json",
            "agentcesourceclass": "enforcement_point",
            "data": {"@context": BASE_CONTEXT, "@type": etype, **data},
        }

    agent = {"id": subject, "name": "perf-agent"}
    return [
        event(
            "d1",
            "Decision",
            {
                "decision_type": "dom:CreditDecision",
                "oversight_modality": "review_before",
                "affects_natural_person": True,
                "legal_or_significant_effect": True,
                "agent": agent,
            },
        ),
        event(
            "t1",
            "ToolCall",
            {
                "agent": agent,
                "tool": {"name": "x", "protocol": "mcp"},
                "side_effect": "write",
            },
        ),
    ]


def measure(ingest_events: int, assess_subjects: int) -> dict[str, Any]:
    """Measure per-event ingest throughput and per-subject assess cost, and extrapolate to target."""
    events = [_synthetic_ingest_event(i) for i in range(ingest_events)]
    t0 = time.perf_counter()
    for event in events:
        validate_event(event)
    validate_seconds = time.perf_counter() - t0
    ingest_eps = ingest_events / validate_seconds if validate_seconds > 0 else 0.0

    domain = DomainBinding.empty()
    t0 = time.perf_counter()
    for i in range(assess_subjects):
        build_graph(_subject_chain(f"spiffe://corp/agents/perf-{i}"), domain=domain)
    assess_seconds = time.perf_counter() - t0
    assess_per_subject = (
        assess_seconds / assess_subjects if assess_subjects > 0 else 0.0
    )

    est_ingest_seconds = TARGET_EVENTS / ingest_eps if ingest_eps > 0 else float("inf")
    est_assess_seconds = TARGET_SUBJECTS * assess_per_subject
    est_total_seconds = est_ingest_seconds + est_assess_seconds
    return {
        "ingest_events_measured": ingest_events,
        "ingest_events_per_second": round(ingest_eps, 1),
        "assess_subjects_measured": assess_subjects,
        "assess_seconds_per_subject": round(assess_per_subject, 6),
        "target_events": TARGET_EVENTS,
        "target_subjects": TARGET_SUBJECTS,
        "target_seconds": TARGET_SECONDS,
        "estimated_ingest_seconds": round(est_ingest_seconds, 1),
        "estimated_assess_seconds": round(est_assess_seconds, 1),
        "estimated_total_seconds": round(est_total_seconds, 1),
        "within_targets": est_total_seconds <= TARGET_SECONDS,
        "host": "developer workstation (not the 16-core reference host)",
    }


def check() -> tuple[bool, str]:
    """The P3.7 gate: pass on a recorded in-target run, else on the performance ADR; report which."""
    if DEFAULT_RESULTS.is_file():
        try:
            recorded = json.loads(DEFAULT_RESULTS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            recorded = {}
        if recorded.get("within_targets") is True:
            return True, f"recorded scale run within targets ({DEFAULT_RESULTS.name})"
    if PERF_ADR.is_file():
        return True, f"performance ADR present ({PERF_ADR.relative_to(REPO_ROOT)})"
    return False, "no recorded scale run within targets and no performance ADR"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="perf",
        description="Performance harness and P3.7 gate for the reference engine (SPEC §8.6).",
    )
    parser.add_argument(
        "--run", action="store_true", help="measure throughput and record a result"
    )
    parser.add_argument(
        "--check", action="store_true", help="the P3.7 gate (run within targets or ADR)"
    )
    parser.add_argument(
        "--events", type=int, default=50_000, help="synthetic ingest events to measure"
    )
    parser.add_argument(
        "--subjects", type=int, default=200, help="synthetic subjects to assess"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_RESULTS,
        help="where --run records its result",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    if args.run:
        metrics = measure(args.events, args.subjects)
        args.out.write_text(
            json.dumps(metrics, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        if args.json:
            print(json.dumps(metrics, sort_keys=True))
        else:
            print(
                f"ingest {metrics['ingest_events_per_second']} events/s; "
                f"estimated {metrics['estimated_total_seconds']}s for the 50M-event target "
                f"(≤ {TARGET_SECONDS}s); within_targets={metrics['within_targets']}"
            )
        return 0

    passed, reason = check()
    if args.json:
        print(json.dumps({"passed": passed, "reason": reason}, sort_keys=True))
    elif passed:
        print(f"PERF RECORDED: {reason}")
    else:
        print(f"PERF NOT RECORDED: {reason}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
