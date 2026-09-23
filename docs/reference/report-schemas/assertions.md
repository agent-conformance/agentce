# `assertions.schema.json`

AgentCE assertions.

assertions.json (SPEC 9.4): one machine-readable record per (control, subject), the outcome with its evidence pointers, violations, and population counts. The array is the source of truth from which report.md/html, OSCAL, and SARIF are rendered; a translation never changes it.

[View the schema](../../../spec/report/assertions.schema.json) (`https://agent-conformance.org/spec/report/assertions.schema.json`).

Each array element (`assertion`):

| Property | Type | Description |
|---|---|---|
| `control` | string |  |
| `control_version` | string |  |
| `subject` | string |  |
| `outcome` | `outcome` |  |
| `rung` | integer |  |
| `mode` | string |  |
| `severity` | string |  |
| `family` | string |  |
| `window` | object |  |
| `expectations` | array |  |
| `violations` | array |  |
| `evidence` | array |  |
| `population` | object |  |
| `source_class_satisfied` | boolean |  |
| `evidence_strength` | string |  |
| `deviation` | string or null |  |
| `remediation_hint_ref` | string |  |
| `crosswalk` | array |  |
