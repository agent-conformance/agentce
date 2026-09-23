# `applicability-statement.schema.json`

AgentCE applicability statement.

applicability-statement.json (SPEC Appendix F): which controls apply to a subject and why, the observed versus declared decision types, drift findings, and the trust-class justifications the applicability resolver recorded so an assessor can challenge them.

[View the schema](../../../spec/report/applicability-statement.schema.json) (`https://agent-conformance.org/spec/report/applicability-statement.schema.json`).

| Property | Type | Description |
|---|---|---|
| `subject` | string |  |
| `roles` | array |  |
| `catalogs` | array |  |
| `controls` | array |  |
| `observed_decision_types` | array |  |
| `declared_decision_types` | array |  |
| `drift` | array |  |
| `class_justifications` | array |  |
