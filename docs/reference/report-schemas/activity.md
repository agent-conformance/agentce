# `activity.schema.json`

AgentCE activity summary.

activity.json in the report directory: the counted facts a run's records show -- agents, models, tools, actions by effect class, approvals by who recorded them, actions denied or blocked, and tools/models the records show that the applicability profile never declared. Built only from the accepted events; never affects an outcome or the verdict.

[View the schema](../../../spec/report/activity.schema.json) (`https://agent-conformance.org/spec/report/activity.schema.json`).

| Property | Type | Description |
|---|---|---|
| `agents` | array |  |
| `models` | array |  |
| `tools` | array |  |
| `actions_by_effect_class` | object |  |
| `approvals_by_recorder` | object |  |
| `denied_or_blocked` | object |  |
| `undeclared` | object |  |
