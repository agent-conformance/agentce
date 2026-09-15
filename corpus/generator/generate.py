"""Deterministic corpus generator (SPEC §11.2–11.4).

This is a seeded, deterministic program: the same ``--set`` produces byte-identical output every run,
on any machine, with no clock, locale, or network dependency (HR-1). It emits the Phase-1 subset of
the simulated corpus — the *credit decisioning* domain crossed with six implementation styles and
five variants, thirty synthetic projects in all — each carrying the complete inputs the engine
expects (an evidence bundle, an applicability profile, a domain binding, a deviation register) and the
authored ground truth (``expected/`` outcomes plus a narrative ``README.md``). It writes a top-level
``corpus-manifest.json`` of the shape ``{"projects":[{"id":…,"events":<int>}, …], …}`` whose digest a
later item pins in ``corpus/VERSIONS.md`` (SPEC §11.7).

The module is self-contained: it depends only on the standard library and the engine's canonical-form
implementation (``agentce.canonical``, the RFC 8785 reference used for integrity hashes, SPEC §6.7).
It never imports the ``corpus`` package, so it runs both as ``python generate.py`` and as
``python -m corpus.generator``.

Each project's control outcomes are authored to what the reference engine produces and are proven by
the corpus test suite, so the precision/recall gate (SPEC §11.6) sees the seeded faults detected
(recall 1.0) and no known-pass control flagged (false-positive rate 0.0).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from agentce import canonical

# --- Corpus shape (SPEC §11.2). -------------------------------------------------------------------

GENERATOR_VERSION = "1"
CORPUS_VERSION = "2026.09"
DOMAIN = "credit"

#: The six implementation styles (SPEC §11.2). The style changes the surface of a bundle — the source
#: systems, the convention versions, the agent identity — but never the control logic, so the engine
#: must reach the same verdict for a variant whatever the style. Each carries a mixed set of
#: convention versions to satisfy the realism requirement (SPEC §11.4).
STYLES: tuple[tuple[str, str, str], ...] = (
    ("langgraph", "LangGraph service", "otel-genai:1.27.0"),
    ("openai-agents", "OpenAI Agents SDK service", "otel-genai:1.28.0"),
    ("claude-agent-sdk", "Claude Agent SDK coding-agent session", "hook-evidence:1.0"),
    ("google-adk", "Google ADK service", "otel-genai:1.26.0"),
    ("crewai", "CrewAI crew", "otel-genai:1.27.0"),
    ("custom-loop", "custom loop with direct provider SDK calls", "agentce-emit:1.0"),
)

#: The five variants generated in Phase 1. Each exercises a distinct, already-implemented engine
#: behaviour so its ground truth is checkable now (SPEC §11.2 variant table).
VARIANTS: tuple[str, ...] = (
    "known-pass",
    "known-fail",
    "insufficient-evidence",
    "tampered",
    "coverage-gap",
)

#: The project designated as the high-volume bundle (SPEC §11.4: ≥ 200k events for the largest
#: project). Its assessed subject is clean; the volume is benign background traffic on other agents.
HIGH_VOLUME_PROJECT = ("custom-loop", "known-pass")
FILLER_EVENTS = 200_000

BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"
GENESIS_PREV = "0" * 64
CONSEQUENTIAL = "dom:CreditDecision"
MINOR = "dom:MinorInquiry"

# Reserved-for-test fictional principals (SPEC §11.3: clearly fictional, no real persons).
HUMAN_OFFICER = "urn:example:person:credit-officer-7"
SERVICE_ORCH = "spiffe://corp/services/credit-orchestrator"

_OUTCOMES = frozenset(
    {
        "conformant",
        "non-conformant",
        "not_applicable",
        "not_assessed",
        "insufficient_evidence",
    }
)


# --- Event construction. --------------------------------------------------------------------------


def _event(
    *,
    eid: str,
    source: str,
    subject: str,
    time: str,
    etype: str,
    sclass: str,
    data: dict[str, Any],
    conv: str | None = None,
    task: str | None = None,
    trace: str | None = None,
) -> dict[str, Any]:
    """Build one CloudEvents-shaped evidence event with a JSON-LD payload (SPEC §6.2)."""
    event: dict[str, Any] = {
        "specversion": "1.0",
        "id": eid,
        "source": source,
        "type": f"org.agent-conformance.evidence.{etype}.v1",
        "time": time,
        "subject": subject,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": sclass,
        "data": {"@context": BASE_CONTEXT, "@type": etype, **data},
    }
    if conv is not None:
        event["agentceconv"] = conv
    if task is not None:
        event["agentcetask"] = task
    if trace is not None:
        event["agentcetrace"] = trace
    return event


def _chain(events: list[dict[str, Any]], strength: str) -> None:
    """Add a valid ``data.integrity`` hash chain to ``events``, grouped by integrity stream.

    The hash is the RFC 8785 canonical form of the event with ``data.integrity`` absent (SPEC §6.6),
    so the engine recomputes it identically. Streams are ``source|subject``; within a stream the
    events must already be in non-decreasing time order.
    """
    prev_by_stream: dict[str, str] = {}
    for event in events:
        stream = f"{event['source']}|{event['subject']}"
        prev = prev_by_stream.get(stream, GENESIS_PREV)
        digest = canonical.sha256_hex(event)  # event still has no integrity block
        event["data"]["integrity"] = {
            "stream": stream,
            "strength": strength,
            "prev": prev,
            "hash": digest,
        }
        prev_by_stream[stream] = digest


def _ref(eid: str) -> str:
    return f"agentce:event/{eid}"


# --- Scenarios: one coherent credit session per variant, authored to the engine's verdicts. -------


class _Clock:
    """A deterministic per-project clock over the 120-day observation window (SPEC §11.3)."""

    def __init__(self, day_offset: int) -> None:
        # A fixed base date; the window is 2026-05-01 .. 2026-08-29 (120 days). No wall clock.
        self._day = day_offset % 120
        self._second = 0

    def next(self) -> str:
        month, day = _day_of_window(self._day)
        stamp = f"2026-{month:02d}-{day:02d}T09:{self._second // 60:02d}:{self._second % 60:02d}.000Z"
        self._second += 1
        return stamp


def _day_of_window(offset: int) -> tuple[int, int]:
    """Map a 0-based day offset in the window to a (month, day) in 2026 (May–August)."""
    month_lengths = [(5, 31), (6, 30), (7, 31), (8, 28)]
    remaining = offset
    for month, length in month_lengths:
        if remaining < length:
            return month, remaining + 1
        remaining -= length
    return 8, 28


def _scenario(
    style_id: str, sources: dict[str, str], conv: str, variant: str, clock: _Clock
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, str]], dict[str, Any]]:
    """Return (events, expected outcomes, seeded faults, profile/bundle extras) for one project.

    Every variant centres on one consequential credit decision. The variants differ only in the
    seeded defect: a missing actor and mismatched oversight (known-fail), self-reported-only evidence
    (insufficient-evidence), a post-hash edit (tampered), or an under-captured stream (coverage-gap).
    """
    subject = f"spiffe://corp/agents/credit-{style_id}"
    agent = {"id": subject, "name": f"credit-underwriter-{style_id}"}
    task = f"task-{style_id}-{variant}"
    trace = hashlib.sha256(f"{style_id}/{variant}".encode()).hexdigest()[:32]

    def ev(
        eid: str, src: str, etype: str, sclass: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        return _event(
            eid=f"{style_id}-{variant}-{eid}",
            source=sources[src],
            subject=subject,
            time=clock.next(),
            etype=etype,
            sclass=sclass,
            data=data,
            conv=conv,
            task=task,
            trace=trace,
        )

    dec_id = f"{style_id}-{variant}-dec1"
    del_id = f"{style_id}-{variant}-del1"

    # Baseline (known-pass) building blocks; variants override specific fields.
    decision_conformant = variant != "known-fail"
    oversight = "review_before" if variant != "known-fail" else "review_after"
    delegation_verified = variant != "known-fail"
    # For insufficient-evidence the enforcement point is absent: the tool call is self-reported only.
    toolcall_class = (
        "self_report" if variant == "insufficient-evidence" else "enforcement_point"
    )

    events: list[dict[str, Any]] = []
    expected: dict[str, str] = {}
    faults: list[dict[str, str]] = []
    extras: dict[str, Any] = {}

    # 1. The consequential credit decision (drives REC-04).
    decision_data: dict[str, Any] = {
        "decision_type": CONSEQUENTIAL,
        "oversight_modality": oversight,
        "affects_natural_person": True,
        "legal_or_significant_effect": True,
        "person_ref": "agentce:principal/applicant-fictional-0001",
    }
    if decision_conformant:
        decision_data["agent"] = agent
    events.append(ev("dec1", "agent", "Decision", "self_report", decision_data))

    # 2. A verified delegation issued to the agent (drives OVS-03 E2b, and declares the principal
    #    kinds in its chain — acted_for carries only IRIs, so the human overseer is recognised here).
    delegation_chain: list[dict[str, str]] = [{"id": SERVICE_ORCH, "kind": "service"}]
    if variant != "known-fail":
        delegation_chain.append(
            {"id": HUMAN_OFFICER, "kind": "human", "role": "credit_officer"}
        )
    events.append(
        ev(
            "del1",
            "idp",
            "DelegationIssued",
            "enforcement_point",
            {
                "agent": agent,
                "subject_principal": subject,
                "chain": delegation_chain,
                "verification": {
                    "status": "verified" if delegation_verified else "unverified",
                    "method": "rfc8693",
                },
                "scope_granted": ["credit.decide", "credit.notify"],
            },
        )
    )

    # 3. The consequential tool call recorded at the enforcement point (drives OVS-03, INT-01).
    #    acted_for is an ordered list of principal IRIs to the root; known-pass ends at the human
    #    overseer (typed via the delegation chain above), known-fail ends at the service.
    acted_for: list[str] = [SERVICE_ORCH]
    if variant != "known-fail":
        acted_for.append(HUMAN_OFFICER)
    events.append(
        ev(
            "tc1",
            "gateway",
            "ToolCall",
            toolcall_class,
            {
                "agent": agent,
                "acted_for": acted_for,
                "tool": {
                    "name": "credit.record_decision",
                    "server": "mcp://credit-core.internal",
                    "protocol": "mcp",
                    "version_or_digest": "sha256:" + "1" * 64,
                },
                "args_ref": "sha256:"
                + "2" * 64,  # content-hash-only payload (SPEC §11.4)
                "result_ref": "sha256:" + "3" * 64,
                "side_effect": "write",
                "effect_class": "write",
                "refs": {"decision": _ref(dec_id), "delegation": _ref(del_id)},
            },
        )
    )

    # 4. A second, self-reported tool call to seed the INT-01 fault in known-fail (an enforcement
    #    point would have recorded it). Only present where it is the seeded defect.
    if variant == "known-fail":
        events.append(
            ev(
                "tc2",
                "agent",
                "ToolCall",
                "self_report",
                {
                    "agent": agent,
                    "tool": {"name": "credit.record_decision", "protocol": "mcp"},
                    "side_effect": "write",
                    "refs": {"decision": _ref(dec_id)},
                },
            )
        )

    # 5. An incident record naming the accountable actor (drives INC-02). Present except where a
    #    variant needs it absent; in known-fail the actor is missing (seeded fault).
    if variant in ("known-pass", "known-fail", "tampered", "coverage-gap"):
        incident_data: dict[str, Any] = {
            "incident_class": "fundamental_rights",
            "detected_at": clock.next(),
            "reported_at": clock.next(),
        }
        if variant != "known-fail":
            incident_data["agent"] = agent
        events.append(
            ev("inc1", "register", "Incident", "independent_system", incident_data)
        )

    # Expected outcomes per variant (authored to the reference engine; proven by the corpus tests).
    if variant == "known-pass":
        expected = {
            "REC-04": "conformant",
            "OVS-03": "conformant",
            "INT-01": "conformant",
            "INC-02": "conformant",
        }
    elif variant == "known-fail":
        expected = {
            "REC-04": "non-conformant",
            "OVS-03": "non-conformant",
            "INT-01": "non-conformant",
            "INC-02": "non-conformant",
        }
        faults = [
            {
                "control": "REC-04",
                "fault": "consequential decision records no acting agent",
            },
            {
                "control": "OVS-03",
                "fault": "delegation chain does not terminate at a human and oversight modality mismatched",
            },
            {
                "control": "INT-01",
                "fault": "a consequential tool call recorded only by a self-reported source",
            },
            {
                "control": "INC-02",
                "fault": "incident record names no accountable actor",
            },
        ]
    elif variant == "insufficient-evidence":
        expected = {
            "REC-04": "conformant",
            "OVS-03": "insufficient_evidence",
            "INT-01": "non-conformant",
            "INC-02": "not_applicable",
        }
        faults = [
            {
                "control": "INT-01",
                "fault": "consequential tool call recorded only by a self-reported source",
            }
        ]
    elif variant == "tampered":
        # Assertions match known-pass; a post-hash edit breaks one integrity stream (INT-family
        # downgrade of dependent controls lands with the integrity→assertion feedback in a later phase).
        expected = {
            "REC-04": "conformant",
            "OVS-03": "conformant",
            "INT-01": "conformant",
            "INC-02": "conformant",
        }
        extras["tamper"] = True
    elif variant == "coverage-gap":
        expected = {
            "REC-04": "conformant",
            "OVS-03": "conformant",
            "INT-01": "conformant",
            "INC-02": "conformant",
        }
        extras["coverage_gap"] = {
            "source": "urn:agentce:source:reference-ledger",
            "event_type": "ToolCall",
            "declared": 5,
        }

    return events, expected, faults, extras


# --- Bundle, profile, domain, and ground-truth writers. -------------------------------------------


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in events
    )
    path.write_text(body, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _benign_quarantine(subject: str, source: str) -> list[dict[str, Any]]:
    """Two benign events the engine quarantines (SPEC §11.4): a duplicate delivery and an
    unrecognised type. They must not touch the assessed outcomes."""
    dup = _event(
        eid=f"{subject}-dup",
        source=source,
        subject=subject,
        time="2026-05-01T08:00:00.000Z",
        etype="SessionStart",
        sclass="self_report",
        data={"session_id": "sess-dup"},
    )
    _chain(
        [dup], "export_chained"
    )  # the accepted delivery verifies cleanly on its own stream
    dup2 = json.loads(json.dumps(dup))  # a second, identical delivery -> duplicate_id
    unknown = {
        "specversion": "1.0",
        "id": f"{subject}-unknown",
        "source": source,
        "type": "org.agent-conformance.evidence.Heartbeat.v1",  # not a modelled type
        "time": "2026-05-01T08:00:01.000Z",
        "subject": subject,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": "self_report",
        "data": {
            "@context": BASE_CONTEXT,
            "@type": "SessionStart",
            "session_id": "sess-hb",
        },
    }
    return [dup, dup2, unknown]


def _apply_tamper(events: list[dict[str, Any]]) -> None:
    """Edit one chained event after its hash was computed, so the engine detects the tamper.

    The edit changes a content-hash reference (still schema-valid, so the event is accepted and
    reaches integrity verification) to a different digest — the recomputed hash no longer matches the
    stored one, and the stream verifies as ``failed``. Structural verdicts are unaffected."""
    for event in events:
        if event["data"].get("@type") == "ToolCall" and "integrity" in event["data"]:
            event["data"]["result_ref"] = (
                "sha256:" + "9" * 64
            )  # changed after hashing -> mismatch
            return


def _otlp_attr(value: str | int) -> dict[str, Any]:
    return (
        {"intValue": str(value)} if isinstance(value, int) else {"stringValue": value}
    )


def _write_native_export(
    proj_dir: Path, style_id: str, variant: str, conv: str, subject: str
) -> str | None:
    """Write the project's native OTLP/JSON GenAI export (SPEC §11.2, §12.3), or ``None``.

    Only the OpenTelemetry-instrumented styles carry an OTLP export; the ``otel-genai`` adapter maps it
    to the agent-runtime evidence (``SessionStart``/``SessionEnd``, ``ModelCall``, ``ToolCall``). The
    hook-evidence and agentce-emit styles emit through their own paths, not OTLP. Deterministic: ids
    derive from the style and variant, times from a fixed base — no wall clock.
    """
    if not conv.startswith("otel-genai:"):
        return None
    version = conv.split(":", 1)[1]
    trace = hashlib.sha256(f"{style_id}/{variant}/native".encode()).hexdigest()[:32]
    base_ns = (
        1_746_090_000_000_000_000  # a fixed 2025 base; offsets keep spans in order
    )
    conversation = f"conv-{style_id}-{variant}"

    def span(
        span_id: str, name: str, attrs: dict[str, str | int], start_off: int, dur: int
    ) -> dict[str, Any]:
        return {
            "traceId": trace,
            "spanId": span_id,
            "name": name,
            "startTimeUnixNano": str(base_ns + start_off),
            "endTimeUnixNano": str(base_ns + start_off + dur),
            "attributes": [
                {"key": k, "value": _otlp_attr(v)} for k, v in attrs.items()
            ],
            "status": {},
        }

    agent_attrs = {
        "gen_ai.agent.id": subject,
        "gen_ai.agent.name": f"credit-{style_id}",
        "gen_ai.conversation.id": conversation,
    }
    spans = [
        span(
            "00000000000a0001",
            "invoke_agent credit",
            {
                "gen_ai.operation.name": "invoke_agent",
                "deployment.environment.name": "production",
                **agent_attrs,
            },
            0,
            5_000_000_000,
        ),
        span(
            "00000000000a0002",
            "chat gpt-4o",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.system": "openai",
                "gen_ai.request.model": "gpt-4o",
                "gen_ai.usage.input_tokens": 1024,
                "gen_ai.usage.output_tokens": 128,
                **agent_attrs,
            },
            1_000_000_000,
            800_000_000,
        ),
        span(
            "00000000000a0003",
            "execute_tool credit.record_decision",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "credit.record_decision",
                "gen_ai.tool.server": "mcp://credit-core.internal",
                "gen_ai.tool.protocol": "mcp",
                **agent_attrs,
            },
            2_000_000_000,
            300_000_000,
        ),
    ]
    doc = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": f"credit-{style_id}"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": f"opentelemetry.instrumentation.{style_id.replace('-', '_')}",
                            "version": "1.0",
                        },
                        "schemaUrl": f"https://opentelemetry.io/schemas/{version}",
                        "spans": spans,
                    }
                ],
            }
        ]
    }
    native_dir = proj_dir / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    rel = "native/otel-genai.otlp.json"
    (proj_dir / rel).write_text(
        json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return rel


def _write_project(
    root: Path, style_id: str, variant: str, index: int
) -> dict[str, Any]:
    style_desc, conv = next((d, c) for (s, d, c) in STYLES if s == style_id)
    sources = {
        "agent": f"urn:agentce:source:{style_id}-runtime:eu-1",
        "gateway": f"urn:agentce:source:{style_id}-gateway:eu-1",
        "idp": "urn:agentce:source:workload-idp:eu-1",
        "register": "urn:agentce:source:incident-register:corp",
        "benign": f"urn:agentce:source:{style_id}-session:eu-1",
        "background": f"urn:agentce:source:{style_id}-runtime-bulk:eu-1",
    }
    clock = _Clock(day_offset=index * 3)
    events, expected, faults, extras = _scenario(
        style_id, sources, conv, variant, clock
    )

    subject = f"spiffe://corp/agents/credit-{style_id}"
    # Integrity chains over the curated streams (export_chained -> verified_weak, a clean status).
    _chain(events, "export_chained")
    if extras.get("tamper"):
        _apply_tamper(events)

    proj_dir = root / "projects" / DOMAIN / style_id / variant
    evidence = proj_dir / "evidence"
    _write_native_export(proj_dir, style_id, variant, conv, subject)

    # Group curated events into one file per source adapter (mixed convention versions, SPEC §11.4).
    files: list[dict[str, str]] = []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_source.setdefault(str(event["source"]), []).append(event)
    total_events = 0
    for src in sorted(by_source):
        rel = f"events/{_slug(src)}.jsonl"
        digest = _write_jsonl(evidence / rel, by_source[src])
        files.append({"path": rel, "sha256": digest})
        total_events += len(by_source[src])

    # A couple of benign quarantined events (SPEC §11.4). They ride their own stream.
    quarantine = _benign_quarantine(subject, sources["benign"])
    files.append(
        {
            "path": "events/_benign-quarantine.jsonl",
            "sha256": _write_jsonl(
                evidence / "events/_benign-quarantine.jsonl", quarantine
            ),
        }
    )
    total_events += len(quarantine)

    # The high-volume project (SPEC §11.4). Benign background traffic on other agents: ingested and
    # counted, but not on the assessed subject, so the assessed verdicts are unchanged.
    if (style_id, variant) == HIGH_VOLUME_PROJECT:
        rel = "events/background.jsonl"
        digest = _write_background(evidence / rel, sources["background"])
        files.append({"path": rel, "sha256": digest})
        total_events += FILLER_EVENTS

    # A coverage denominator that declares more than was captured (SPEC §11.2 coverage-gap).
    coverage_denoms: list[dict[str, Any]] = []
    if "coverage_gap" in extras:
        gap = extras["coverage_gap"]
        ref_rel = "reference/coverage.json"
        (evidence / "reference").mkdir(parents=True, exist_ok=True)
        (evidence / ref_rel).write_text(
            json.dumps(
                {"counts": {gap["event_type"]: gap["declared"]}},
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        files.append(
            {
                "path": ref_rel,
                "sha256": hashlib.sha256((evidence / ref_rel).read_bytes()).hexdigest(),
            }
        )
        coverage_denoms = [
            {
                "kind": "reference-ledger",
                "source": gap["source"],
                "covers": [gap["event_type"]],
                "manifest": ref_rel,
            }
        ]

    manifest = {
        "agentce_bundle_version": 1,
        "domain": DOMAIN,
        "sources": [
            {"id": s}
            for s in sorted({str(e["source"]) for e in events} | set(sources.values()))
        ],
        "files": sorted(files, key=lambda f: f["path"]),
    }
    (evidence / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    bundle_digest = "sha256:" + canonical.sha256_hex(manifest)

    _write_profile(proj_dir, subject, style_id, coverage_denoms, sources)
    _write_domain(proj_dir)
    _write_deviations(proj_dir)
    _write_expected(proj_dir, subject, expected, faults, variant)
    _write_readme(proj_dir, style_id, style_desc, variant, subject, expected, faults)

    return {
        "id": f"{DOMAIN}/{style_id}/{variant}",
        "domain": DOMAIN,
        "style": style_id,
        "variant": variant,
        "subject": subject,
        "events": total_events,
        "bundle_digest": bundle_digest,
        "expected": expected,
        "seeded_faults": len(faults),
    }


def _slug(source: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in source).strip("-")


def _write_background(path: Path, source: str) -> str:
    """Write ``FILLER_EVENTS`` benign background tool calls (one ingest stream, monotonic time).

    Deterministic and index-driven: no clock, no RNG. These are self-reported and carry no integrity
    chain — high-volume raw traffic on background agents, none of them the assessed subject."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for i in range(FILLER_EVENTS):
            subject = f"spiffe://corp/agents/bg-worker-{i % 64}"
            second = i % 60
            minute = (i // 60) % 60
            hour = 9 + (i // 3600) % 12
            day_off = (i // 43200) % 120
            month, day = _day_of_window(day_off)
            event = {
                "specversion": "1.0",
                "id": f"bg-{i:012d}",
                "source": source,
                "type": "org.agent-conformance.evidence.ToolCall.v1",
                "time": f"2026-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}.{i % 1000:03d}Z",
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
            handle.write(
                json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_profile(
    proj_dir: Path,
    subject: str,
    style_id: str,
    coverage_denoms: list[dict[str, Any]],
    sources: dict[str, str],
) -> None:
    lines = [
        "# Applicability profile (SPEC §6.5): what the adopter declares about the assessed system.",
        "profile_version: 1",
        "observation_window:",
        '  start: "2026-05-01T00:00:00Z"',
        '  end: "2026-08-29T00:00:00Z"',
        "catalogs:",
        '  - "eu-ai-act@2026.09"',
        "subjects:",
        f'  - id: "{subject}"',
        f'    name: "credit-underwriter-{style_id}"',
        '    role: "both"',
        "    declared_decision_types:",
        f'      - "{CONSEQUENTIAL}"',
        "    declared_oversight:",
        f'      "{CONSEQUENTIAL}": "review_before"',
        "    evidence_sources:",
    ]
    for key in ("agent", "gateway", "idp", "register"):
        cls = {
            "agent": "self_report",
            "gateway": "enforcement_point",
            "idp": "enforcement_point",
            "register": "independent_system",
        }[key]
        lines.append(f'      - adapter: "{key}"')
        lines.append(f'        source: "{sources[key]}"')
        lines.append(f'        class: "{cls}"')
    if coverage_denoms:
        lines.append("    coverage_denominators:")
        for denom in coverage_denoms:
            lines.append(f'      - kind: "{denom["kind"]}"')
            lines.append(f'        source: "{denom["source"]}"')
            lines.append(f'        manifest: "{denom["manifest"]}"')
            lines.append("        covers:")
            for cover in denom["covers"]:
                lines.append(f'          - "{cover}"')
    (proj_dir / "applicability.yaml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _write_domain(proj_dir: Path) -> None:
    text = (
        "# Domain ontology binding (SPEC §6.5): the credit domain's decision classes.\n"
        "decision_types:\n"
        f'  - id: "{CONSEQUENTIAL}"\n'
        '    subclass_of: "agentce:ConsequentialDecision"\n'
        "    consequential: true\n"
        '    required_oversight_modality: "review_before"\n'
        f'  - id: "{MINOR}"\n'
        '    subclass_of: "agentce:Decision"\n'
        "    consequential: false\n"
    )
    (proj_dir / "domain.linkml.yaml").write_text(text, encoding="utf-8")


def _write_deviations(proj_dir: Path) -> None:
    text = (
        "# Deviation register (SPEC §6.5). Empty for the Phase-1 variants; the partial variant that\n"
        "# exercises deviation records lands with deviation handling in a later phase.\n"
        "deviations: []\n"
    )
    (proj_dir / "deviations.yaml").write_text(text, encoding="utf-8")


def _write_expected(
    proj_dir: Path,
    subject: str,
    expected: dict[str, str],
    faults: list[dict[str, str]],
    variant: str,
) -> None:
    payload = {
        "subject": subject,
        "variant": variant,
        "outcomes": [
            {"control": control, "subject": subject, "outcome": outcome}
            for control, outcome in sorted(expected.items())
        ],
        "seeded_faults": faults,
    }
    exp_dir = proj_dir / "expected"
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "outcomes.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _write_readme(
    proj_dir: Path,
    style_id: str,
    style_desc: str,
    variant: str,
    subject: str,
    expected: dict[str, str],
    faults: list[dict[str, str]],
) -> None:
    fault_by_control = {f["control"]: f["fault"] for f in faults}
    rows = "\n".join(
        f"| {control} | {expected[control]} | {fault_by_control.get(control, 'declared measure observed')} |"
        for control in sorted(expected)
    )
    text = (
        f"# credit / {style_id} / {variant}\n\n"
        f"A simulated **{style_desc}** making consequential credit decisions (SPEC §11.2–11.3). "
        f"Assessed subject: `{subject}`.\n\n"
        f"Variant **{variant}**: "
        f"{_variant_blurb(variant)}\n\n"
        "## Ground truth (control → expected outcome → why)\n\n"
        "| Control | Expected | Rationale / seeded fault |\n|---|---|---|\n"
        f"{rows}\n\n"
        "Outcomes are authored to the reference engine and proven by the corpus test suite; "
        "`expected/outcomes.json` is the machine-readable form (SPEC §11.3).\n"
    )
    (proj_dir / "README.md").write_text(text, encoding="utf-8")


def _variant_blurb(variant: str) -> str:
    return {
        "known-pass": "full enforcement-point evidence, every declared measure observed.",
        "known-fail": "missing acting agent, a delegation chain that does not reach a human, a mismatched oversight modality, a self-reported consequential tool call, and an incident without an actor.",
        "insufficient-evidence": "only self-reported streams for the consequential tool call, so the oversight control cannot be evaluated.",
        "tampered": "one integrity stream carries an event edited after it was hashed; the tamper is detected while the structural verdicts are unchanged.",
        "coverage-gap": "the reference ledger declares more tool calls than were captured, so coverage falls below threshold.",
    }[variant]


# --- Corpus assembly. -----------------------------------------------------------------------------


def build_corpus(out: Path, set_name: str = "v1") -> dict[str, Any]:
    """Generate the corpus ``set_name`` into ``out`` and return the corpus manifest."""
    if set_name != "v1":
        raise SystemExit(
            f"unknown corpus set {set_name!r}; the only Phase-1 set is 'v1'."
        )
    out.mkdir(parents=True, exist_ok=True)
    projects: list[dict[str, Any]] = []
    index = 0
    for style_id, _desc, _conv in STYLES:
        for variant in VARIANTS:
            projects.append(_write_project(out, style_id, variant, index))
            index += 1

    projects.sort(key=lambda p: p["id"])
    digest_material = "\n".join(
        f"{p['id']}\t{p['bundle_digest']}\t{p['events']}" for p in projects
    )
    manifest = {
        "corpus_version": CORPUS_VERSION,
        "generator_version": GENERATOR_VERSION,
        "set": set_name,
        "domain": DOMAIN,
        "digest": "sha256:"
        + hashlib.sha256(digest_material.encode("utf-8")).hexdigest(),
        "projects": projects,
    }
    (out / "corpus-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus.generator",
        description="Deterministically generate the AgentCE simulated corpus (SPEC §11.2–11.4).",
    )
    parser.add_argument(
        "--set",
        dest="set_name",
        default="v1",
        help="the corpus set to generate (default: v1)",
    )
    parser.add_argument("--out", required=True, help="the output directory")
    args = parser.parse_args(argv)
    manifest = build_corpus(Path(args.out), args.set_name)
    print(
        f"generated {len(manifest['projects'])} projects into {args.out} (digest {manifest['digest']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
