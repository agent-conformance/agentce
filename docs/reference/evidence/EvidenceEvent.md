# `EvidenceEvent`

CloudEvent envelope carrying an AgentCE evidence payload (SPEC 6.2.1).

This is an abstract base class; it is never emitted directly.

| Attribute | Range | Required | Description |
|---|---|---|---|
| `specversion` | string | yes | CloudEvents version; MUST be 1.0. |
| `id` | string | yes | Globally unique event id. |
| `source` | string | yes | URI of the emitting system. |
| `type` | string | yes | org.agent-conformance.evidence.<EventType>.v1 |
| `time` | datetime | yes |  |
| `subject` | string | yes | The assessed subject system. |
| `datacontenttype` | string | yes | MUST be application/ld+json. |
| `agentcetrace` | string |  | W3C Trace Context trace id. |
| `agentcespan` | string |  | W3C Trace Context span id. |
| `agentceparent` | string |  | W3C Trace Context parent id. |
| `agentcetask` | string |  | Cross-agent task id. |
| `agentcesourceclass` | SourceClass | yes | Evidence trust class. |
| `agentceconv` | string |  | Upstream convention version mapped. |
| `data` | Payload | yes | JSON-LD payload. |
