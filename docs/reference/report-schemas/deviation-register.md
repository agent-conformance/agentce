# `deviation-register.schema.json`

AgentCE deviation register.

deviations.yaml (SPEC 6.5): accepted or temporary departures from a control, each with a rationale, a compensating control, an owner, an approver, an expiry, and evidence references. A deviation yields the outcome partial, never conformant, and is validated further by deviation_lint (SPEC Appendix F); it is not a way to absorb operational noise (that is tolerance, SPEC 9.2).

[View the schema](../../../spec/report/deviation-register.schema.json) (`https://agent-conformance.org/spec/report/deviation-register.schema.json`).

| Property | Type | Description |
|---|---|---|
| `deviation_register_version` | integer |  |
| `deviations` | array |  |
