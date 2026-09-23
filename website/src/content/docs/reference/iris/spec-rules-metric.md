---
title: AgentCE metric specification
description: Served at its canonical IRI, /spec/rules/metric.schema.json.
---

Served at its canonical IRI: `/spec/rules/metric.schema.json` (`application/schema+json`).

A declarative rung-3 statistical evaluation with fixed numerics (SPEC 7.2). Statistics are computed with exact arithmetic per numerics.md; no floating point enters an outcome.

A declarative rung-3 statistical evaluation with fixed numerics (SPEC 7.2). Statistics are computed with exact arithmetic per numerics.md; no floating point enters an outcome.

[View the schema](https://agent-conformance.org/spec/rules/metric.schema.json) (`https://agent-conformance.org/spec/rules/metric.schema.json`).

| Property | Type | Description |
|---|---|---|
| `id` | string | Stable metric identifier, kebab-case. |
| `population` | object | The events the metric is computed over. |
| `group_by` | array | Field paths that partition the population; the statistic is computed per group. |
| `statistic` | object |  |
| `threshold` | object |  |
| `min_population` | integer | Below this the outcome is not_assessed with a reason code. |
| `sampling` | object |  |
| `window` | string | The window the metric is computed over; observation_window unless otherwise declared. |
| `outcome_map` | object |  |

[View at its canonical IRI](https://agent-conformance.org/spec/rules/metric.schema.json).
