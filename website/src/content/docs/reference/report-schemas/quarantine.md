---
title: AgentCE quarantine record
description: 'One record of quarantine.jsonl (SPEC Appendix F): an ingested item the engine refused, with the stable reason code. The file carries one such JSON obj'
---

AgentCE quarantine record.

One record of quarantine.jsonl (SPEC Appendix F): an ingested item the engine refused, with the stable reason code. The file carries one such JSON object per line; this schema validates a single record.

[View the schema](/spec/report/quarantine.schema.json) (`https://agent-conformance.org/spec/report/quarantine.schema.json`).

| Property | Type | Description |
|---|---|---|
| `reason` | string |  |
| `event_id` | string | The id of the offending event, when it has one. |
| `source` | string |  |
| `stream` | string |  |
| `type` | string | The declared event type, when known. |
| `detail` | string | Human-readable detail; the reason code is the stable part. |
