# AgentCE probe specification

Served at its canonical IRI: `/spec/rules/probe.schema.json` (`application/schema+json`).

A rung-3 effectiveness evaluation that scores the results of a frozen probe corpus (SPEC 7.2). The engine never executes probes; it applies a deterministic oracle to results the corpus produced and computes an attack success rate with the exact interval of numerics.md.

A rung-3 effectiveness evaluation that scores the results of a frozen probe corpus (SPEC 7.2). The engine never executes probes; it applies a deterministic oracle to results the corpus produced and computes an attack success rate with the exact interval of numerics.md.

[View the schema](../../../spec/rules/probe.schema.json) (`https://agent-conformance.org/spec/rules/probe.schema.json`).

| Property | Type | Description |
|---|---|---|
| `id` | string | Stable probe identifier, kebab-case. |
| `corpus` | string | Frozen probe corpus reference, probes/<name>@<version>, with a manifest of prompt hashes and human review sign-off. |
| `oracle` | object | The deterministic per-case verdict rule. |
| `threshold` | object | Bound on the attack success rate (failed cases / cases). |
| `min_population` | integer | Below this the outcome is not_assessed with a reason code. |
| `outcome_map` | object |  |

[View at its canonical IRI](https://agent-conformance.org/spec/rules/probe.schema.json).
