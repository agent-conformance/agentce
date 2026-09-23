---
title: AgentCE applicability profile
description: Served at its canonical IRI, /spec/model/applicability-profile.schema.json.
---

Served at its canonical IRI: `/spec/model/applicability-profile.schema.json` (`application/schema+json`).

The applicability profile (applicability.yaml) declares the subjects assessed and their risk context, selects catalogs and overlays, and declares evidence sources and coverage denominators (SPEC 6.5). It is a secondary input to assessment. Authored from the specification rather than emitted by the evidence-model generator; it is published here so consumers find every input schema under one root.

The applicability profile (applicability.yaml) declares the subjects assessed and their risk context, selects catalogs and overlays, and declares evidence sources and coverage denominators (SPEC 6.5). It is a secondary input to assessment. Authored from the specification rather than emitted by the evidence-model generator; it is published here so consumers find every input schema under one root.

[View the schema](https://agent-conformance.org/spec/model/applicability-profile.schema.json) (`https://agent-conformance.org/spec/model/applicability-profile.schema.json`).

| Property | Type | Description |
|---|---|---|
| `profile_version` | integer |  |
| `observation_window` | `window` |  |
| `content_capture` | object |  |
| `subjects` | array |  |
| `catalogs` | array |  |

[View at its canonical IRI](https://agent-conformance.org/spec/model/applicability-profile.schema.json).
