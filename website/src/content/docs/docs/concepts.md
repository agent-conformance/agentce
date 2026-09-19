---
title: What is agent conformance?
description: The mental model behind Agent Conformance — subjects, evidence, controls, and verdicts.
---

Agent conformance is the practice of checking how an AI-agent deployment behaves against a shared,
public specification, and producing evidence for the result that anyone can reproduce. It answers a
question that is otherwise hard to ask across vendors: does this agent meet the same bar as that one?

## The pieces

**The subject.** What is being assessed — an agent, a tool, an oversight process — identified in an
*applicability profile*. The profile is what you declare about the system: its role (provider or
deployer), the frameworks it uses, and which controls apply.

**Evidence.** Structured records of what the agent did: model calls, tool calls, decisions, oversight
actions, and the enforcement points around them. Evidence is referenced by content hash, never by
capturing prompts or outputs, so an assessment carries no sensitive payloads.

**Controls.** The rules an agent is measured against, grouped into families and published as a
catalog. The base catalog maps to the EU AI Act; overlays add domain and framework requirements. Each
control is executable: it names the evidence it needs and the shape that evidence must take.

**Verdicts.** For every control and subject, the engine emits one verdict — conformant,
non-conformant, partial, not applicable, not assessed, or insufficient evidence — and cites the
evidence behind it. "Insufficient evidence" is a first-class result: the engine reports what it
cannot verify rather than guessing.

**The bundle.** The directory of evidence an assessment reads: a `manifest.json` that lists every file
with its hash, and the `events/` streams the agent emitted. You pass it to `assess` with `--bundle`.

**The profile.** The applicability profile: what you declare about the subject — its role, its evidence
sources, and the catalogs to assess against. You pass it with `--profile`, and `agentce init` writes a
starter for you to complete.

**The layout.** Where a project keeps its files: the declarations in an `agentce/` directory
(`applicability.yaml` for the profile and `domain.linkml.yaml` for the domain binding) and the evidence
bundle in `evidence/`. `agentce init` writes the declarations, and `agentce doctor --project <dir>`
checks all of it.

## Why it is deterministic and model-free

No learned component takes part in a verdict. The engine reads evidence and evaluates explicit rules,
so the same inputs always produce the same output — on a different machine, in a different language
implementation, at a later date. That property is what makes a result comparable and a report
verifiable.

## Read next

- [The Conformance Spec](/docs/specification/) — how the model, catalog, and reports fit together.
- [Running Assessments](/docs/running-assessments/) — put the model to work from the command line.
