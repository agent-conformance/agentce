# `results-sarif.schema.json`

AgentCE SARIF results (profile).

The subset of SARIF 2.1.0 that AgentCE emits as results.sarif (SPEC 9), so CI can gate on non-conformant controls with a format it already reads, GitHub code-scanning can anchor and de-dup findings, and every rule and result carries the fields a code-scanning consumer needs. This is a bounded profile; it conforms to the SARIF 2.1.0 structure for the fields it uses, and every document that satisfies it also validates against the vendored real OASIS SARIF 2.1.0 schema (spec/report/vendor/sarif-schema-2.1.0.json).

[View the schema](../../../spec/report/results-sarif.schema.json) (`https://agent-conformance.org/spec/report/results-sarif.schema.json`).

| Property | Type | Description |
|---|---|---|
| `$schema` | string |  |
| `version` | string |  |
| `runs` | array |  |
