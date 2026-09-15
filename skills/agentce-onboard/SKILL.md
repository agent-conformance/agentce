---
name: agentce-onboard
description: >
  Onboard an AI-agent codebase to the Agent Conformance Engine: declare subjects and decision types,
  instrument chokepoints so the deployment emits canonical evidence, and iterate until a bundle
  validates with the minimum evidence for the target controls. Use when asked to prepare an agent for
  conformance assessment, produce AgentCE evidence, or resolve insufficient_evidence findings.
license: Apache-2.0
metadata:
  spec_version: "0.6"
  cli_version: ">=0.0.1,<0.1"
  catalog_versions: ["eu-ai-act@2026.09"]
  skill_version: "1.0.0"
---

# agentce-onboard

Prepare an AI-agent deployment so it produces **canonical AgentCE evidence** and its bundle passes
`agentce validate` and `agentce assess` with the minimum evidence for the target controls. This skill
adds capture, correlation, and declarations only — it never changes what the agent decides or does
(S-6), and it never fabricates evidence (S-1).

## When to use

- "Prepare this agent for conformance assessment" / "make this deployment emit AgentCE evidence".
- "Why is control X `insufficient_evidence`?" / "instrument for the EU AI Act assessment".
- `agentce validate` or `agentce assess` is failing on inputs.

Ask for anything missing (repository path, environments, which enforcement points exist and where
their exports land, intended purpose and affected parties per subject, target catalogs, content-capture
policy). Never guess these.

## The bar (a skill that breaks any of these is defective)

| # | Rule |
|---|---|
| S-1 | No fabricated evidence: add capture and declarations; never write an event the code did not observe. |
| S-2 | Honest trust class: agent-side emission is `self_report`; never relabel it to pass a control. |
| S-3 | Protected artifacts are read-only: never write `assertions.json`, bundles, `manifest.json`, signed claims, the deviation register, or manual records. |
| S-4 | Scripts are deterministic, offline, model-free: the model reasons; the scripts check. |
| S-5 | Version pinning: scripts verify the pins above against the engine and stop on mismatch (exit 3). |
| S-6 | Behaviour preservation: if capture would change behaviour, stop and report. |
| S-7 | Provenance: every run appends to `agentce/skill-log.jsonl`. |
| S-8 | Human confirmation: Annex III classification, decision typing, oversight, and deviations are `confirmed_by` a named person. |
| S-9 | Content minimisation: record hashes, locations, and counts — never prompt/tool content, personal data, or secrets. |
| S-10 | Signed, pinned distribution: install by commit-pinned reference. |

## Phases

**A — Declare** (`references/declare.md`). Enumerate subjects and decision types; draft the Annex III
classification (`references/annex-iii-classification.md`, `classification_status: draft_for_legal_review`
— a named reviewer confirms, never this skill); draft `agentce/domain.linkml.yaml` and validate it with
`linkml_validate`; collect reference snapshots; write `agentce/applicability.yaml` from
`assets/applicability.template.yaml` and run `lint_profile` until clean.

**B — Instrument** (`references/instrument.md`, the maintained home of the SPEC §13.1 prompt). Run
`find_chokepoints --repo . --json` and complete the inventory by reading low-confidence sites. Prefer
enforcement-point evidence (route tool, authorization, and credential calls through a gateway/policy
engine and record its request id) over agent-side logging. Enable the framework's OpenTelemetry GenAI
instrumentation, propagate W3C Trace Context, add the emitter, and emit the §13.1 events — hashed
content, deterministic ids, source timestamps, `self_report` for all agent-side emission. Record every
un-hookable chokepoint in `docs/agentce/gaps.md` (S-1: never simulate it).

**C — Validate and iterate** (`references/validate-iterate.md`). Run `bundle_preflight --bundle <b>
--profile agentce/applicability.yaml`; run `agentce assess` (never touch its outputs);
`explain_insufficient --assertions <report>/assertions.json --support-matrices <adapters>` groups
every `insufficient_evidence` outcome by root cause and names the adapter that could supply the missing
member. Apply techniques by coverage gained, re-emit, and repeat until every applicable target control
has its minimum evidence or the gap is documented as organisational.

**Definition of done:** zero schema quarantines; every declared source present; trust classes
consistent; minimum evidence met (or documented as organisational gaps); the project test suite
unchanged; `lint_profile` clean; the skill log complete.

## Trust classes, honestly

`references/trust-classes.md` is the guide. Agent-side spans and logs are `self_report`. Only a system
that could have prevented the action is an `enforcement_point`; only records the agent's delegation
chain cannot transition are `independent_system`. `lint_profile` rejects a known self-report pattern —
a framework interrupt, an SDK tracing processor, an in-process approval prompt, an agent-written log —
declared as anything else.

## Scripts (`scripts/`, each `--json`, exit 0 ok / 1 findings / 2 input error / 3 version mismatch)

- `find_chokepoints.py --repo <path>` — inventory chokepoints (incl. instruction-entry and refusal).
- `lint_profile.py --profile <file>` (`--self-test`) — the profile rules above.
- `linkml_validate.py --events <jsonl> | --profile <file>` — validate against the generated schemas.
- `bundle_preflight.py --bundle <dir> [--profile <file>]` — quarantines, per-source counts, trust-class
  consistency, dangling refs (read-only).
- `explain_insufficient.py --assertions <file> --support-matrices <adapters>` — explain and group.

## Assets

`assets/`: `applicability.template.yaml`, `domain.template.linkml.yaml`, `deviations.template.yaml`,
`chokepoints.template.md`, and `task-scope.template.yaml` (the Conduct overlay, SPEC §7.7). The
deviation register is written by the operator, never by this skill (S-3).

## Where to read next

`references/declare.md` → `references/instrument.md` → `references/validate-iterate.md`;
`references/trust-classes.md` and `references/annex-iii-classification.md` for the two judgement calls;
`references/frameworks/<style>.md` for framework-specific instrumentation notes.
