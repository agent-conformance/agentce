# `Refs`

Typed references to other events; slot_uri gives the graph relation (SPEC 6.3). A ref names an event by its graph IRI, the event id prefixed with agentce:event/ (SPEC 3). It resolves only to a held event with that IRI. A ref in any other form, a bare event id included, names nothing held, and a control that needs the event it names fails closed. The one exception is decision: it also resolves its padded and bare-id forms (SPEC 10.4) to the held decision, because approval and oversight systems write their own ids and cannot write the engine's IRI.

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
