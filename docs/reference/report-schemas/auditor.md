# `auditor.schema.json`

AgentCE auditor view.

auditor.json in the report directory. It answers "can I rely on this, clause by clause?": one entry per (control, subject), full evidence trail, accepted deviations with their approvers, and, for a control this run couldn't evaluate yet, an honest not-yet-evaluated note rather than a fabricated result. `by_clause` groups the same entries' own control ids under every external framework clause they cite. This view adds no new fact and no new outcome; it is a different selection of the same assertions, and it is not itself a certification. AgentCE writes it only with `--for auditor`.

[View the schema](../../../spec/report/auditor.schema.json) (`https://agent-conformance.org/spec/report/auditor.schema.json`).

| Property | Type | Description |
|---|---|---|
| `clauses` | array |  |
| `by_clause` | object |  |
| `counts` | `counts` |  |
