---
name: agentce-check-report
description: >
  Verify an Agent Conformance Engine report before it is signed: check the human-supplied inputs the
  report depends on, run the assessment, complete the manual checklists and deviations, and compute
  the readiness verdict. Use when asked to run the assessment, prepare the conformance report for
  review, decide whether a report can be signed, complete the manual checks, record a deviation, or
  explain an insufficient-evidence or non-conformant outcome.
license: Apache-2.0
metadata:
  spec_version: "0.6"
  cli_version: ">=0.0.1,<0.1"
  catalog_versions: ["eu-ai-act@2026.09"]
  skill_version: "1.0.0"
---

# agentce-check-report

Prepare an AgentCE report for review and signature. This skill checks the inputs the report inherits,
runs the deterministic assessment, records the human-supplied manual checks and deviations, and
computes the **readiness verdict** — `READY`, `READY WITH LIMITATIONS`, or `NOT READY`. The verdict
logic lives in the engine (`agentce readiness`), never in this skill, so signing never depends on a
script. The skill never modifies code, evidence, `assertions.json`, or the manifest; never relabels an
outcome; never creates a deviation without a named approver and expiry; and never signs anything.

## Version pins

Every script verifies these pins against the installed engine before doing anything (S-5) and stops
with exit 3 on a mismatch: spec `0.6`, CLI `>=0.0.1,<0.1`, catalog `eu-ai-act@2026.09`.

## Stage 1 — Pre-run checks (`claim_check`)

Before the engine runs, the observation window, scope, sources, versions, declarations, and
third-party components are inputs the report inherits. Confirm the profile's window lies within the
bundle's timestamps; every subject in the profile has events and vice versa; every declared source
appears in the manifest with its trust class; catalog and engine versions satisfy the pins.

## Stage 2 — Run

`agentce verify` then `agentce assess` with `--manual` and `--deviations` pointed at the records from
stage 3. A first run without them is allowed, to discover which manual controls apply.

## Stage 3 — Human-supplied records

- **Manual checklists** (`checklist_lint`). For every applicable control with `mode: manual` or the
  manual part of `semi-automated`, present the catalog's checklist items and the named artifact to
  inspect, and record the answer, assessor identity and organisation, independence flag, date, and
  the digests of the artifacts inspected. `checklist_lint` enforces the schema, assessor identity, and
  digest freshness — an artifact changed since inspection invalidates the record.
- **Deviations** (`deviation_lint`). A deviation is created only for a `non-conformant` outcome the
  organisation accepts with a compensating control. `deviation_lint` enforces: the control exists; the
  outcome was `non-conformant` (never `insufficient_evidence`, which is an evidence gap, not a risk
  acceptance); rationale, compensating control, owner, approver, approval date, and expiry present;
  expiry within the catalog maximum; no deviation on the INT family; the approver is a named person
  distinct from the owner. The skill drafts the entry from the finding and the operator's rationale; it
  never sets `approved_by`.

## Stage 4 — Post-run checks (`report_readiness`)

`report_readiness` is a thin wrapper over `agentce readiness`. It blocks (NOT READY) on a failed,
gapped, or reordered integrity stream; a coverage shortfall; applicability drift; a missing manual
record; an invalid or expired deviation; an unrecorded high-severity evidence gap; or a claim that
does not match the profile. A recorded high-severity evidence gap (owner and date in `gaps.md`) is a
limitation, not a blocker. The claimant signs only on `READY` or `READY WITH LIMITATIONS`, and the
limitations text is carried into the claim.

## Stage 5 — Reading the outcomes (`read_outcomes`)

Explain the report in fixed language: `conformant` and `non-conformant` are deterministic and
reproducible; `partial` is a fail with an accepted, expiring deviation; `insufficient_evidence` is a
statement about collection, not about the control; `not_assessed` names what was not evaluated and
why; rung-3 results are measurements with intervals, never proofs; the report states conformance to a
catalog and is not a compliance determination. `read_outcomes` prints the six-outcome counts per
family and **refuses** a composite score or a "percent compliant" figure (DC-4).

## Scripts

| Script | Purpose |
|---|---|
| `claim_check.py` | Cross-check the claim's subjects, window, and catalogs against the profile. |
| `checklist_lint.py` | Validate completed manual-checklist records. |
| `deviation_lint.py` | Validate a deviation register against the §13.3.4 rules. |
| `report_readiness.py` | Compute the readiness verdict via the engine. |
| `read_outcomes.py` | Summarise the six outcomes per family; refuse a composite score. |

Each script is deterministic, offline, model-free, and emits `--json` alongside human text (S-4).
