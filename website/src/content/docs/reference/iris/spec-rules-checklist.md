---
title: AgentCE manual checklist
description: Served at its canonical IRI, /spec/rules/checklist.schema.json.
---

Served at its canonical IRI: `/spec/rules/checklist.schema.json` (`application/schema+json`).

A deterministic checklist for a manual or semi-automated control (SPEC 7.2, Appendix D). Each item names the artifact to inspect, a closed question, and the allowed answers. A completed checklist also records the assessor identity, the date, and the digests of the artifacts inspected; the engine treats a completed checklist as evidence of class independent_system.

A deterministic checklist for a manual or semi-automated control (SPEC 7.2, Appendix D). Each item names the artifact to inspect, a closed question, and the allowed answers. A completed checklist also records the assessor identity, the date, and the digests of the artifacts inspected; the engine treats a completed checklist as evidence of class independent_system.

[View the schema](https://agent-conformance.org/spec/rules/checklist.schema.json) (`https://agent-conformance.org/spec/rules/checklist.schema.json`).

| Property | Type | Description |
|---|---|---|
| `checklist` | string | Checklist identifier: the control id with the -M manual suffix (e.g. TRN-03-M). |
| `control` | string | The control this checklist evaluates. |
| `items` | array |  |
| `completed_by` | object | Present only on a completed checklist. |
| `completed_at` | string | RFC 3339 UTC timestamp of completion; present only on a completed checklist. |
| `artifact_digests` | object | Artifact path to its inspected digest (algorithm-prefixed). |

[View at its canonical IRI](https://agent-conformance.org/spec/rules/checklist.schema.json).
