# `project.schema.json`

AgentCE project view.

project.json in the report directory: every agent this run assessed, side by side -- one summary row per subject whether declared or discovered only from the records, the agents no profile declared, and the top blind spots across the whole project, each naming which agents it touches. Written only when a run assesses more than one subject; never affects an outcome or the verdict.

[View the schema](../../../spec/report/project.schema.json) (`https://agent-conformance.org/spec/report/project.schema.json`).

| Property | Type | Description |
|---|---|---|
| `agents` | array |  |
| `undeclared_agents` | array |  |
| `top_gaps` | array |  |
