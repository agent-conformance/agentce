# `manifest.schema.json`

AgentCE reproducibility manifest.

manifest.json in the report directory (SPEC 8.4): the engine, the digests of every input, the digests of every output, and the run metadata. A verifier reruns the engine with the same inputs and compares output digests; equality proves reproducibility.

[View the schema](../../../spec/report/manifest.schema.json) (`https://agent-conformance.org/spec/report/manifest.schema.json`).

| Property | Type | Description |
|---|---|---|
| `agentce_manifest_version` | integer |  |
| `engine` | object |  |
| `inputs` | object |  |
| `outputs` | object | Output file name to its digest. |
| `run` | object |  |
| `source_completeness` | array | Per-source completeness (SPEC 5.4, Appendix F). |
| `supersedes` | array |  |
| `limitations` | array | What the run was told to use without being able to verify it, one string each (SPEC 8.7 --allow-unverified-catalog); absent when there was none. |
| `signature_ref` | string |  |
