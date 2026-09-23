# AgentCE control

Served at its canonical IRI: `/spec/rules/control.schema.json` (`application/schema+json`).

One control of a catalog, expressed as a single YAML document (SPEC 7.1). A control declares how it applies, the minimum evidence and source class it requires, its expectations, and the rung of the correctness ladder at which it is evaluated. Structural expectations reference a Portable Shape Profile shape (psp.md); statistical and effectiveness expectations embed a metric or probe specification.

One control of a catalog, expressed as a single YAML document (SPEC 7.1). A control declares how it applies, the minimum evidence and source class it requires, its expectations, and the rung of the correctness ladder at which it is evaluated. Structural expectations reference a Portable Shape Profile shape (psp.md); statistical and effectiveness expectations embed a metric or probe specification.

[View the schema](../../../spec/rules/control.schema.json) (`https://agent-conformance.org/spec/rules/control.schema.json`).

| Property | Type | Description |
|---|---|---|
| `id` | string | Control identifier: family prefix and two-digit number (e.g. OVS-03). |
| `version` | string | Catalog version this control belongs to, as YYYY.MM. |
| `title` | string |  |
| `description` | string |  |
| `crosswalk` | array | Mappings to external frameworks; clause references only, never reproduced text (SPEC 7.3). |
| `interpretations` | array | Interpretation register entries this control depends on (SPEC 6.10). |
| `applicability` | object |  |
| `evaluation` | object |  |
| `expectations` | array |  |
| `severity` | string |  |
| `evidence_strength` | string | strong = operating effectiveness; partial = design only. |
| `tolerance` | object | Population-level tolerance; defaults to zero for severity high (SPEC 7.1, 9.2). |
| `tolerance_rationale` | string |  |
| `false_positive_modes` | array |  |
| `techniques` | array | Informative implementation-pattern references; never change an outcome (DC-10). |
| `test_cases` | array |  |
| `remediation_hint` | string |  |
| `metric` | `metric.schema.json` |  |
| `probe` | `probe.schema.json` |  |
| `checklist` | `checklist.schema.json` |  |

[View at its canonical IRI](https://agent-conformance.org/spec/rules/control.schema.json).
