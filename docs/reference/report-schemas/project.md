# `project.schema.json`

AgentCE project view.

project.json in the report directory. It has one summary row for each agent this run assessed, whether a profile declared it or only the records showed it, the agents no profile declared, and the top blind spots across the project, each naming the agents it touches. AgentCE writes it only when a run assesses more than one subject, and it never changes an outcome or the verdict.

[View the schema](../../../spec/report/project.schema.json) (`https://agent-conformance.org/spec/report/project.schema.json`).

| Property | Type | Description |
|---|---|---|
| `agents` | array |  |
| `undeclared_agents` | array |  |
| `top_gaps` | array |  |
