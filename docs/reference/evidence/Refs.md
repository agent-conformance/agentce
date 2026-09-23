# `Refs`

Typed references to other event ids; slot_uri gives the graph relation (SPEC 6.3).

| Attribute | Range | Required | Description |
|---|---|---|---|
| `instruction` | string |  | The instruction this activity acts on. |
| `authorization` | string |  | The authorizing policy decision. |
| `delegation` | string |  | The delegation used. |
| `decision` | string |  | The decision this activity executes. |
| `request` | string |  | The action decided (on authority events). |
| `task` | string |  | Cross-agent task correlation. |
| `parent` | string |  | The instruction this was derived from. |
| `origin` | string |  | The activity that produced the content. |
| `guard` | string |  | The memory guard that ruled on a write. |
| `consumer` | string |  | The consumer of a memory read. |
| `policy_decision` | string |  | A policy decision associated with a refusal. |
| `executed_by` | string |  | The tool call that executed a decision. |
