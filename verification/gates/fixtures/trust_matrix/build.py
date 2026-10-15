#!/usr/bin/env python3
"""Fixture builders for the trust-model case matrix (matrix.yaml beside this file), run by VG-TRUST-MATRIX
(verification/gates/trust_matrix.py).

A builder is called as ``builder(dest, root, dims, args)``: ``dest`` an empty folder it owns, ``root`` the throwaway
clone of the public repository, ``dims`` the cell's dimension values, ``args`` the cell's optional ``fixture_args``.
It writes one evidence bundle under ``dest/bundle`` (one ``events/subject.jsonl`` of sorted-key lines and a manifest),
plus the applicability profile and domain binding it is assessed with, and returns a Fixture naming them and the
subject whose assertions the runner reads.

Every builder reads its events from the cell's dimension values, so a cell's declared dimensions and its events cannot
drift apart. A dimension value a builder does not build raises FixtureError, which the runner records as ERROR with the
message; add the value here rather than writing a near-copy builder.

- ``instruction_chain``: one instruction, its lineage, the activity acting on it, corroboration, delegation, memory and
  the integrity of every stream (CND-04, CND-05, CND-06, ROB-02 and the instruction cells of the matrix).
- ``memory_case``: a consequential decision and the memory it used (ROB-02's write, read, path, subject and arrival).
- ``focus_case``: the other controls whose ``min_source_class`` is above self_report: one focus event of a declared
  class, its shape made to pass or fail, an unrelated enforcement-point event, and the focus stream's integrity.
- ``gate_case``: one case of a gate's cases.json verbatim, for anchor cells that pin a generated bundle to the gate's.

Integrity (SPEC §6.6). Every event carries ``data.integrity``: a SHA-256 hash chain per (source, subject) stream in
arrival order, by default ``export_anchored`` with a manifest anchor after every event, which verifies. The
``integrity`` dimension changes one stream (``integrity_stream``) to another strength or status, so the caps of SPEC
§6.6 and §8.1 can be read against a clean twin: verified_signed is source_signed with a sig_ref naming a DSSE file
that carries no signature (an empty envelope no key verifies, 18.155), unsigned is source_signed with no sig_ref,
weak_chained is export_chained, weak_unanchored is export_anchored with no manifest anchor, gap breaks the last
event's prev, failed breaks its hash, missing writes no integrity member, time_suspect anchors before the events. Hashes use RFC 8785 for the ASCII-only, integer-only content written
here (sorted keys, no spaces), so no engine code is used to build a fixture.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUBJECT = "spiffe://corp/agents/cnd05-fixture"
OTHER = "spiffe://corp/agents/other"
CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"
GATE_FIXTURES = "verification/gates/fixtures"
GENESIS = "0" * 64
ANCHOR_AT = "2026-01-03T00:00:00Z"
#: Appendix F Instruction.source_class values, plus "missing" (the member left out).
SOURCE_CLASSES = (
    "user",
    "operator",
    "service",
    "agent_identified",
    "agent_unidentified",
    "tool_output",
    "retrieved",
    "memory_trusted",
    "memory_untrusted",
    "missing",
)
EP, SR, IS = "enforcement_point", "self_report", "independent_system"
#: One source per role, each with its own declared class (SPEC §6.4) and so its own integrity stream (SPEC §6.6).
SOURCES = {
    "ingress": (
        "urn:src:ingress",
        EP,
    ),  # an API gateway or prompt firewall recording instructions (SPEC §7.7.5)
    "gateway": ("urn:src:gateway", EP),  # the MCP or tool gateway recording tool calls
    "guard": ("urn:src:guard", EP),  # the memory guard
    "policy": ("urn:src:policy", EP),  # the policy engine and the delegation issuer
    "agent": ("urn:src:agent", SR),  # the agent runtime
    "sor": (
        "urn:src:sor",
        IS,
    ),  # systems of record, approvals, incident register, identity provider
    "agent_ep": (
        "urn:src:agent-ep",
        EP,
    ),  # an agent-run source declared enforcement_point (for focus classes)
}
PRINCIPAL = {"id": "user:alice", "kind": "human", "role": "credit_officer"}
OTHER_PRINCIPAL = {"id": "user:mallory", "kind": "human", "role": "credit_officer"}
INTEGRITY = (
    "verified",
    "verified_signed",
    "weak_chained",
    "weak_unanchored",
    "unsigned",
    "gap",
    "failed",
    "missing",
    "time_suspect",
)


TWINS = (
    "same",
    "other_content",
    "other_task",
    "other_subject",
    "other_authorised_class",
    "other_principal",
    "no_principal",
    "principal_empty_id",
    "principal_empty_object",
    "principal_string",
    "origin_ep_read_untrusted",
    "origin_sr_read",
    "parent_tool_output",
)
#: A malformed Instruction.principal, written on both the instruction and its twin.
MALFORMED_PRINCIPAL: dict[str, Any] = {
    "principal_empty_id": {"id": ""},
    "principal_empty_object": {},
    "principal_string": PRINCIPAL["id"],
}
WITNESS_TIMES = (
    "before",
    "same_instant",
    "same_instant_offset",
    "after_1s",
    "after",
    "missing",
    "unparseable",
)
NOT_A_TIME = "not-a-time"


class FixtureError(Exception):
    """A cell asks for a bundle this module cannot build (the message says which dimension)."""


@dataclass(frozen=True)
class Fixture:
    bundle: Path
    profile: Path
    domain: Path
    subject: str


def _pick(
    dims: dict[str, Any],
    name: str,
    default: str,
    allowed: tuple[str, ...],
    who: str = "builder",
) -> str:
    value = str(dims.get(name, default))
    if value not in allowed:
        raise FixtureError(
            f"{who} does not build {name}={value} (it builds {', '.join(allowed)})"
        )
    return value


def _jcs(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


class _Events:
    """CloudEvents in arrival order, each a second after the last unless a time is given."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.roles: dict[str, str] = {}  # event id -> source role

    def add(
        self,
        eid: str,
        ptype: str,
        role: str,
        subject: str = SUBJECT,
        time: str | None = None,
        cls: str | None = None,
        **data: Any,
    ) -> str:
        source, declared = SOURCES[role]
        payload: dict[str, Any] = {"@type": ptype, "@context": CONTEXT}
        payload.update({k: v for k, v in data.items() if v is not None})
        n = len(self.items)
        self.items.append(
            {
                "id": eid,
                "source": source,
                "subject": subject,
                "type": f"org.agent-conformance.evidence.{ptype}.v1",
                "agentcesourceclass": cls or declared,
                "time": time or f"2026-01-02T00:{n // 60:02d}:{n % 60:02d}Z",
                "specversion": "1.0",
                "datacontenttype": "application/ld+json",
                "data": payload,
            }
        )
        self.roles[eid] = role
        return f"agentce:event/{eid}"


def _shift(stamp: str, seconds: int, offset_hours: int = 0) -> str:
    """A ``...Z`` time moved by ``seconds``, written in UTC or at a ``+HH:00`` offset (the same instant)."""
    at = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    ) + timedelta(seconds=seconds)
    if not offset_hours:
        return at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return at.astimezone(timezone(timedelta(hours=offset_hours))).strftime(
        f"%Y-%m-%dT%H:%M:%S+{offset_hours:02d}:00"
    )


def _retime(ev: "_Events", witness: str, call: str, decision: str) -> None:
    """Place the twin (i1-ingress) and the PolicyDecision (pd1) on the acting call's (tc1) clock, and forge or drop
    the call's or the Decision's (d1) own time; every default leaves the arrival-order times as they are."""
    by_id = {e["id"]: e for e in ev.items}
    if (witness, call, decision) == ("before", "as_recorded", "as_recorded"):
        return
    if "tc1" not in by_id:
        raise FixtureError(
            "witness_time, call_time and decision_time need an acting ToolCall (acted_on)"
        )
    true = by_id["tc1"]["time"]
    witnesses = [by_id[i] for i in ("i1-ingress", "pd1") if i in by_id]
    stamp = {
        "before": None,
        "same_instant": true,
        "same_instant_offset": _shift(true, 0, 5),
        "after_1s": _shift(true, 1),
        "after": _shift(true, 30),
        "missing": "",
        "unparseable": NOT_A_TIME,
    }[witness]
    if call == "forged_late" or decision == "forged_late":
        stamp = _shift(true, 5)
    for event in witnesses:
        if stamp == "":
            del event["time"]
        elif stamp is not None:
            event["time"] = stamp
    if call == "missing":
        del by_id["tc1"]["time"]
    elif call == "unparseable":
        by_id["tc1"]["time"] = NOT_A_TIME
    elif call == "forged_late":
        by_id["tc1"]["time"] = _shift(true, 10)
    if decision == "forged_late":
        if "d1" not in by_id:
            raise FixtureError("decision_time=forged_late needs a Decision (acted_on)")
        by_id["d1"]["time"] = _shift(true, 10)


def _apply_integrity(
    events: list[dict[str, Any]], bundle: Path, mode: str, target: str | None
) -> list[dict[str, Any]]:
    """Chain every (source, subject) stream; the target role's stream gets ``mode``, every other one ``verified``.
    Returns the manifest's stream entries (anchors for export_anchored streams)."""
    streams: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        streams.setdefault(f"{event['source']}|{event['subject']}", []).append(event)
    target_source = SOURCES[target][0] if target else None
    entries: list[dict[str, Any]] = []
    for stream, members in sorted(streams.items()):
        this = (
            mode
            if target_source and stream.startswith(target_source + "|")
            else "verified"
        )
        if this == "missing":
            continue
        strength = {
            "verified_signed": "source_signed",
            "unsigned": "source_signed",
            "weak_chained": "export_chained",
        }.get(this, "export_anchored")
        prev = GENESIS
        for index, event in enumerate(members):
            event["data"].pop("integrity", None)
            digest = hashlib.sha256(_jcs(event)).hexdigest()
            block: dict[str, Any] = {
                "hash": digest,
                "prev": prev,
                "stream": stream,
                "strength": strength,
            }
            if this == "gap" and index == len(members) - 1:
                block["prev"] = (
                    "f" * 64
                )  # the last event names a predecessor the stream does not hold
            if this == "failed" and index == len(members) - 1:
                block["hash"] = "e" * 64  # the stored hash no longer matches the record
            if this == "verified_signed":
                sig = f"attestations/{hashlib.sha256(stream.encode()).hexdigest()[:8]}-{index:04d}.dsse.json"
                (bundle / sig).parent.mkdir(parents=True, exist_ok=True)
                (bundle / sig).write_text(
                    json.dumps(
                        {
                            "payloadType": "application/vnd.in-toto+json",
                            "payload": "",
                            "signatures": [],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                block["sig_ref"] = sig
            event["data"]["integrity"] = block
            prev = digest
        if strength == "export_anchored" and this != "weak_unanchored":
            at = "2026-01-01T00:00:00Z" if this == "time_suspect" else ANCHOR_AT
            entries.append(
                {
                    "stream": stream,
                    "head": prev,
                    "anchors": [
                        {
                            "kind": "rfc3161",
                            "at": at,
                            "ref": f"anchors/{len(entries)}.tsr",
                        }
                    ],
                }
            )
    return entries


def write_bundle(
    bundle: Path,
    events: list[dict[str, Any]],
    domain: str = "trust-fixture",
    integrity: str | None = None,
    integrity_stream: str | None = None,
    sources: list[dict[str, str]] | None = None,
) -> None:
    """Write events as the gates do: one subject.jsonl of sorted-key lines and a manifest declaring each source's
    class. With ``integrity`` set, every event carries a chain (SPEC §6.6) and the manifest lists the anchors."""
    (bundle / "events").mkdir(parents=True, exist_ok=False)
    streams = (
        _apply_integrity(events, bundle, integrity, integrity_stream)
        if integrity
        else []
    )
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
    (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
    files = [
        {
            "path": "events/subject.jsonl",
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    ]
    for folder in ("attestations", "reference"):
        for extra in (
            sorted((bundle / folder).glob("*.json"))
            if (bundle / folder).is_dir()
            else []
        ):
            files.append(
                {
                    "path": f"{folder}/{extra.name}",
                    "sha256": hashlib.sha256(extra.read_bytes()).hexdigest(),
                }
            )
    manifest: dict[str, Any] = {
        "agentce_bundle_version": 1,
        "domain": domain,
        "files": files,
        "sources": sources
        if sources is not None
        else [{"id": src, "class": cls} for src, cls in sorted(set(SOURCES.values()))],
    }
    if streams:
        manifest["streams"] = streams
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def gate_files(root: Path, gate: str) -> tuple[Path, Path]:
    folder = root / GATE_FIXTURES / gate
    profile, domain = folder / "applicability.yaml", folder / "domain.linkml.yaml"
    for path in (profile, domain):
        if not path.is_file():
            raise FixtureError(f"the clone has no {path.relative_to(root)}")
    return profile, domain


def reconcile(
    bundle: Path, events: list[dict[str, Any]], subject: str = SUBJECT
) -> dict[str, Any]:
    """An independent denominator (SPEC §6.5: a system-of-record export, independent of the agent and the enforcement
    point) whose manifest counts exactly the subject's events of every type, so coverage reconciles ("ok")."""
    counts: dict[str, int] = {}
    for event in events:
        if event["subject"] == subject:
            counts[event["data"]["@type"]] = counts.get(event["data"]["@type"], 0) + 1
    (bundle / "reference").mkdir(parents=True, exist_ok=True)
    (bundle / "reference" / "sor-counts.json").write_text(
        json.dumps({"counts": counts}, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "kind": "system_of_record_export",
        "source": "urn:src:sor-export",
        "covers": sorted(counts),
        "manifest": "reference/sor-counts.json",
    }


def _task_scopes(roles: str) -> str:
    """The binding's task_scopes for an instruction_chain ``principal_role`` (SPEC §7.7.6 authorised_principal_roles):
    credit_officer authorised (declared_match, and no_role, whose principal carries no role); another role authorised
    (declared_other); no roles declared (not_declared); two task types, so the fixture's task-1 has no scope
    (no_scope)."""
    listed = {"declared_other": '["loan_manager"]', "not_declared": None}.get(
        roles, '["credit_officer"]'
    )
    declared = f", authorised_principal_roles: {listed}" if listed else ""
    types = (
        ("credit-review", "kyc-review") if roles == "no_scope" else ("credit-review",)
    )
    return "".join(
        f'  - {{task_type: "{t}", allowed_tools: ["lookup"], allowed_effect_classes: ["read"],\n'
        f'     requires_approval: ["irreversible"], budgets: {{tokens: 1000, compute_seconds: 60, spend: 0}}{declared}}}\n'
        for t in types
    )


def write_profile(
    dest: Path,
    catalogs: list[str],
    subjects: tuple[str, ...] = (SUBJECT, OTHER),
    oversight: str | None = None,
    denominator: dict[str, Any] | None = None,
    roles: str = "declared_match",
) -> tuple[Path, Path]:
    """An applicability profile (SPEC §6.5) naming the catalogs and subjects (the fixture subject with the coverage
    denominator, when given), and a domain binding in which dom:CreditDecision is consequential and dom:Minor is
    not."""
    lines = [
        "profile_version: 1",
        "observation_window:",
        '  start: "2026-01-01T00:00:00Z"',
        '  end: "2026-02-01T00:00:00Z"',
        "content_capture:",
        "  mode: hashes_only",
        "catalogs:",
    ]
    lines += [f'  - "{c}"' for c in catalogs]
    lines.append("subjects:")
    for s in sorted(subjects):
        lines += [
            f'  - id: "{s}"',
            f'    name: "trust matrix {s.rsplit("/", 1)[-1]}"',
            '    role: "both"',
        ]
        if denominator and s == SUBJECT:
            lines += [
                "    coverage_denominators:",
                f'      - kind: "{denominator["kind"]}"',
                f'        source: "{denominator["source"]}"',
                f'        manifest: "{denominator["manifest"]}"',
                "        covers:",
            ] + [f'          - "{t}"' for t in denominator["covers"]]
    profile = dest / "applicability.yaml"
    profile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    extra = f', required_oversight_modality: "{oversight}"' if oversight else ""
    domain = dest / "domain.linkml.yaml"
    domain.write_text(
        "decision_types:\n"
        f'  - {{id: "dom:CreditDecision", subclass_of: "agentce:ConsequentialDecision", consequential: true{extra}}}\n'
        '  - {id: "dom:Minor", subclass_of: "agentce:Decision", consequential: false}\n'
        "task_scopes:\n" + _task_scopes(roles),
        encoding="utf-8",
    )
    return profile, domain


CONDUCT = ["baseline@2026.09", "conduct@2026.09"]


# --- instruction chains (CND-04, CND-05, CND-06, ROB-02) -------------------------------------------------------------


def _with_role(principal: dict[str, Any] | None, role: str) -> dict[str, Any] | None:
    """A witness's principal with its role as the ``pd_role`` / ``twin_role`` dimension sets it: the acted-on
    principal's (match), another (other) or none (CND-04 role witness)."""
    if principal is None or role == "match":
        return principal
    if role == "other":
        return {**principal, "role": "loan_manager"}
    return {k: v for k, v in principal.items() if k != "role"}


def instruction_chain(
    dest: Path, root: Path, dims: dict[str, Any], args: dict[str, Any]
) -> Fixture:
    """One instruction, its lineage, the activity acting on it and the records around it, under the baseline catalog
    and the Conduct overlay (so CND-04, CND-05, CND-06 and ROB-02 are all assessed).

    Dimensions read (default first):
    - instruction_class: enforcement_point | self_report | independent_system. The acted-on Instruction's own class
      (enforcement_point comes from the ingress source, SPEC §7.7.5; self_report from the agent).
    - source_class: an Appendix F value, or missing. The acted-on Instruction's declared class.
    - principal: absent | present. Instruction.principal on every instruction of the chain (SPEC §6.2.3).
    - origin: none | ep_read_trusted | ep_read_untrusted | ep_read_no_trustmin | sr_read | tool_call |
      resource_access | not_held | bare_id | quarantined. refs.origin of the chain's root: a guard read at trust_min
      trusted, or untrusted, or with no trust_min, of a record the guard wrote trusted; an agent read of that record;
      a held tool call or resource access; a ref the bundle does not hold; a bare id of a held tool_output
      instruction; a guard read ingest quarantines schema_invalid (trust_min outside the enumeration).
    - chain_depth: 1 | 2 | 3. Instructions in the refs.parent chain (1 is direct); the origin hangs off the root.
    - parent_class: same | enforcement_point | self_report. The class of the parents (depth > 1).
    - parent_source_class: same | user | tool_output | missing. The declared class of the root parent (depth > 1).
    - instruction_held: held | not_held | bare_id | quarantined_schema | quarantined_time_order | other_subject.
      How refs.instruction names the acted-on instruction: its IRI; an id the bundle does not hold; its bare id; the
      instruction quarantined (a source_class outside the enumeration; or arriving more than five minutes late in its
      stream); the instruction recorded under another subject.
    - acted_on: ToolCall | consequential_ToolCall | consequential_Decision | Decision_only | both | ModelCall |
      minor_Decision. ToolCall: an enforcement-point call acting on it, executing nothing. consequential_ToolCall:
      the call acts on it and executes a consequential Decision. consequential_Decision: the consequential Decision
      acts on it and the call executes the decision without naming the instruction. Decision_only: the decision acts
      on it and no call executes it. both: the call and the decision both act on it. ModelCall: a model call carries
      refs.instruction. minor_Decision: a non-consequential decision acts on it.
    - call_class: enforcement_point | self_report. The acting ToolCall's own class.
    - unrelated_ep: absent | present. An enforcement-point ToolCall elsewhere in the subject, acting on its own
      enforcement-point user instruction (ix) and on nothing else.
    - corroboration: none | ep_pd_principal | ep_pd_other_principal | ep_pd_no_principal | sr_pd_principal |
      ep_pd_deny | is_pd_principal. A PolicyDecision on the acting call (SPEC §7.7.3, §7.7.5).
    - relabel: none | ep_twin_untrusted | ep_twin_agrees. An enforcement-point ingress Instruction with the same
      content_ref and refs.task as the acted-on one, declaring tool_output, or the same class (SPEC §7.7.5).
    - delegation: none | ep_verified | ep_unverified | sr_verified. A DelegationIssued the call names in
      refs.delegation (SPEC §6.3 agentce:chainVerified), from the policy source or the agent.
    - refusal: none | sr_refusal | ep_refusal. A Refusal of the acted-on instruction (CND-06).
    - memory: none | clean. clean adds a guard write and a trust_min trusted guard read of a record the decision used
      (ROB-02's minimum evidence and a clean memory path).
    - twin: same | other_content | other_task | other_subject | other_authorised_class | other_principal |
      no_principal | principal_empty_id | principal_empty_object | principal_string | origin_ep_read_untrusted |
      origin_sr_read | parent_tool_output. How the relabel twin differs from the acted-on instruction (the three
      principal_* values write that malformed principal on both the instruction and its twin).
    - pd_request: acting_call | other_call. refs.request of the corroborating PolicyDecision (other_call: the
      unrelated enforcement-point call, which is then written).
    - witness_time: before | same_instant | same_instant_offset | after_1s | after | missing | unparseable. When the
      twin and the PolicyDecision are recorded against the acting call's time (both are written before the call in
      arrival order): earlier, the same instant, the same instant written +05:00 against the call's Z, a second
      later, thirty seconds later, with no time, or with a time that is not a date-time.
    - call_time: as_recorded | missing | unparseable | forged_late. The acting call's own time; forged_late stamps it
      ten seconds late with the witnesses five seconds after the true time.
    - parent_cycle: none | self | pair. The acted-on instruction names itself as refs.parent, or an
      enforcement-point user instruction whose refs.parent is the acted-on one (KD-283).
    - decision_time: as_recorded | forged_late. The Decision's own time; forged_late stamps it ten seconds after the
      executing call, with the witnesses five seconds after that call.
    - pd_role, twin_role: match | other | none. The role the corroborating PolicyDecision / relabel twin records
      for the principal (CND-04's role witness for a self_report terminus).
    - principal_role: declared_match | declared_other | not_declared | no_role | no_scope. Whether the binding
      authorises the principal's role for the task (SPEC §7.7.4 "for the task type", §7.7.6): its list names
      credit_officer; names another role; is not declared; the principal carries no role; task-1 resolves to no
      scope (see :func:`_task_scopes`).
    - integrity / integrity_stream: see the module docstring; the stream is a SOURCES role.
    """
    who = "instruction_chain"
    cls = _pick(dims, "instruction_class", EP, (EP, SR, IS), who)
    declared = _pick(dims, "source_class", "user", SOURCE_CLASSES, who)
    principal = (
        PRINCIPAL
        if _pick(dims, "principal", "absent", ("absent", "present"), who) == "present"
        else None
    )
    origin = _pick(
        dims,
        "origin",
        "none",
        (
            "none",
            "ep_read_trusted",
            "ep_read_untrusted",
            "ep_read_no_trustmin",
            "sr_read",
            "tool_call",
            "resource_access",
            "not_held",
            "bare_id",
            "quarantined",
            "modelcall",
        ),
        who,
    )
    depth = int(_pick(dims, "chain_depth", "1", ("1", "2", "3"), who))
    parent_cls = _pick(dims, "parent_class", "same", ("same", EP, SR), who)
    parent_declared = _pick(
        dims,
        "parent_source_class",
        "same",
        ("same", "user", "tool_output", "missing"),
        who,
    )
    held = _pick(
        dims,
        "instruction_held",
        "held",
        (
            "held",
            "not_held",
            "bare_id",
            "quarantined_schema",
            "quarantined_time_order",
            "other_subject",
            "absent",
            "null",
        ),
        who,
    )
    acted_on = _pick(
        dims,
        "acted_on",
        "ToolCall",
        (
            "ToolCall",
            "consequential_ToolCall",
            "consequential_Decision",
            "Decision_only",
            "both",
            "ModelCall",
            "minor_Decision",
        ),
        who,
    )
    call_cls = _pick(dims, "call_class", EP, (EP, SR), who)
    unrelated = _pick(dims, "unrelated_ep", "absent", ("absent", "present"), who)
    corroboration = _pick(
        dims,
        "corroboration",
        "none",
        (
            "none",
            "ep_pd_principal",
            "ep_pd_other_principal",
            "ep_pd_no_principal",
            "sr_pd_principal",
            "ep_pd_deny",
            "is_pd_principal",
        ),
        who,
    )
    relabel = _pick(
        dims, "relabel", "none", ("none", "ep_twin_untrusted", "ep_twin_agrees"), who
    )
    delegation = _pick(
        dims,
        "delegation",
        "none",
        ("none", "ep_verified", "ep_unverified", "sr_verified"),
        who,
    )
    refusal = _pick(dims, "refusal", "none", ("none", "sr_refusal", "ep_refusal"), who)
    memory = _pick(dims, "memory", "none", ("none", "clean"), who)
    twin_kind = _pick(dims, "twin", "same", TWINS, who)
    pd_request = _pick(
        dims, "pd_request", "acting_call", ("acting_call", "other_call"), who
    )
    witness_time = _pick(dims, "witness_time", "before", WITNESS_TIMES, who)
    call_time = _pick(
        dims,
        "call_time",
        "as_recorded",
        ("as_recorded", "missing", "unparseable", "forged_late"),
        who,
    )
    decision_time = _pick(
        dims, "decision_time", "as_recorded", ("as_recorded", "forged_late"), who
    )
    cycle = _pick(dims, "parent_cycle", "none", ("none", "self", "pair"), who)
    integrity = _pick(dims, "integrity", "verified", INTEGRITY, who)
    stream = _pick(dims, "integrity_stream", "ingress", tuple(SOURCES), who)
    role_decl = _pick(
        dims,
        "principal_role",
        "declared_match",
        ("declared_match", "declared_other", "not_declared", "no_role", "no_scope"),
        who,
    )
    pd_role = _pick(dims, "pd_role", "match", ("match", "other", "none"), who)
    twin_role = _pick(dims, "twin_role", "match", ("match", "other", "none"), who)
    if principal and role_decl == "no_role":
        principal = {k: v for k, v in principal.items() if k != "role"}
    if twin_kind in MALFORMED_PRINCIPAL:
        principal = MALFORMED_PRINCIPAL[twin_kind]
    if pd_request == "other_call":
        unrelated = "present"

    def role_of(c: str) -> str:
        return {EP: "ingress", SR: "agent", IS: "sor"}[c]

    ev = _Events()
    if memory == "clean":
        ev.add(
            "w0",
            "MemoryWrite",
            "guard",
            store="kb",
            record_ref="mem:r0",
            trust="trusted",
            guard_verdict="allow",
        )
        ev.add(
            "g0",
            "MemoryRead",
            "guard",
            store="kb",
            record_refs=["mem:r0"],
            trust_min="trusted",
        )
    origin_ref: str | None = None
    if origin in (
        "ep_read_trusted",
        "ep_read_untrusted",
        "ep_read_no_trustmin",
        "sr_read",
    ):
        ev.add(
            "w1",
            "MemoryWrite",
            "guard",
            store="kb",
            record_ref="mem:r1",
            trust="trusted",
            guard_verdict="allow",
        )
        if origin == "sr_read":
            origin_ref = ev.add(
                "m1", "MemoryRead", "agent", store="kb", record_refs=["mem:r1"]
            )
        else:
            trust_min = {
                "ep_read_trusted": "trusted",
                "ep_read_untrusted": "untrusted",
            }.get(origin)
            origin_ref = ev.add(
                "m1",
                "MemoryRead",
                "guard",
                store="kb",
                record_refs=["mem:r1"],
                trust_min=trust_min,
            )
    elif origin == "tool_call":
        origin_ref = ev.add(
            "tc0",
            "ToolCall",
            "gateway",
            agent={"id": SUBJECT},
            tool={"name": "lookup", "protocol": "native"},
            effect_class="read",
        )
    elif origin == "resource_access":
        origin_ref = ev.add(
            "ra0",
            "ResourceAccess",
            "gateway",
            agent={"id": SUBJECT},
            operation="read",
            resource={
                "uri": "https://docs.example/a",
                "kind": "document",
                "classification": "public",
            },
        )
    elif (
        origin == "quarantined"
    ):  # a guard read whose trust_min is outside the enumeration: schema_invalid
        ev.add(
            "w1",
            "MemoryWrite",
            "guard",
            store="kb",
            record_ref="mem:r1",
            trust="trusted",
            guard_verdict="allow",
        )
        origin_ref = ev.add(
            "m1",
            "MemoryRead",
            "guard",
            store="kb",
            record_refs=["mem:r1"],
            trust_min="sometimes",
        )
    elif (
        origin == "modelcall"
    ):  # model output named as the origin (no type SPEC §6.2.3 allows)
        origin_ref = ev.add(
            "mc0",
            "ModelCall",
            "agent",
            agent={"id": SUBJECT},
            operation="chat",
            model={"provider": "p", "name": "m", "version_or_digest": "1"},
        )
    elif origin == "not_held":
        origin_ref = "agentce:event/gone"
    elif origin == "bare_id":
        ev.add(
            "i9",
            "Instruction",
            "ingress",
            source_class="tool_output",
            content_ref="sha256:" + "9" * 64,
        )
        origin_ref = "i9"

    def member(value: str) -> str | None:
        return None if value == "missing" else value

    content = "sha256:" + "a" * 64
    instruction_subject = OTHER if held == "other_subject" else SUBJECT
    if held == "quarantined_time_order":
        ev.add(
            "i0",
            "Instruction",
            role_of(cls),
            subject=instruction_subject,
            source_class="user",
            principal=principal,
            content_ref="sha256:" + "0" * 64,
            refs={"task": "task-1"},
        )
    parent: str | None = None
    for hop in range(
        depth, 0, -1
    ):  # the root (hop = depth) first, the acted-on instruction i1 last
        refs = {
            k: v
            for k, v in (
                ("origin", origin_ref if hop == depth else None),
                ("parent", parent),
                ("task", "task-1"),
            )
            if v
        }
        hop_cls = cls if hop == 1 or parent_cls == "same" else parent_cls
        hop_declared = (
            declared
            if hop == 1 or parent_declared == "same" or hop != depth
            else parent_declared
        )
        if hop == 1 and held == "quarantined_schema":
            hop_declared = "root"  # outside the Appendix F enumeration: ingest quarantines it schema_invalid
        time = (
            "2026-01-01T23:00:00Z"
            if hop == 1 and held == "quarantined_time_order"
            else None
        )
        parent = ev.add(
            f"i{hop}",
            "Instruction",
            role_of(hop_cls),
            subject=instruction_subject,
            time=time,
            source_class=member(hop_declared),
            principal=principal,
            content_ref=content if hop == 1 else f"sha256:{hop:064d}",
            refs=refs or None,
        )
    if cycle != "none":
        acted = next(e for e in ev.items if e["id"] == "i1")
        if cycle == "pair":
            parent = ev.add(
                "i2",
                "Instruction",
                "ingress",
                source_class="user",
                principal=PRINCIPAL,
                content_ref="sha256:" + "d" * 64,
                refs={"parent": "agentce:event/i1", "task": "task-1"},
            )
        acted["data"].setdefault("refs", {})["parent"] = (
            "agentce:event/i1" if cycle == "self" else parent
        )
        parent = "agentce:event/i1"
    if relabel != "none":
        twin = "tool_output" if relabel == "ep_twin_untrusted" else declared
        if twin_kind == "other_authorised_class":
            twin = "operator" if declared != "operator" else "user"
        twin_principal = (
            principal
            if twin in ("user", "operator", "service", "agent_identified")
            else None
        )
        twin_principal = {"other_principal": OTHER_PRINCIPAL, "no_principal": None}.get(
            twin_kind, twin_principal
        )
        twin_refs: dict[str, str] = {
            "task": "task-2" if twin_kind == "other_task" else "task-1"
        }
        if twin_kind in ("origin_ep_read_untrusted", "origin_sr_read"):
            ev.add(
                "w2",
                "MemoryWrite",
                "guard",
                store="kb",
                record_ref="mem:r2",
                trust="trusted",
                guard_verdict="allow",
            )
            twin_refs["origin"] = ev.add(
                "m2",
                "MemoryRead",
                "agent" if twin_kind == "origin_sr_read" else "guard",
                store="kb",
                record_refs=["mem:r2"],
                trust_min=None if twin_kind == "origin_sr_read" else "untrusted",
            )
        if twin_kind == "parent_tool_output":
            twin_refs["parent"] = ev.add(
                "i1-ingress-parent",
                "Instruction",
                "ingress",
                source_class="tool_output",
                content_ref="sha256:" + "b" * 64,
                refs={"task": "task-1"},
            )
        twin_principal = _with_role(twin_principal, twin_role)
        ev.add(
            "i1-ingress",
            "Instruction",
            "ingress",
            subject=OTHER if twin_kind == "other_subject" else SUBJECT,
            source_class=member(twin),
            principal=twin_principal,
            content_ref="sha256:" + "c" * 64
            if twin_kind == "other_content"
            else content,
            refs=twin_refs,
        )
    instruction = {
        "held": parent,
        "not_held": "agentce:event/gone",
        "bare_id": "i1",
        "quarantined_schema": parent,
        "quarantined_time_order": parent,
        "other_subject": parent,
        "absent": None,
        "null": None,
    }[held]

    deleg_ref: str | None = None
    if delegation != "none":
        chain = [
            {"id": PRINCIPAL["id"], "kind": "human"},
            {"id": SUBJECT, "kind": "agent"},
        ]
        data: dict[str, Any] = {
            "token_ref": "jti-1",
            "issuer": "https://idp.example",
            "chain": chain,
            "subject_principal": PRINCIPAL["id"],
            "actor_principal": SUBJECT,
            "scope_granted": ["credit:read"],
            "scope_parent": ["credit:read", "credit:write"],
            "expires": "2026-02-01T00:00:00Z",
        }
        data["verification"] = {
            "status": "failed" if delegation == "ep_unverified" else "verified",
            "method": "rfc8693",
        }
        deleg_ref = ev.add(
            "dl1",
            "DelegationIssued",
            "agent" if delegation.startswith("sr") else "policy",
            **data,
        )

    call_role = "gateway" if call_cls == EP else "agent"
    has_call = acted_on in (
        "ToolCall",
        "consequential_ToolCall",
        "consequential_Decision",
        "both",
    )
    if (
        corroboration != "none"
    ):  # recorded before the acting call (witness_time moves it on the clock)
        target = (
            "agentce:event/tc1"
            if has_call and pd_request == "acting_call"
            else "agentce:event/tcx"
        )
        role = {"sr_pd_principal": "agent", "is_pd_principal": "sor"}.get(
            corroboration, "policy"
        )
        pd_principal = {
            "ep_pd_other_principal": OTHER_PRINCIPAL,
            "ep_pd_no_principal": None,
        }.get(corroboration, PRINCIPAL)
        pd_principal = _with_role(pd_principal, pd_role)
        ev.add(
            "pd1",
            "PolicyDecision",
            role,
            engine="custom",
            policy_id="ingress",
            policy_version="1",
            policy_digest="sha256:" + "0" * 64,
            decision="deny" if corroboration == "ep_pd_deny" else "allow",
            reasons=[],
            obligations=[],
            principal=pd_principal,
            refs={"request": target},
        )
    decision: str | None = None
    if acted_on in (
        "consequential_ToolCall",
        "consequential_Decision",
        "Decision_only",
        "both",
        "minor_Decision",
    ):
        dtype = "dom:Minor" if acted_on == "minor_Decision" else "dom:CreditDecision"
        decision_refs = (
            {"instruction": instruction}
            if acted_on != "consequential_ToolCall" and held != "absent"
            else None
        )
        decision = ev.add(
            "d1",
            "Decision",
            "agent",
            agent={"id": SUBJECT},
            decision_type=dtype,
            acted_for=[PRINCIPAL["id"]],
            inputs=["mem:r0"] if memory == "clean" else None,
            refs=decision_refs,
        )
    if has_call:
        call_refs: dict[str, Any] = {}
        if (
            acted_on in ("ToolCall", "consequential_ToolCall", "both")
            and held != "absent"
        ):
            call_refs["instruction"] = None if held == "null" else str(instruction)
        if decision and acted_on != "minor_Decision":
            call_refs["decision"] = decision
        if deleg_ref:
            call_refs["delegation"] = deleg_ref
        ev.add(
            "tc1",
            "ToolCall",
            call_role,
            agent={"id": SUBJECT},
            acted_for=[PRINCIPAL["id"]],
            tool={"name": "lookup", "protocol": "native"},
            effect_class="read",
            refs=call_refs,
        )
    if acted_on == "ModelCall":
        ev.add(
            "mc1",
            "ModelCall",
            "agent",
            agent={"id": SUBJECT},
            operation="chat",
            model={"provider": "p", "name": "m", "version_or_digest": "1"},
            refs={"instruction": str(instruction)},
        )
    if unrelated == "present" or acted_on in (
        "Decision_only",
        "ModelCall",
        "minor_Decision",
    ):
        # The bystander acts on its own attested user instruction (ix, written last so no stream's order moves):
        # a ToolCall that names no instruction reads insufficient_evidence for CND-05 (SPEC §6.2.3, HR-5).
        ev.add(
            "tcx",
            "ToolCall",
            "gateway",
            agent={"id": SUBJECT},
            tool={"name": "ping", "protocol": "native"},
            effect_class="read",
            refs={"instruction": "agentce:event/ix"},
        )
    _retime(ev, witness_time, call_time, decision_time)
    if refusal != "none":
        ev.add(
            "rf1",
            "Refusal",
            "agent" if refusal == "sr_refusal" else "gateway",
            agent={"id": SUBJECT},
            reason_class="untrusted_source",
            refs={"instruction": str(instruction)},
        )
    if any(e["id"] == "tcx" for e in ev.items):
        ev.add(
            "ix",
            "Instruction",
            "ingress",
            source_class="user",
            principal=PRINCIPAL,
            content_ref="sha256:" + "7" * 64,
        )

    write_bundle(
        dest / "bundle", ev.items, integrity=integrity, integrity_stream=stream
    )
    catalogs = list(args.get("catalogs") or CONDUCT)
    profile, domain = write_profile(dest, catalogs, roles=role_decl)
    return Fixture(dest / "bundle", profile, domain, SUBJECT)


# --- memory (ROB-02) -------------------------------------------------------------------------------------------------


def memory_case(
    dest: Path, root: Path, dims: dict[str, Any], args: dict[str, Any]
) -> Fixture:
    """A consequential decision and the memory it used, under the baseline catalog (ROB-02, SPEC §7.4).

    Dimensions read (default first):
    - write: ep_trusted | none | ep_untrusted | ep_quarantined | ep_block | ep_sanitize | sr_trusted | sr_untrusted |
      unmarked_untrusted_origin | unmarked_trusted_origin | unmarked_untrusted_nested. The write of record mem:r1
      (unmarked: no trust and no verdict, only an origin class, flat as the model generates it or nested as SPEC
      §6.2.3 writes it).
    - read: ep_trusted | none | ep_untrusted | ep_no_trustmin | sr. The read of mem:r1.
    - path: inputs | consumer | via_modelcall | via_toolcall | instruction_origin. How the record reaches the
      consequential decision: the decision lists it in inputs; the read names the decision as refs.consumer; the read
      feeds a model call whose output the decision used; a tool call used it and the decision used the call's result;
      an enforcement-point memory_trusted instruction has the read as refs.origin and the decision acts on it;
      nothing: the decision lists no input and names nothing (the read and write stay in the subject).
    - write_subject: same | other. The write recorded under another subject (18.120).
    - arrival_order: in_order | late_time_order. A later untrusted write of mem:r1 by the guard arrives more than
      five minutes late in its stream, so ingest quarantines it for time_order (18.119).
    - unrelated_ep: absent | present. An enforcement-point read of another record, trust_min trusted, used by nothing.
    - decision_class: self_report | enforcement_point. The decision's own class.
    - integrity / integrity_stream: as in the module docstring (the guard's stream is ``guard``).
    """
    who = "memory_case"
    write = _pick(
        dims,
        "write",
        "ep_trusted",
        (
            "ep_trusted",
            "none",
            "ep_untrusted",
            "ep_quarantined",
            "ep_block",
            "ep_sanitize",
            "sr_trusted",
            "sr_untrusted",
            "unmarked_untrusted_origin",
            "unmarked_trusted_origin",
            "unmarked_untrusted_nested",
        ),
        who,
    )
    read = _pick(
        dims,
        "read",
        "ep_trusted",
        ("ep_trusted", "none", "ep_untrusted", "ep_no_trustmin", "sr"),
        who,
    )
    path = _pick(
        dims,
        "path",
        "inputs",
        (
            "inputs",
            "consumer",
            "via_modelcall",
            "via_toolcall",
            "instruction_origin",
            "nothing",
        ),
        who,
    )
    write_subject = _pick(dims, "write_subject", "same", ("same", "other"), who)
    arrival = _pick(
        dims, "arrival_order", "in_order", ("in_order", "late_time_order"), who
    )
    unrelated = _pick(dims, "unrelated_ep", "absent", ("absent", "present"), who)
    dcls = _pick(dims, "decision_class", SR, (SR, EP), who)
    integrity = _pick(dims, "integrity", "verified", INTEGRITY, who)
    stream = _pick(dims, "integrity_stream", "guard", tuple(SOURCES), who)

    ev = _Events()
    wsubject = OTHER if write_subject == "other" else SUBJECT
    if write != "none":
        role = "agent" if write.startswith("sr") else "guard"
        data: dict[str, Any] = {"store": "kb", "record_ref": "mem:r1"}
        if (
            write == "unmarked_untrusted_nested"
        ):  # SPEC §6.2.3's nested form, provenance {origin_ref, origin_class}
            data["provenance"] = {
                "origin_ref": "urn:doc:1",
                "origin_class": "retrieved",
            }
        elif write.startswith(
            "unmarked"
        ):  # the generated model's flat member, as the gate writes it
            data["provenance_origin_class"] = (
                "retrieved" if write.endswith("untrusted_origin") else "user"
            )
        else:
            data["trust"] = {
                "ep_untrusted": "untrusted",
                "sr_untrusted": "untrusted",
                "ep_quarantined": "quarantined",
            }.get(write, "trusted")
            data["guard_verdict"] = {
                "ep_block": "block",
                "ep_sanitize": "sanitize",
                "ep_quarantined": "quarantine",
            }.get(write, "allow")
        ev.add("w1", "MemoryWrite", role, subject=wsubject, **data)
    if unrelated == "present":
        ev.add(
            "w9",
            "MemoryWrite",
            "guard",
            store="kb",
            record_ref="mem:r9",
            trust="trusted",
            guard_verdict="allow",
        )
        ev.add(
            "g9",
            "MemoryRead",
            "guard",
            store="kb",
            record_refs=["mem:r9"],
            trust_min="trusted",
        )
    consumer_target = {
        "consumer": "agentce:event/d1",
        "via_modelcall": "agentce:event/mc1",
    }.get(path)
    read_ref: str | None = None
    if read != "none":
        trust_min = {"ep_trusted": "trusted", "ep_untrusted": "untrusted"}.get(read)
        read_ref = ev.add(
            "m1",
            "MemoryRead",
            "agent" if read == "sr" else "guard",
            store="kb",
            record_refs=["mem:r1"],
            trust_min=trust_min,
            refs={"consumer": consumer_target} if consumer_target else None,
        )
    inputs: list[str] | None = None
    drefs: dict[str, str] | None = None
    if path == "inputs":
        inputs = ["mem:r1"]
    elif path == "via_modelcall":
        ev.add(
            "mc1",
            "ModelCall",
            "agent",
            agent={"id": SUBJECT},
            operation="chat",
            model={"provider": "p", "name": "m", "version_or_digest": "1"},
            input_ref="mem:r1",
            output_ref="sha256:" + "c" * 64,
        )
        inputs = ["sha256:" + "c" * 64]
    elif path == "via_toolcall":
        ev.add(
            "tc0",
            "ToolCall",
            "gateway",
            agent={"id": SUBJECT},
            tool={"name": "lookup", "protocol": "native"},
            effect_class="read",
            used=["mem:r1"],
            result_ref="sha256:" + "d" * 64,
        )
        inputs = ["sha256:" + "d" * 64]
    elif path == "instruction_origin":
        if read_ref is None:
            raise FixtureError("memory_case path=instruction_origin needs a read")
        ev.add(
            "i1",
            "Instruction",
            "ingress",
            source_class="memory_trusted",
            content_ref="sha256:" + "a" * 64,
            refs={"origin": read_ref, "task": "task-1"},
        )
        drefs = {"instruction": "agentce:event/i1"}
    ev.add(
        "d1",
        "Decision",
        "agent" if dcls == SR else "agent_ep",
        agent={"id": SUBJECT},
        decision_type="dom:CreditDecision",
        inputs=inputs,
        refs=drefs,
    )
    if arrival == "late_time_order":
        ev.add(
            "w2",
            "MemoryWrite",
            "guard",
            subject=wsubject,
            time="2026-01-01T23:00:00Z",
            store="kb",
            record_ref="mem:r1",
            trust="untrusted",
            guard_verdict="quarantine",
        )
    write_bundle(
        dest / "bundle", ev.items, integrity=integrity, integrity_stream=stream
    )
    profile, domain = write_profile(
        dest, list(args.get("catalogs") or ["baseline@2026.09"])
    )
    return Fixture(dest / "bundle", profile, domain, SUBJECT)


# --- the other controls whose min_source_class is above self_report ------------------------------------------------

#: focus_case scenarios: control id -> what the focus event is.
FOCUS_SCENARIOS = (
    "consequential_toolcall",
    "incident",
    "oversight_chain",
    "effect_scope",
    "approval",
    "budget",
    "refusal",
    "manual_decision",
    "harm_signal",
    "parity",
)


def focus_case(
    dest: Path, root: Path, dims: dict[str, Any], args: dict[str, Any]
) -> Fixture:
    """One focus event for a control whose min_source_class is above self_report (fixture_args.scenario names it).

    Dimensions read (default first):
    - focus_class: enforcement_point | independent_system | self_report. The focus event's own class.
    - shape_verdict: pass | fail. Whether the control's expectation holds on the focus, independent of its class.
    - unrelated_ep: absent | present. An enforcement-point ToolCall (and, for scenarios whose minimum evidence names
      another type, that type) elsewhere in the subject, linked to nothing.
    - login_class: independent_system | self_report. The identity-provider login the approval names (approval).
    - coverage: reconciled | unknown. An independent system-of-record denominator counting every event of the
      subject (SPEC §6.5), or none (coverage unknown: 18.134's INT and REC rule).
    - integrity / integrity_stream: as in the module docstring; ``focus`` names the focus event's own stream.

    Scenarios (fixture_args.scenario; fixture_args.catalogs overrides the catalogs):
    - consequential_toolcall (INT-01, INT-04, OVS-06, ROB-01, ROB-05, ROB-06, DOC-03): a ToolCall executing a
      consequential Decision; shape fail = the call's own class is self_report while focus_class is its declared
      source's class (so a fail is the shape's own failure).
    - incident (INC-02): an Incident; shape fail = no agent named.
    - oversight_chain (OVS-03): a consequential call under a DelegationIssued whose chain ends at a human; the focus
      is the DelegationIssued; shape fail = verification failed.
    - effect_scope (CND-01): a ToolCall of effect class irreversible in a task whose scope allows read only; shape
      pass = effect class read.
    - approval (CND-02): a consequential decision approved by a human through an ApprovalDecided; the focus is the
      approval; shape fail = no approval.
    - budget (CND-07): a ToolCall of effect class spend over a zero spend budget, recorded with usage; shape pass =
      no spend.
    - refusal (CND-06): an untrusted (tool_output) instruction not acted on; the focus is its Refusal; shape fail =
      no refusal.
    - manual_decision (EMP-01, FIN-01, INS-01, ORG-01): a consequential Decision of the focus class, no checklist.
    - harm_signal (CND-03): an adverse Outcome of a consequential decision, followed by an Incident, of the focus class.
    - parity (CND-08): adverse Outcomes of the focus class.
    """
    who = "focus_case"
    scenario = str(args.get("scenario", ""))
    if scenario not in FOCUS_SCENARIOS:
        raise FixtureError(
            f"focus_case needs fixture_args.scenario in {FOCUS_SCENARIOS}, got {scenario!r}"
        )
    fcls = _pick(dims, "focus_class", EP, (EP, IS, SR), who)
    verdict = _pick(dims, "shape_verdict", "pass", ("pass", "fail"), who)
    unrelated = _pick(dims, "unrelated_ep", "absent", ("absent", "present"), who)
    login_cls = _pick(dims, "login_class", IS, (IS, SR), who)
    coverage = _pick(dims, "coverage", "reconciled", ("reconciled", "unknown"), who)
    integrity = _pick(dims, "integrity", "verified", INTEGRITY, who)
    stream = _pick(dims, "integrity_stream", "focus", tuple(SOURCES) + ("focus",), who)
    role = {EP: "agent_ep", IS: "sor", SR: "agent"}[fcls]
    if fcls == EP and scenario in ("consequential_toolcall", "effect_scope", "budget"):
        role = "gateway"
    if fcls == EP and scenario in ("oversight_chain",):
        role = "policy"
    ev = _Events()
    catalogs = list(args.get("catalogs") or ["eu-ai-act@2026.09"])
    oversight = None
    if unrelated == "present":
        ev.add(
            "tcx",
            "ToolCall",
            "gateway",
            agent={"id": SUBJECT},
            tool={"name": "ping", "protocol": "native"},
            effect_class="read",
        )
    if scenario == "consequential_toolcall":
        ev.add(
            "d1",
            "Decision",
            "agent",
            agent={"id": SUBJECT},
            decision_type="dom:CreditDecision",
        )
        call_role = role if verdict == "pass" else "agent"
        ev.add(
            "tc1",
            "ToolCall",
            call_role,
            agent={"id": SUBJECT},
            tool={"name": "lookup", "protocol": "native"},
            effect_class="write",
            refs={"decision": "agentce:event/d1"},
        )
    elif scenario == "incident":
        ev.add(
            "in1",
            "Incident",
            role,
            agent={"id": SUBJECT} if verdict == "pass" else None,
            incident_id="inc-1",
            incident_class="fundamental_rights",
            detected_at="2026-01-02T00:00:00Z",
            reported_at="2026-01-02T01:00:00Z",
            related_refs=[],
        )
    elif scenario == "oversight_chain":
        chain = [
            {"id": PRINCIPAL["id"], "kind": "human"},
            {"id": SUBJECT, "kind": "agent"},
        ]
        ev.add(
            "dl1",
            "DelegationIssued",
            role,
            token_ref="jti-1",
            issuer="https://idp.example",
            chain=chain,
            subject_principal=PRINCIPAL["id"],
            actor_principal=SUBJECT,
            scope_granted=["credit:write"],
            scope_parent=["credit:write"],
            expires="2026-02-01T00:00:00Z",
            verification={
                "status": "verified" if verdict == "pass" else "failed",
                "method": "rfc8693",
            },
        )
        ev.add(
            "d1",
            "Decision",
            "agent",
            agent={"id": SUBJECT},
            decision_type="dom:CreditDecision",
            oversight_modality="review_before",
            acted_for=[PRINCIPAL["id"]],
        )
        ev.add(
            "tc1",
            "ToolCall",
            "gateway",
            agent={"id": SUBJECT},
            tool={"name": "lookup", "protocol": "native"},
            effect_class="write",
            acted_for=[PRINCIPAL["id"]],
            refs={"decision": "agentce:event/d1", "delegation": "agentce:event/dl1"},
        )
        oversight = "review_before"
    elif scenario == "effect_scope":
        ev.add(
            "tc1",
            "ToolCall",
            role,
            agent={"id": SUBJECT},
            tool={"name": "lookup", "protocol": "native"},
            effect_class="read" if verdict == "pass" else "irreversible",
            refs={"task": "task-1"},
        )
        ev.items[-1]["agentcetask"] = "task-1"
    elif scenario == "approval":
        ev.add(
            "login-u1",
            "SessionStart",
            "sor" if login_cls == IS else "agent",
            cls=login_cls,
            agent={"id": "urn:example:service:identity-provider"},
            environment="idp-login",
            principal={"id": "u1", "kind": "human"},
        )
        ev.add(
            "d1",
            "Decision",
            "agent",
            agent={"id": SUBJECT},
            decision_type="dom:CreditDecision",
        )
        if verdict == "pass":
            ev.add(
                "ap1",
                "ApprovalDecided",
                role,
                outcome="approve",
                refs={"decision": "agentce:event/d1"},
                actor={
                    "id": "u1",
                    "kind": "human",
                    "authority_ref": "ref:authority/officers",
                },
                session_ref="agentce:event/login-u1",
                latency_ms=1000,
            )
        ev.add(
            "tc1",
            "ToolCall",
            "gateway",
            agent={"id": SUBJECT},
            tool={"name": "lookup", "protocol": "native"},
            effect_class="irreversible",
            refs={"decision": "agentce:event/d1"},
        )
    elif scenario == "budget":
        ev.add(
            "tc1",
            "ToolCall",
            role,
            agent={"id": SUBJECT},
            tool={"name": "pay", "protocol": "native"},
            effect_class="read" if verdict == "pass" else "spend",
        )
        ev.items[-1]["agentcetask"] = "task-1"
        ev.add(
            "mc1",
            "ModelCall",
            "agent",
            agent={"id": SUBJECT},
            operation="chat",
            model={"provider": "p", "name": "m", "version_or_digest": "1"},
            usage={
                "input_tokens": 10 if verdict == "pass" else 5000,
                "output_tokens": 10,
            },
        )
        ev.items[-1]["agentcetask"] = "task-1"
    elif scenario == "refusal":
        ev.add(
            "i1",
            "Instruction",
            "ingress",
            source_class="tool_output",
            content_ref="sha256:" + "a" * 64,
            refs={"task": "task-1"},
        )
        if verdict == "pass":
            ev.add(
                "rf1",
                "Refusal",
                role,
                agent={"id": SUBJECT},
                reason_class="untrusted_source",
                refs={"instruction": "agentce:event/i1"},
            )
        ev.add(
            "tcy",
            "ToolCall",
            "gateway",
            agent={"id": SUBJECT},
            tool={"name": "ping", "protocol": "native"},
            effect_class="read",
        )
    elif scenario == "manual_decision":
        ev.add(
            "d1",
            "Decision",
            role,
            agent={"id": SUBJECT},
            decision_type="dom:CreditDecision",
        )
    elif scenario == "harm_signal":
        ev.add(
            "d1",
            "Decision",
            "agent",
            agent={"id": SUBJECT},
            decision_type="dom:CreditDecision",
        )
        ev.add(
            "o1",
            "Outcome",
            role,
            refs={"decision": "agentce:event/d1"},
            outcome_type="denial",
            observed_at="2026-01-02T00:00:05Z",
            adverse=True,
            reversed=False,
        )
        if verdict == "pass":
            ev.add(
                "in1",
                "Incident",
                role,
                agent={"id": SUBJECT},
                incident_id="inc-1",
                incident_class="fundamental_rights",
                detected_at="2026-01-02T00:00:06Z",
                reported_at="2026-01-02T00:01:00Z",
                related_refs=["agentce:event/o1"],
            )
        ev.add(
            "tcy",
            "ToolCall",
            "gateway",
            agent={"id": SUBJECT},
            tool={"name": "ping", "protocol": "native"},
            effect_class="read",
        )
    elif scenario == "parity":
        for n in range(3):
            ev.add(
                f"d{n}",
                "Decision",
                "agent",
                agent={"id": SUBJECT},
                decision_type="dom:CreditDecision",
            )
            ev.add(
                f"o{n}",
                "Outcome",
                role,
                refs={"decision": f"agentce:event/d{n}"},
                outcome_type="denial",
                observed_at="2026-01-02T00:00:05Z",
                adverse=verdict == "fail" or n == 0,
                reversed=False,
            )
        ev.add(
            "tcy",
            "ToolCall",
            "gateway",
            agent={"id": SUBJECT},
            tool={"name": "ping", "protocol": "native"},
            effect_class="read",
        )
    target = role if stream == "focus" else stream
    (dest / "bundle").mkdir()
    denominator = (
        reconcile(dest / "bundle", ev.items) if coverage == "reconciled" else None
    )
    write_bundle(
        dest / "bundle", ev.items, integrity=integrity, integrity_stream=target
    )
    profile, domain = write_profile(
        dest, catalogs, oversight=oversight, denominator=denominator
    )
    return Fixture(dest / "bundle", profile, domain, SUBJECT)


def gate_case(
    dest: Path, root: Path, dims: dict[str, Any], args: dict[str, Any]
) -> Fixture:
    """One case of a gate's cases.json, verbatim: fixture_args {gate: cnd05_chain | rob02_untrusted, case: <name>}.
    The gate's own events, manifest shape and profile; no integrity block (as the gate writes them)."""
    del dims
    gate, name = str(args.get("gate", "")), str(args.get("case", ""))
    subjects = {
        "cnd05_chain": SUBJECT,
        "rob02_untrusted": "spiffe://corp/agents/rob02-fixture",
    }
    if gate not in subjects:
        raise FixtureError(
            f"gate_case needs fixture_args.gate in {sorted(subjects)}, got {gate!r}"
        )
    cases = json.loads(
        (root / GATE_FIXTURES / gate / "cases.json").read_text(encoding="utf-8")
    )["cases"]
    if name not in cases:
        raise FixtureError(f"gate_case: {gate}/cases.json has no case {name!r}")
    write_bundle(
        dest / "bundle",
        cases[name]["events"],
        domain=gate.split("_")[0] + "-fixture",
        sources=[{"id": f"urn:src:{c}", "class": c} for c in (EP, SR)],
    )
    profile, domain = gate_files(root, gate)
    return Fixture(dest / "bundle", profile, domain, subjects[gate])
