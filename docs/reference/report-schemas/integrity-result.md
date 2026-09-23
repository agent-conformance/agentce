# `integrity-result.schema.json`

AgentCE integrity result.

An IntegrityResult (SPEC 6.6, Appendix F): the verification outcome for one integrity stream, so a report consumer sees exactly which streams were verified, at what strength, and where the record breaks. IntegrityResult is also carried as an evidence event; this schema describes the standalone verification record.

[View the schema](../../../spec/report/integrity-result.schema.json) (`https://agent-conformance.org/spec/report/integrity-result.schema.json`).

| Property | Type | Description |
|---|---|---|
| `stream` | string | The integrity stream id, (source, subject). |
| `strength` | string |  |
| `status` | string |  |
| `first_bad_index` | integer | The index of the first event where the chain breaks, when status indicates a break. |
| `anchors` | array |  |
