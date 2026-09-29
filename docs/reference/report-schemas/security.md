# `security.schema.json`

AgentCE security view.

security.json in the report directory. It shows this run's tool access, its actions by effect class, its enforcement-point evidence (approvals and denials), the tools and models your records show that no profile declared, and each baseline control's citations to OWASP agentic (ASI), MITRE ATLAS, and the OWASP Agent Control Standard. Every fact here is answered at the whole-run level: it names how many irreversible actions this run took next to how many approvals and denials it recorded, not which specific action a given check covers. AgentCE writes it only with `--for security`, alongside the existing SARIF, Markdown, and HTML files that preset has always written, and it never changes an outcome or the verdict.

[View the schema](../../../spec/report/security.schema.json) (`https://agent-conformance.org/spec/report/security.schema.json`).

| Property | Type | Description |
|---|---|---|
| `tool_access` | array |  |
| `actions_by_effect_class` | `counts` |  |
| `enforcement_point_evidence` | object |  |
| `drift` | object |  |
| `standards_citations` | array |  |
