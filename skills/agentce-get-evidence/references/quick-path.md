# The quick path: assessing a folder of trace exports

`agentce assess <folder>` reads a folder of OpenTelemetry GenAI and OpenInference trace exports (one
OTLP/JSON document per `.json` file, one per line in `.jsonl`) and runs a full assessment with no
catalog choice, no `--profile`, and no declaration (SPEC §12, §13.4 AX-1). It is the fastest way to see
what AgentCE reports on a real deployment, and the right first step before Phase A below. It is not a
substitute for Phases A-C: most of baseline@2026.09's controls need evidence this path can never
produce (see "What it cannot show" below).

## What it does

The engine reads the folder through the vendored `otel-genai` adapter, derives an evidence bundle and
a default applicability profile from what it finds (one subject per agent identity the records name,
the baseline lens, one self-reported evidence source per file), and evaluates the baseline catalog
against that profile. The derived profile sets `pilot_window: true`: a scan's observation window spans
whatever records happen to be in the folder, not a full 90-day window, and the profile says so rather
than claiming more than it saw.

## `insufficient_evidence`, never `not_applicable`

Every control the records give no population for is reported `insufficient_evidence`, never
`not_applicable`. The manifest records why (`engines/python/agentce/records/__init__.py`'s
`RECORDS_LIMITATION`):

> assessed from trace records only: traces carry no decision records and no declaration says which
> tool calls are consequential, so a control the records give no population for is reported as
> insufficient evidence rather than not applicable.

Running the same derived bundle and profile back through `assess --bundle`/`--profile` (the
hand-authored-profile path) instead declares `applicability_declared: true`, and the same empty
population then reads as `not_applicable` — a real behaviour difference between the two paths, not a
bug in either: a records-only scan asserts nothing about what is out of scope, where a declared profile
does.

## What it cannot show

`blind_spots.json`'s `checks_unlocked` names the one missing record type that would unlock the most
checks — but on baseline@2026.09 run against the vendored `otel-genai-agent-session` fixture, 9 of its
10 controls (`DOC-01`, `INC-01`, `INC-02`, `OVS-03`, `OVS-08`, `REC-01`, `REC-04`, `ROB-02`, `TRN-03`)
land under `blind_spots`/`needed_by`, not `no_population`, because they need one of:

- a declared decision type (`DOC-01`, `INC-01`, `OVS-08`, `REC-01`, `REC-04`, `ROB-02`, `TRN-03` — 7
  controls, via `ConsequentialDecision` typing),
- an `Incident` event (`INC-02`),
- consequential-tagged `ToolCall` provenance (`OVS-03`),

and none of these is suppliable from trace records alone. Only `INT-01` lands under `no_population`
(its `ToolCall` shape itself cannot be reached from records at all, declared or not). The `otel-genai`
adapter maps exactly seven event types (`adapters/otel-genai/support-matrix.yaml`): `SessionStart`,
`SessionEnd`, `ModelCall`, `ToolCall`, `ResourceAccess`, `MemoryRead`, `MemoryWrite` — never `Decision`,
`Outcome`, `Incident`, `Notice`, or `ApprovalDecided`. Closing any of the 9 gaps above needs Phases A-C:
a declared decision type or an enforcement-point/independent-system source, not another records scan.

## After the first look

Run `skills/agentce-get-evidence/scripts/lint_profile.py --profile <the derived applicability.yaml>
--json` to confirm the derived profile is clean before relying on it for anything beyond a first read,
then move to Phase A (`references/declare.md`) to declare the subjects and decision types the records
alone cannot.
