# `claim.schema.json`

AgentCE conformance claim.

The conformance claim object (SPEC 9.1): the scoped statement of what was assessed, by whom, against what catalogs, with what methods and evidence. It is content-addressed and signed by the claimant, never by the engine.

[View the schema](../../../spec/report/claim.schema.json) (`https://agent-conformance.org/spec/report/claim.schema.json`).

| Property | Type | Description |
|---|---|---|
| `claim_id` | string | Content address: the digest of the claim without its signatures. |
| `subjects` | array |  |
| `observation_window` | object |  |
| `catalogs` | array |  |
| `engine` | object |  |
| `evidence_sources` | array |  |
| `evaluation_methods` | object |  |
| `partial_conformance` | array |  |
| `deviations` | array |  |
| `limitations` | array |  |
| `claimant` | `party` |  |
| `assessor` | `party` |  |
| `statement` | string |  |
| `signatures` | array |  |
